"""
federalWayPerformingArtsCenter.py
Autonomous Production Scraper for Federal Way Performing Arts and Event Center (FWPAEC).
Uses HTTP-first MEC calendar listing + concurrent detail fetching (workers=10)
with lazy Xvfb/Chrome hydration fallback and centralized parse_event_dates normalization.
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
logger = logging.getLogger("scrape_fwpaec")

BASE_URL = "https://fwpaec.org"
TARGET_URL = "https://fwpaec.org/event-calendar/"
DEFAULT_VENUE = "Federal Way Performing Arts and Event Center"
DEFAULT_ADDRESS = "31510 Pete von Reichbauer Way S, Federal Way, WA 98003"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def normalize_ws(text: Optional[str]) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("\xa0", " ")).strip()


def resolve_url(raw_url: Optional[str]) -> Optional[str]:
    if not raw_url:
        return None
    clean = str(raw_url).strip()
    if (
        not clean
        or clean.startswith("#")
        or clean.lower().startswith("javascript:")
        or clean.lower().startswith("data:")
    ):
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


def normalize_mec_group_key(detail_url: Optional[str], title: str) -> str:
    if detail_url:
        # Strip occurrence query params (?occurrence=2026-12-05) so multi-show runs consolidate
        base = detail_url.split("?")[0].rstrip("/")
        return base.lower()
    return title.lower()


def parse_detail_page_html(
    html_content: str,
    card_title: Optional[str],
    card_date_str: Optional[str],
    card_image: Optional[str],
    detail_url: str,
) -> Dict[str, Optional[Any]]:
    """Extracts detail parameters from an MEC single event page using verified DOM locators."""
    soup = BeautifulSoup(clean_html_source(html_content), "html.parser")
    container = soup.find("section", class_="mec-container", id="main-content")
    root = container if container else soup

    title_el = root.find("h1", class_="mec-single-title")
    title = normalize_ws(title_el.get_text(" ", strip=True)
                         ) if title_el else (card_title or "")

    # Extract start & optional end date labels (MEC often includes the year already)
    start_el = root.find("span", class_="mec-start-date-label")
    end_el = root.find("span", class_="mec-end-date-label")
    dates: List[str] = []

    if start_el:
        s_txt = normalize_ws(start_el.get_text(" ", strip=True))
        e_txt = normalize_ws(end_el.get_text(" ", strip=True)).lstrip(
            "-–— ") if end_el else ""
        expr = f"{s_txt} - {e_txt}" if e_txt and e_txt != s_txt else s_txt
        dates = parse_event_dates(expr)

    if not dates and card_date_str:
        dates = parse_event_dates(card_date_str)

    # Occurrence query param fallback (?occurrence=YYYY-MM-DD)
    if not dates:
        occ_match = re.search(r"occurrence=(\d{4}-\d{2}-\d{2})", detail_url)
        if occ_match:
            dates = parse_event_dates(occ_match.group(1))

    # Registration/Ticket Link
    ticket_link = detail_url
    reg_btn = root.find("a", class_=re.compile(r"mec-booking-button", re.I))
    if reg_btn and reg_btn.get("href"):
        ticket_link = resolve_url(reg_btn["href"]) or detail_url

    # Image fallback from detail hero / og:image
    image_url = card_image
    if not image_url:
        og_img = soup.find("meta", property="og:image")
        if og_img and og_img.get("content"):
            image_url = resolve_url(og_img["content"])
    if not image_url:
        hero_img = root.find("div", class_=re.compile(
            r"mec-events-event-image", re.I))
        if hero_img:
            img_tag = hero_img.find("img")
            if img_tag:
                image_url = resolve_url(
                    img_tag.get("data-src") or img_tag.get("src")
                )

    # Description Locators
    desc_el = root.find("div", class_=re.compile(
        r"mec-single-event-description", re.I))
    short_desc: Optional[str] = None
    big_desc: Optional[str] = None

    if desc_el:
        rich_text = fetch_event_text(desc_el)
        big_desc = normalize_ws(rich_text) if rich_text else normalize_ws(
            desc_el.get_text(" ", strip=True))
        p_tags = desc_el.find_all("p")
        for p in p_tags:
            txt = normalize_ws(p.get_text(" ", strip=True))
            if txt:
                short_desc = txt
                break
        if not short_desc and big_desc:
            short_desc = big_desc[:250]

    return {
        "title": title,
        "start_time": dates,
        "description": short_desc or title,
        "big_desc": big_desc or short_desc or title,
        "image_url": image_url,
        "ticket_link": ticket_link,
    }


def fetch_detail_via_http(
    session: requests.Session, item: Dict[str, Any]
) -> Tuple[Dict[str, Any], bool]:
    detail_url = item["external_url"]
    try:
        resp = session.get(detail_url, timeout=15)
        resp.raise_for_status()
        if len(resp.text) < 500:
            return item, False

        parsed = parse_detail_page_html(
            resp.text,
            item.get("title"),
            item.get("_card_date_str"),
            item.get("image_url"),
            detail_url,
        )
        item["title"] = parsed["title"] or item["title"]
        if parsed["start_time"]:
            item["start_time"] = parsed["start_time"]
        item["description"] = parsed["description"]
        item["big_desc"] = parsed["big_desc"]
        item["image_url"] = parsed["image_url"]
        item["ticket_link"] = parsed["ticket_link"]
        return item, True
    except Exception:
        return item, False


def scraper() -> pd.DataFrame:
    """Scrapes FWPAEC MEC events via HTTP-first listing + concurrent detail fetching."""
    logger.info(f"[START] Scraping Federal Way PAEC: {TARGET_URL}")
    session = build_http_session(pool_size=12)

    display = None
    driver = None
    event_articles: List[BeautifulSoup] = []

    def ensure_browser():
        nonlocal display, driver
        if driver is None:
            from pyvirtualdisplay import Display

            display = Display(visible=0, size=(1920, 1080), backend="xvfb")
            display.start()
            driver = create_stealth_driver()
        return driver

    try:
        # 1. HTTP-first main calendar fetch
        try:
            resp = session.get(TARGET_URL, timeout=15)
            resp.raise_for_status()
            soup_http = BeautifulSoup(
                clean_html_source(resp.text), "html.parser")
            candidates = soup_http.find_all(
                "article", class_=re.compile(r"mec-event-article", re.I)
            )
            if candidates:
                event_articles = candidates
                logger.info(
                    f"[HTTP FAST-PATH] Loaded FWPAEC calendar via HTTP ({len(event_articles)} cards)."
                )
            else:
                logger.info(
                    "[HTTP LISTING] Page shell loaded with 0 static event articles; launching Xvfb + Chrome..."
                )
        except Exception as http_err:
            logger.warning(
                f"[HTTP LISTING] Failed ({http_err}); launching Xvfb + Chrome fallback..."
            )

        if not event_articles:
            drv = ensure_browser()
            drv.get(TARGET_URL)
            time.sleep(4)
            drv.execute_script(
                "window.scrollTo(0, document.body.scrollHeight / 2);")
            time.sleep(1.5)

            soup_browser = BeautifulSoup(
                clean_html_source(drv.page_source), "html.parser")
            event_articles = soup_browser.find_all(
                "article", class_=re.compile(r"mec-event-article", re.I)
            )

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

        if not event_articles:
            logger.warning(
                "[PARSER WARNING] Found 0 articles matching class 'mec-event-article'.")
            return pd.DataFrame()

        logger.info(
            f"[PARSER SUCCESS] Found {len(event_articles)} MEC event cards. Batch-fetching details (workers=10)..."
        )

        # 2. Extract card metadata
        staged_items: List[Dict[str, Any]] = []
        for idx, article in enumerate(event_articles, start=1):
            title_el = article.find("h4", class_="mec-event-title")
            link_tag = title_el.find("a", href=True) if title_el else None

            card_title = normalize_ws(link_tag.get_text(
                " ", strip=True)) if link_tag else ""
            detail_url = resolve_url(
                link_tag.get("href")) if link_tag else None

            if not detail_url:
                booking_btn = article.find("a", class_=re.compile(
                    r"mec-booking-button", re.I), href=True)
                if booking_btn:
                    detail_url = resolve_url(booking_btn.get("href"))

            if not detail_url:
                logger.warning(
                    f"[ITEM SKIP] Event {idx}: No detail URL found.")
                continue

            month_el = article.find("div", class_="mec-event-month")
            card_date_str = normalize_ws(month_el.get_text(
                " ", strip=True)) if month_el else None

            venue_el = article.find("div", class_="mec-event-loc-place")
            card_venue = (
                normalize_ws(venue_el.get_text(" ", strip=True))
                if venue_el
                else DEFAULT_VENUE
            ) or DEFAULT_VENUE

            addr_el = article.find("p", class_="mec-grid-event-location")
            card_address = (
                normalize_ws(addr_el.get_text(" ", strip=True))
                if addr_el
                else DEFAULT_ADDRESS
            ) or DEFAULT_ADDRESS

            img_tag = article.find("img")
            image_url = (
                resolve_url(img_tag.get("data-src") or img_tag.get("src"))
                if img_tag
                else None
            )

            staged_items.append(
                {
                    "venue_name": card_venue,
                    "address": card_address,
                    "title": card_title,
                    "start_time": parse_event_dates(card_date_str) if card_date_str else [],
                    "_card_date_str": card_date_str,
                    "description": card_title,
                    "big_desc": card_title,
                    "external_url": detail_url,
                    "image_url": image_url,
                    "ticket_link": detail_url,
                }
            )

        # 3. Concurrent HTTP detail page traversal
        enriched_items: List[Dict[str, Any]] = []
        fallback_items: List[Dict[str, Any]] = []

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
                    fallback_items.append(updated_item)

        if fallback_items:
            drv = ensure_browser()
            for item in fallback_items:
                try:
                    drv.get(item["external_url"])
                    time.sleep(1.5)
                    parsed = parse_detail_page_html(
                        drv.page_source,
                        item.get("title"),
                        item.get("_card_date_str"),
                        item.get("image_url"),
                        item["external_url"],
                    )
                    item["title"] = parsed["title"] or item["title"]
                    if parsed["start_time"]:
                        item["start_time"] = parsed["start_time"]
                    item["description"] = parsed["description"]
                    item["big_desc"] = parsed["big_desc"]
                    item["image_url"] = parsed["image_url"]
                    item["ticket_link"] = parsed["ticket_link"]
                    enriched_items.append(item)
                except Exception as fb_err:
                    logger.warning(
                        f"[FALLBACK WARN] Failed {item['external_url']}: {fb_err}"
                    )

        # 4. Consolidate multi-performance runs by canonical event URL / title
        grouped: Dict[str, Dict[str, Any]] = {}
        for item in enriched_items:
            item.pop("_card_date_str", None)
            title = normalize_ws(item.get("title"))
            dates = item.get("start_time") or []
            if not title or not dates:
                logger.warning(
                    f"[SKIP] Missing title or valid dates for '{item.get('external_url')}'."
                )
                continue

            key = normalize_mec_group_key(item.get("external_url"), title)
            if key in grouped:
                grouped[key]["dates_set"].update(dates)
                continue

            entry = dict(item)
            entry["title"] = title
            entry["dates_set"] = set(dates)
            grouped[key] = entry

        final_rows: List[Dict[str, Any]] = []
        for entry in grouped.values():
            entry["start_time"] = sorted(entry.pop("dates_set"))
            final_rows.append(entry)

        return pd.DataFrame(final_rows) if final_rows else pd.DataFrame()

    except Exception as e:
        logger.error(
            f"[FATAL ERROR] Exception encountered during scraping loop: {e}", exc_info=True)
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
    logger.info("Executing Federal Way PAEC autonomous scraper pipeline task...")
    raw_df = scraper()

    if not raw_df.empty:
        pipeline = VividaIngestionPipeline(table_name="events")
        success = pipeline.run_standalone_pipeline(raw_df)

        if success:
            logger.info(
                "[PIPELINE SUCCESS] FWPAEC scraper pipeline execution completed successfully.")
        else:
            logger.error(
                "[PIPELINE ERROR] FWPAEC scraper pipeline execution reported errors.")
    else:
        logger.warning("[EXIT] No events scraped. Pipeline handoff skipped.")
