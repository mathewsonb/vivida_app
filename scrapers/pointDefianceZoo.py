"""
pointDefianceZoo.py
Autonomous Scraper for Point Defiance Zoo & Aquarium (Tacoma, WA).
Uses Tribe REST API / HTTP-first listing with automatic Xvfb/Chrome hydration fallback,
concurrent detail-page fetching (workers=10), 4-tier hero image extraction,
and centralized parse_event_dates normalization.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
import glob
import logging
import re
import shutil
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

from bs4 import BeautifulSoup
import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from vivida_etl import (
    VividaIngestionPipeline,
    clean_html_source,
    fetch_event_text,
    parse_event_dates,
    create_stealth_driver
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(filename)s:%(lineno)d] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("scrape_pdza")

BASE_URL = "https://www.pdza.org"
TARGET_URL = "https://www.pdza.org/events/"
TRIBE_API_URL = "https://www.pdza.org/wp-json/tribe/events/v1/events"
DEFAULT_VENUE = "Point Defiance Zoo & Aquarium"
DEFAULT_ADDRESS = "5400 N Pearl St, Tacoma, WA 98407"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json;q=0.8,*/*;q=0.7",
    "Accept-Language": "en-US,en;q=0.9",
}


def normalize_ws(text: Optional[str]) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("\xa0", " ")).strip()


def resolve_url(raw_url: Optional[str]) -> Optional[str]:
    if not raw_url:
        return None
    clean = str(raw_url).strip()
    if not clean or clean.startswith("#") or clean.lower().startswith("javascript:"):
        return None
    if clean.startswith("//"):
        return f"https:{clean}"
    if clean.startswith("http://") or clean.startswith("https://"):
        return clean
    return f"{BASE_URL}{clean if clean.startswith('/') else '/' + clean}"


def build_http_session(pool_size: int = 12) -> requests.Session:
    session = requests.Session()
    session.headers.update(HEADERS)
    retries = Retry(
        total=2,
        backoff_factor=0.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    adapter = HTTPAdapter(
        max_retries=retries,
        pool_connections=pool_size,
        pool_maxsize=pool_size,
    )
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def strip_html_text(raw_html: Optional[str]) -> str:
    if not raw_html:
        return ""
    soup = BeautifulSoup(clean_html_source(raw_html), "html.parser")
    text = fetch_event_text(soup) or soup.get_text(" ", strip=True)
    return normalize_ws(text)


def normalize_event_group_key(detail_url: Optional[str], title: str) -> str:
    if detail_url:
        stripped = re.sub(r"/\d{4}-\d{2}-\d{2}/?$", "", detail_url.rstrip("/"))
        return stripped.lower()
    return title.lower()


def fetch_via_tribe_api(session: requests.Session, max_pages: int = 5) -> List[Dict[str, Any]]:
    """Fast-path: queries WordPress Tribe Events REST API if enabled on pdza.org."""
    grouped: Dict[str, Dict[str, Any]] = {}
    now_str = pd.Timestamp.now(
        tz="America/Los_Angeles").strftime("%Y-%m-%d 00:00:00")

    for page in range(1, max_pages + 1):
        resp = session.get(
            TRIBE_API_URL,
            params={"per_page": 50, "page": page, "start_date": now_str},
            timeout=10,
        )
        if resp.status_code != 200:
            break

        data = resp.json()
        events = data.get("events", []) if isinstance(data, dict) else []
        if not events:
            break

        for ev in events:
            title = strip_html_text(ev.get("title"))
            if not title:
                continue
            detail_url = resolve_url(ev.get("url"))

            raw_start = ev.get("start_date") or ""
            raw_end = ev.get("end_date") or ""
            dt_start = pd.to_datetime(raw_start, errors="coerce")
            dt_end = pd.to_datetime(raw_end, errors="coerce")
            if pd.isna(dt_start):
                start_times = parse_event_dates(raw_start)
            else:
                s_iso = dt_start.strftime("%Y-%m-%d")
                if pd.notnull(dt_end) and dt_end.date() > dt_start.date():
                    start_times = parse_event_dates(
                        f"{s_iso} - {dt_end.strftime('%Y-%m-%d')}"
                    )
                else:
                    start_times = parse_event_dates(s_iso)

            if not start_times:
                continue

            group_key = normalize_event_group_key(detail_url, title)
            if group_key in grouped:
                grouped[group_key]["dates_set"].update(start_times)
                continue

            short_desc = strip_html_text(ev.get("excerpt"))
            full_desc = strip_html_text(ev.get("description"))
            # img_obj = ev.get("image") if isinstance(
            #     ev.get("image"), dict) else {}

            grouped[group_key] = {
                "venue_name": DEFAULT_VENUE,
                "address": DEFAULT_ADDRESS,
                "title": title,
                "dates_set": set(start_times),
                "description": short_desc or full_desc[:250] or title,
                "big_desc": full_desc or short_desc or title,
                "external_url": detail_url,
                # resolve_url(img_obj.get("url")),
                "image_url": "https://www.pdza.org/wp-content/themes/pdza/dist/images/logo/logo-main.jpg",
                "ticket_link": resolve_url(ev.get("website")) or detail_url,
            }

        total_pages = int(data.get("total_pages") or 1)
        if page >= total_pages:
            break

    records: List[Dict[str, Any]] = []
    for entry in grouped.values():
        entry["start_time"] = sorted(entry.pop("dates_set"))
        records.append(entry)
    return records


def extract_pdza_image(soup: BeautifulSoup) -> Optional[str]:
    """Multi-tiered image extractor targeting hero divs, OpenGraph tags, and WP uploads."""
    if not soup:
        return None

    # Tier 0: Direct PDZA Hero Container Inspection (<div class="hero" style="background-image: ...">)
    hero_div = soup.find("div", class_=re.compile(r"hero", re.I)) or soup.find(
        lambda tag: tag.has_attr(
            "style") and "background-image" in tag["style"]
    )
    if hero_div and hero_div.get("style"):
        match = re.search(
            r'url\((?:["\']?)(.*?)(?:["\']?)\)', hero_div["style"], re.IGNORECASE
        )
        if match:
            resolved = resolve_url(match.group(1).strip())
            if resolved:
                return resolved

    # Tier 1: OpenGraph Meta Tag
    og_img = soup.find("meta", property=re.compile(r"og:image", re.I))
    if og_img and og_img.get("content"):
        resolved = resolve_url(og_img["content"].strip())
        if resolved:
            return resolved

    # Tier 2: Dedicated Event Banner / Featured Image Container
    featured_container = soup.find(
        "div",
        class_=re.compile(
            r"single-event-image|tribe-events-event-image|featured-image", re.I
        ),
    ) or soup.find("section", class_=re.compile(r"single-event", re.I))
    search_target = featured_container if featured_container else soup

    # Tier 3: Lazy-loaded or standard img tags inside the main container
    img_tag = (
        search_target.find("img", attrs={"data-src": True})
        or search_target.find("img", attrs={"data-lazy-src": True})
        or search_target.find("img", src=re.compile(r"wp-content/uploads", re.I))
    )
    if img_tag:
        src = (
            img_tag.get("data-src")
            or img_tag.get("data-lazy-src")
            or img_tag.get("src")
        )
        resolved = resolve_url(src)
        if resolved:
            return resolved

    return None


def find_listing_candidates(soup: BeautifulSoup) -> List[BeautifulSoup]:
    """Finds top-level PDZA event cards or fallback detail links from the calendar page."""
    for element in soup(["script", "style"]):
        element.decompose()

    event_items = soup.find_all(
        ["article", "div"],
        class_=re.compile(
            r"event-card|tribe-events-single|type-tribe_events", re.I),
    )
    if event_items:
        return event_items

    return soup.find_all("a", href=re.compile(r"/event/[^/?#]+", re.I))


def parse_detail_html(
    html: str,
    current_title: Optional[str],
    current_dates: List[str],
    detail_url: str,
) -> Tuple[Optional[str], List[str], str, str, Optional[str], Optional[str]]:
    """Extracts title, dates, rich description, summary, hero image, and ticket link."""
    det_soup = BeautifulSoup(clean_html_source(html), "html.parser")
    if not det_soup:
        fallback = current_title or ""
        return current_title, current_dates, fallback, fallback, None, detail_url

    title = current_title
    if not title:
        h1_tag = det_soup.find("h1")
        if h1_tag:
            title = normalize_ws(h1_tag.get_text(" ", strip=True))

    dates = current_dates
    if not dates:
        detail_date_el = det_soup.find(
            "span",
            class_=re.compile(
                r"tribe-event-date-start|date-start|event-date", re.I
            ),
        ) or det_soup.find("div", class_=re.compile(r"date|schedule", re.I))
        if detail_date_el:
            dates = parse_event_dates(
                normalize_ws(detail_date_el.get_text(" ", strip=True))
            )

    if not dates:
        body_text = normalize_ws(det_soup.get_text(" ", strip=True))
        dates = parse_event_dates(body_text)

    section = (
        det_soup.find("section", class_=re.compile(
            r"single-event|content", re.I))
        or det_soup.find("main")
        or det_soup
    )

    ticket_link = detail_url
    t_btn = section.find(
        ["a", "button"],
        href=re.compile(r"ticket|buy|admission|register|etix", re.I),
    )
    if t_btn and t_btn.get("href"):
        ticket_link = resolve_url(t_btn.get("href")) or detail_url

    rich_text = fetch_event_text(section)
    if rich_text:
        big_desc = normalize_ws(rich_text)
        description = big_desc[:250]
    else:
        big_desc = title or ""
        description = title or ""

    image_url = extract_pdza_image(det_soup)

    return title, dates, description, big_desc, image_url, ticket_link


def fetch_detail_via_http(
    session: requests.Session, item: Dict[str, Any]
) -> Tuple[Dict[str, Any], bool]:
    detail_url = item.get("external_url")
    if not detail_url:
        return item, True

    try:
        resp = session.get(detail_url, timeout=15)
        resp.raise_for_status()
        if len(resp.text) < 500:
            return item, False

        title, dates, description, big_desc, image_url, ticket_link = parse_detail_html(
            resp.text,
            item.get("title"),
            item["start_time"],
            detail_url,
        )
        item["title"] = title
        item["start_time"] = dates
        item["description"] = description
        item["big_desc"] = big_desc
        item["image_url"] = image_url or item.get("image_url")
        item["ticket_link"] = ticket_link
        return item, True
    except Exception:
        return item, False


def scraper() -> pd.DataFrame:
    """Scrapes PDZA events via API / HTTP-first with automatic Chrome hydration fallback."""
    logger.info(
        f"[START] Scraping Point Defiance Zoo & Aquarium: {TARGET_URL}")
    session = build_http_session(pool_size=12)

    # 1. Probe Tribe REST API first
    try:
        api_records = fetch_via_tribe_api(session)
        if api_records:
            logger.info(
                f"[TRIBE API SUCCESS] Retrieved {len(api_records)} unique PDZA events via REST API."
            )
            return pd.DataFrame(api_records)
    except Exception:
        pass

    display = None
    driver = None
    event_items: List[BeautifulSoup] = []

    def ensure_browser():
        nonlocal display, driver
        if driver is None:
            from pyvirtualdisplay import Display

            display = Display(visible=0, size=(1920, 1080), backend="xvfb")
            display.start()
            driver = create_stealth_driver()
        return driver

    try:
        # 2. HTTP-first listing fetch
        try:
            resp = session.get(TARGET_URL, timeout=15)
            resp.raise_for_status()
            soup_http = BeautifulSoup(
                clean_html_source(resp.text), "html.parser")
            candidates = find_listing_candidates(soup_http)
            if candidates:
                event_items = candidates
                logger.info(
                    f"[HTTP FAST-PATH] Loaded PDZA calendar via HTTP ({len(event_items)} candidates)."
                )
            else:
                logger.info(
                    "[HTTP LISTING] Page shell loaded with 0 static event cards; launching Xvfb + Chrome..."
                )
        except Exception as http_err:
            logger.warning(
                f"[HTTP LISTING] Failed ({http_err}); launching Xvfb + Chrome fallback..."
            )

        if not event_items:
            from selenium.webdriver.common.by import By
            from selenium.webdriver.support import expected_conditions as EC
            from selenium.webdriver.support.ui import WebDriverWait

            drv = ensure_browser()
            drv.get(TARGET_URL)
            try:
                WebDriverWait(drv, 10).until(
                    EC.presence_of_element_located(
                        (
                            By.CSS_SELECTOR,
                            "article.event, div.event-card, .tribe-events-list",
                        )
                    )
                )
            except Exception:
                pass
            time.sleep(2)

            soup_browser = BeautifulSoup(
                clean_html_source(drv.page_source), "html.parser")
            event_items = find_listing_candidates(soup_browser)

            ua = drv.execute_script("return navigator.userAgent;")
            if ua:
                session.headers.update({"User-Agent": ua})
            for cookie in drv.get_cookies():
                session.cookies.set(
                    cookie["name"],
                    cookie["value"],
                    domain=cookie.get("domain"),
                    path=cookie.get("path", "/"),
                )

        if not event_items:
            logger.warning("[PARSER] No PDZA event candidates found.")
            return pd.DataFrame()

        # 3. Deduplicate detail URLs and pre-extract card metadata
        staged_map: Dict[str, Dict[str, Any]] = {}
        for item in event_items:
            link = item.get("href") if item.name == "a" else None
            if not link:
                a_tag = item.find("a", href=re.compile(r"/event/", re.I))
                if a_tag:
                    link = a_tag.get("href")

            full_url = resolve_url(link)
            if not full_url or full_url.rstrip("/") == TARGET_URL.rstrip("/"):
                continue

            card_title: Optional[str] = None
            card_dates: List[str] = []
            if item.name != "a":
                title_tag = item.find(
                    ["h2", "h3", "h4", "a"], class_=re.compile(r"title", re.I)
                )
                if title_tag:
                    card_title = normalize_ws(
                        title_tag.get_text(" ", strip=True))

                date_tag = item.find(
                    ["div", "span", "p"], class_=re.compile(r"date|time", re.I)
                )
                if date_tag:
                    card_dates = parse_event_dates(
                        normalize_ws(date_tag.get_text(" ", strip=True))
                    )

            group_key = normalize_event_group_key(
                full_url, card_title or full_url)
            if group_key in staged_map:
                staged_map[group_key]["dates_set"].update(card_dates)
                continue

            staged_map[group_key] = {
                "venue_name": DEFAULT_VENUE,
                "address": DEFAULT_ADDRESS,
                "title": card_title,
                "dates_set": set(card_dates),
                "description": None,
                "big_desc": None,
                "external_url": full_url,
                "image_url": extract_pdza_image(item) if item.name != "a" else None,
                "ticket_link": full_url,
            }

        if not staged_map:
            return pd.DataFrame()

        staged_items: List[Dict[str, Any]] = []
        for entry in staged_map.values():
            entry["start_time"] = sorted(entry.pop("dates_set"))
            staged_items.append(entry)

        logger.info(
            f"Batch-fetching {len(staged_items)} PDZA detail pages via HTTP (workers=10)..."
        )
        enriched_items: List[Dict[str, Any]] = []
        fallback_queue: List[Dict[str, Any]] = []

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [
                executor.submit(fetch_detail_via_http, session, item)
                for item in staged_items
            ]
            for fut in as_completed(futures):
                updated_item, ok = fut.result()
                if ok:
                    enriched_items.append(updated_item)
                else:
                    fallback_queue.append(updated_item)

        logger.info(
            f"HTTP detail batch complete: {len(enriched_items)} parsed, "
            f"{len(fallback_queue)} queued for Chrome fallback."
        )

        if fallback_queue:
            drv = ensure_browser()
            for item in fallback_queue:
                detail_url = item.get("external_url")
                if detail_url:
                    try:
                        drv.get(detail_url)
                        time.sleep(2)
                        title, dates, description, big_desc, image_url, ticket_link = parse_detail_html(
                            drv.page_source,
                            item.get("title"),
                            item["start_time"],
                            detail_url,
                        )
                        item["title"] = title
                        item["start_time"] = dates
                        item["description"] = description
                        item["big_desc"] = big_desc
                        item["image_url"] = image_url or item.get("image_url")
                        item["ticket_link"] = ticket_link
                    except Exception as fb_err:
                        logger.warning(
                            f"[FALLBACK WARN] Failed {detail_url}: {fb_err}")
                enriched_items.append(item)

        raw_records = []
        for item in enriched_items:
            if not item.get("title") or not item.get("start_time"):
                logger.warning(
                    f"[SKIP] Missing title or valid dates for '{item.get('external_url')}'."
                )
                continue
            raw_records.append(item)

        return pd.DataFrame(raw_records) if raw_records else pd.DataFrame()

    except Exception as e:
        logger.error(
            f"[FATAL ERROR] Exception encountered during PDZA scraping loop: {e}", exc_info=True)
        return pd.DataFrame()
    finally:
        if driver:
            logger.info("[CLEANUP] Closing Chrome driver instance...")
            try:
                driver.quit()
            except Exception:
                pass
        if display:
            logger.info("[CLEANUP] Stopping PyVirtualDisplay server...")
            try:
                display.stop()
            except Exception:
                pass


if __name__ == "__main__":
    logger.info("Executing Point Defiance Zoo & Aquarium scraper task...")
    raw_df = scraper()

    if not raw_df.empty:
        pipeline = VividaIngestionPipeline(table_name="events")
        success = pipeline.run_standalone_pipeline(raw_df)

        if success:
            logger.info(
                "[PIPELINE SUCCESS] PDZA scraper pipeline execution completed successfully.")
        else:
            logger.error(
                "[PIPELINE ERROR] PDZA scraper pipeline execution reported errors.")
    else:
        logger.warning("[EXIT] No events scraped. Exiting task.")
