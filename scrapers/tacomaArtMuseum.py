"""
tacomaArtMuseum.py
Autonomous Scraper for Tacoma Art Museum (Tacoma, WA).
Uses Tribe REST API / HTTP-first listing with automatic cross-platform browser hydration fallback
when cards render client-side, concurrent detail-page fetching (workers=10),
and centralized parse_event_dates normalization.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
import logging
import re
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
    create_stealth_driver,
    fetch_event_text,
    parse_event_dates,
    virtual_display,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(filename)s:%(lineno)d] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("scrape_tam")

BASE_URL = "https://www.tacomaartmuseum.org"
TARGET_URL = "https://www.tacomaartmuseum.org/events/"
TRIBE_API_URL = "https://www.tacomaartmuseum.org/wp-json/tribe/events/v1/events"
DEFAULT_VENUE = "Tacoma Art Museum"
DEFAULT_ADDRESS = "1701 Pacific Avenue, Tacoma, WA 98402"

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


def normalize_tribe_group_key(detail_url: Optional[str], title: str) -> str:
    """Groups recurring Tribe event occurrences (e.g. .../slug/2026-11-19/) under one canonical key."""
    if detail_url:
        stripped = re.sub(r"/\d{4}-\d{2}-\d{2}/?$", "", detail_url.rstrip("/"))
        return stripped.lower()
    return title.lower()


def fetch_via_tribe_api(
    session: requests.Session, max_pages: int = 6
) -> List[Dict[str, Any]]:
    """Fast-path: queries WordPress Tribe Events REST API and merges recurring dates per event."""
    grouped: Dict[str, Dict[str, Any]] = {}
    now_str = pd.Timestamp.now(tz="America/Los_Angeles").strftime(
        "%Y-%m-%d 00:00:00"
    )

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

            group_key = normalize_tribe_group_key(detail_url, title)
            if group_key in grouped:
                grouped[group_key]["dates_set"].update(start_times)
                continue

            short_desc = strip_html_text(ev.get("excerpt"))
            full_desc = strip_html_text(ev.get("description"))
            img_obj = ev.get("image") if isinstance(ev.get("image"), dict) else {}

            grouped[group_key] = {
                "venue_name": DEFAULT_VENUE,
                "address": DEFAULT_ADDRESS,
                "title": title,
                "dates_set": set(start_times),
                "description": short_desc or full_desc or title,
                "big_desc": full_desc or short_desc or title,
                "external_url": detail_url,
                "image_url": resolve_url(img_obj.get("url")),
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


def extract_tam_dates(container: BeautifulSoup) -> List[str]:
    """Extracts single or multi-day dates from TAM listing/detail date elements."""
    if not container:
        return []

    for selector in [
        container.find(
            "div", class_=re.compile(r"num|date|schedule|tribe-event-schedule", re.I)
        ),
        container.find("span", class_=re.compile(r"date|time", re.I)),
        container.find("time"),
    ]:
        if selector:
            raw_dt = selector.get("datetime") or selector.get_text(" ", strip=True)
            dates = parse_event_dates(normalize_ws(raw_dt))
            if dates:
                return dates

    # Fallback: scan full card text for date patterns
    card_text = normalize_ws(container.get_text(" ", strip=True))
    if card_text:
        return parse_event_dates(card_text)
    return []


def find_event_candidates(soup: BeautifulSoup) -> List[BeautifulSoup]:
    """Locates TAM event cards across Tribe, custom TAM main_column, or event link structures."""
    target_section = soup.find("div", class_="main_column") or soup.find("main") or soup
    for element in target_section(["script", "style"]):
        element.decompose()

    items = target_section.find_all(
        ["li", "article", "div"],
        class_=re.compile(r"event|tribe-events-item|type-tribe_events", re.I),
    )
    if items:
        return items

    # Custom TAM layout: <li> inside div.main_column or ul.events-list
    main_col = soup.find("div", class_="main_column") or soup.find(
        "ul", class_=re.compile(r"events", re.I)
    )
    if main_col:
        li_items = main_col.find_all("li")
        if li_items:
            return li_items

    # Fallback: anchors pointing to individual event detail pages
    event_links = []
    for a in target_section.find_all("a", href=True):
        href = a["href"].strip()
        if re.search(r"/events?/[^/?#]+", href, re.I) and not href.rstrip("/").endswith(
            "/events"
        ):
            parent = a.find_parent(["li", "article", "div"]) or a
            if parent not in event_links:
                event_links.append(parent)
    return event_links


def parse_detail_html(
    html: str,
    current_dates: List[str],
    card_title: str,
    current_image: Optional[str],
    detail_url: str,
) -> Tuple[List[str], str, str, Optional[str], Optional[str]]:
    """Extracts fallback dates, rich description text, summary, image, and ticket link."""
    det_soup = BeautifulSoup(clean_html_source(html), "html.parser")
    if not det_soup:
        return current_dates, card_title, card_title, current_image, detail_url

    dates = current_dates or extract_tam_dates(det_soup)

    container = (
        det_soup.find(
            "div", class_=re.compile(r"rtecontent|tribe-events-content|entry-content", re.I)
        )
        or det_soup.find("main")
        or det_soup
    )

    rich_text = fetch_event_text(container)
    big_desc = normalize_ws(rich_text) if rich_text else card_title

    h3_tag = container.find("h3")
    if h3_tag and normalize_ws(h3_tag.get_text(" ", strip=True)):
        description = normalize_ws(h3_tag.get_text(" ", strip=True))
    else:
        p_tag = container.find("p")
        if p_tag and normalize_ws(p_tag.get_text(" ", strip=True)):
            description = normalize_ws(p_tag.get_text(" ", strip=True))
        else:
            description = big_desc[:250] if big_desc else card_title

    image_url = current_image
    if not image_url:
        det_img = container.find("img", src=True) or det_soup.find(
            "meta", property="og:image"
        )
        if det_img:
            image_url = resolve_url(det_img.get("src") or det_img.get("content"))

    ticket_link = detail_url
    ticket_anchor = det_soup.find(
        "a", href=re.compile(r"ticket|register|rsvp|buy", re.I)
    )
    if ticket_anchor and ticket_anchor.get("href"):
        ticket_link = resolve_url(ticket_anchor["href"]) or detail_url

    return dates, description, big_desc, image_url, ticket_link


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

        dates, description, big_desc, image_url, ticket_link = parse_detail_html(
            resp.text,
            item["start_time"],
            item["title"],
            item.get("image_url"),
            detail_url,
        )
        item["start_time"] = dates
        item["description"] = description
        item["big_desc"] = big_desc
        item["image_url"] = image_url
        item["ticket_link"] = ticket_link
        return item, True
    except Exception:
        return item, False


def scraper() -> pd.DataFrame:
    """Scrapes Tacoma Art Museum events via API / HTTP-first with automatic browser hydration fallback."""
    logger.info(f"[START] Scraping Tacoma Art Museum: {TARGET_URL}")
    session = build_http_session(pool_size=12)

    # 1. Probe Tribe REST API first
    try:
        api_records = fetch_via_tribe_api(session)
        if api_records:
            logger.info(
                f"[TRIBE API SUCCESS] Retrieved {len(api_records)} unique TAM events via REST API."
            )
            return pd.DataFrame(api_records)
    except Exception:
        pass

    disp_ctx = None
    active_disp = None
    driver = None
    event_items: List[BeautifulSoup] = []

    def ensure_browser():
        nonlocal disp_ctx, active_disp, driver
        if disp_ctx is None:
            disp_ctx = virtual_display(size=(1920, 1080))
            active_disp = disp_ctx.__enter__()
        if driver is None:
            driver = create_stealth_driver(headless=(active_disp is None))
        return driver

    try:
        # 2. HTTP-first listing fetch (verify hydrated cards exist before accepting)
        try:
            resp = session.get(TARGET_URL, timeout=15)
            resp.raise_for_status()
            soup_http = BeautifulSoup(clean_html_source(resp.text), "html.parser")
            candidates = find_event_candidates(soup_http)
            if candidates:
                event_items = candidates
                logger.info(
                    f"[HTTP FAST-PATH] Loaded Tacoma Art Museum events listing via HTTP ({len(event_items)} candidates)."
                )
            else:
                logger.info(
                    "[HTTP LISTING] Page shell loaded with 0 static event cards; launching browser for JS hydration..."
                )
        except Exception as http_err:
            logger.warning(
                f"[HTTP LISTING] Failed ({http_err}); launching browser fallback..."
            )

        if not event_items:
            from selenium.webdriver.common.by import By
            from selenium.webdriver.support import expected_conditions as EC
            from selenium.webdriver.support.ui import WebDriverWait

            drv = ensure_browser()
            drv.get(TARGET_URL)
            try:
                WebDriverWait(drv, 12).until(
                    EC.presence_of_element_located(
                        (
                            By.CSS_SELECTOR,
                            "div.main_column, ul.events-list, .tribe-events-list, article",
                        )
                    )
                )
            except Exception:
                pass
            time.sleep(3)
            drv.execute_script("window.scrollTo(0, document.body.scrollHeight / 2);")
            time.sleep(2)

            soup_browser = BeautifulSoup(
                clean_html_source(drv.page_source), "html.parser"
            )
            event_items = find_event_candidates(soup_browser)

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

        logger.info(
            f"[PARSER SUCCESS] Found {len(event_items)} TAM candidate event list items."
        )
        if not event_items:
            return pd.DataFrame()

        grouped_items: Dict[str, Dict[str, Any]] = {}

        for idx, event in enumerate(event_items, start=1):
            try:
                anchor = event.find("a", href=True)
                if not anchor:
                    continue

                detail_url = resolve_url(anchor.get("href"))
                if not detail_url or detail_url.rstrip("/") == TARGET_URL.rstrip("/"):
                    continue

                deets_div = anchor.find(
                    "div", class_=re.compile(r"deets|title|heading", re.I)
                ) or event.find(["h3", "h2", "h4"])
                title = (
                    normalize_ws(deets_div.get_text(" ", strip=True))
                    if deets_div
                    else normalize_ws(anchor.get_text(" ", strip=True))
                )
                if not title or len(title) < 3:
                    continue

                start_time_list = extract_tam_dates(event)
                dedup_key = normalize_tribe_group_key(detail_url, title)

                if dedup_key in grouped_items:
                    grouped_items[dedup_key]["dates_set"].update(start_time_list)
                    continue

                img_tag = event.find("img", src=True) or event.find(
                    "img", attrs={"data-src": True}
                )
                image_url = (
                    resolve_url(img_tag.get("data-src") or img_tag.get("src"))
                    if img_tag
                    else None
                )

                grouped_items[dedup_key] = {
                    "venue_name": DEFAULT_VENUE,
                    "address": DEFAULT_ADDRESS,
                    "title": title,
                    "dates_set": set(start_time_list),
                    "description": title,
                    "big_desc": title,
                    "external_url": detail_url,
                    "image_url": image_url,
                    "ticket_link": detail_url,
                }
            except Exception as item_err:
                logger.error(
                    f"[ITEM ERROR] Failed parsing TAM event #{idx}: {item_err}",
                    exc_info=True,
                )

        if not grouped_items:
            return pd.DataFrame()

        staged_items: List[Dict[str, Any]] = []
        for entry in grouped_items.values():
            entry["start_time"] = sorted(entry.pop("dates_set"))
            staged_items.append(entry)

        logger.info(
            f"Batch-fetching {len(staged_items)} TAM detail pages via HTTP (workers=10)..."
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
            f"{len(fallback_queue)} queued for browser fallback."
        )

        if fallback_queue:
            drv = ensure_browser()
            for item in fallback_queue:
                detail_url = item.get("external_url")
                if detail_url:
                    try:
                        drv.get(detail_url)
                        time.sleep(2.5)
                        dates, description, big_desc, image_url, ticket_link = (
                            parse_detail_html(
                                drv.page_source,
                                item["start_time"],
                                item["title"],
                                item.get("image_url"),
                                detail_url,
                            )
                        )
                        item["start_time"] = dates
                        item["description"] = description
                        item["big_desc"] = big_desc
                        item["image_url"] = image_url
                        item["ticket_link"] = ticket_link
                    except Exception as fb_err:
                        logger.warning(f"[FALLBACK WARN] Failed {detail_url}: {fb_err}")
                enriched_items.append(item)

        raw_records = []
        for item in enriched_items:
            if not item.get("start_time"):
                logger.warning(
                    f"[SKIP] No valid dates parsed for '{item.get('title')}'."
                )
                continue
            raw_records.append(item)

        return pd.DataFrame(raw_records) if raw_records else pd.DataFrame()

    except Exception as e:
        logger.error(
            f"[FATAL ERROR] Error during TAM scraping loop: {e}", exc_info=True
        )
        return pd.DataFrame()
    finally:
        if driver:
            logger.info("[CLEANUP] Closing Chrome driver instance...")
            try:
                driver.quit()
            except Exception:
                pass
        if disp_ctx is not None:
            logger.info("[CLEANUP] Stopping virtual display context...")
            try:
                disp_ctx.__exit__(None, None, None)
            except Exception:
                pass


if __name__ == "__main__":
    logger.info("Executing Tacoma Art Museum scraper task...")
    raw_df = scraper()

    if not raw_df.empty:
        pipeline = VividaIngestionPipeline(table_name="events")
        success = pipeline.run_standalone_pipeline(raw_df)
        if success:
            logger.info(
                "[PIPELINE SUCCESS] TAM scraper execution completed successfully."
            )
        else:
            logger.error(
                "[PIPELINE ERROR] TAM scraper pipeline execution reported errors."
            )
    else:
        logger.warning("[EXIT] No events scraped. Pipeline handoff skipped.")