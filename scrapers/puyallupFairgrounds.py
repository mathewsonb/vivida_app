"""
puyallupFairgrounds.py
Autonomous Scraper for Puyallup Fairgrounds / Washington State Fair (Puyallup, WA).
Uses HTTP-first pagination & concurrent detail-page fetching (workers=10),
with automatic Xvfb/Chrome hydration fallback and centralized parse_event_dates normalization.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
import glob
import logging
import re
import shutil
import sys
import time
from typing import Any, Dict, List, Optional, Set, Tuple

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
logger = logging.getLogger("scrape_puyallup_fair")

BASE_URL = "https://www.thefair.com"
TARGET_URL = "https://www.thefair.com/events-calendar/"
DEFAULT_VENUE = "Puyallup Fairgrounds"
DEFAULT_ADDRESS = "110 9th Ave SW, Puyallup, WA 98371"

MONTH_NAMES = (
    r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec|"
    r"January|February|March|April|May|June|July|August|September|October|November|December)"
)

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


def extract_valid_cards(html: str) -> List[BeautifulSoup]:
    """Parses listing HTML and isolates .event-card-large cards containing .event-card-large__info."""
    soup = BeautifulSoup(clean_html_source(html), "html.parser")
    target_container = soup.find(
        "div", class_=re.compile(r"container--md2")) or soup
    event_items = target_container.find_all("div", class_="event-card-large")
    return [card for card in event_items if card.find("div", class_="event-card-large__info")]


def parse_detail_html(
    html: str,
    current_dates: List[str],
    card_desc: Optional[str],
    card_title: str,
    current_ticket: Optional[str],
    detail_url: str,
) -> Tuple[List[str], str, str, Optional[str]]:
    """Extracts fallback dates, rich description text, and ticket link from a detail page."""
    det_soup = BeautifulSoup(clean_html_source(html), "html.parser")
    if not det_soup:
        fallback_desc = card_desc or card_title
        return current_dates, fallback_desc, fallback_desc, current_ticket or detail_url

    dates = current_dates
    if not dates:
        det_date = det_soup.find(
            "div", class_=re.compile(r"date|event-date|schedule", re.I)
        ) or det_soup.find("span", class_=re.compile(r"date", re.I))
        if det_date:
            dates = parse_event_dates(normalize_ws(
                det_date.get_text(" ", strip=True)))

    rich_text = fetch_event_text(det_soup)
    big_desc = normalize_ws(rich_text) if rich_text else (
        card_desc or card_title)
    description = card_desc or (big_desc[:250] if big_desc else card_title)

    ticket_link = current_ticket
    if not ticket_link:
        t_btn = det_soup.find("a", href=re.compile(
            r"ticket|buy|admission", re.I))
        if t_btn and t_btn.get("href"):
            ticket_link = resolve_url(t_btn["href"])

    return dates, description, big_desc, ticket_link or detail_url


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

        dates, description, big_desc, ticket_link = parse_detail_html(
            resp.text,
            item["start_time"],
            item.get("_card_desc"),
            item["title"],
            item.get("ticket_link"),
            detail_url,
        )
        item["start_time"] = dates
        item["description"] = description
        item["big_desc"] = big_desc
        item["ticket_link"] = ticket_link
        return item, True
    except Exception:
        return item, False


def scraper(max_pages: int = 20) -> pd.DataFrame:
    """Scrapes Puyallup Fairgrounds events via HTTP-first pagination + concurrent detail fetching."""
    logger.info(f"[START] Scraping Puyallup Fairgrounds: {TARGET_URL}")
    session = build_http_session(pool_size=12)

    display = None
    driver = None
    browser_synced = False

    def ensure_browser():
        nonlocal display, driver
        if driver is None:
            from pyvirtualdisplay import Display

            display = Display(visible=0, size=(1920, 1080), backend="xvfb")
            display.start()
            driver = create_stealth_driver()
        return driver

    grouped_items: Dict[str, Dict[str, Any]] = {}
    seen_page_signatures: Set[Tuple[str, ...]] = set()

    try:
        page = 1
        while page <= max_pages:
            page_url = TARGET_URL if page == 1 else f"{TARGET_URL}?page={page}"
            valid_cards: List[BeautifulSoup] = []

            # 1. HTTP-first page fetch
            try:
                resp = session.get(page_url, timeout=15)
                if resp.status_code == 200 and len(resp.text) > 1000:
                    valid_cards = extract_valid_cards(resp.text)
                    if valid_cards:
                        logger.info(
                            f"[HTTP PAGE {page}] Found {len(valid_cards)} valid event cards."
                        )
            except Exception as http_err:
                logger.warning(f"[HTTP PAGE {page}] Failed ({http_err}).")

            # 2. Browser hydration fallback only if page 1 had 0 static cards or browser already active
            if not valid_cards and (page == 1 or driver is not None):
                from selenium.webdriver.common.by import By
                from selenium.webdriver.support import expected_conditions as EC
                from selenium.webdriver.support.ui import WebDriverWait

                logger.info(
                    f"[SELENIUM FALLBACK] Rendering page {page}: {page_url}")
                drv = ensure_browser()
                drv.get(page_url)
                try:
                    WebDriverWait(drv, 10).until(
                        EC.presence_of_element_located(
                            (By.CSS_SELECTOR, "div.container--md2, div.event-card-large")
                        )
                    )
                except Exception:
                    pass
                time.sleep(2)
                valid_cards = extract_valid_cards(drv.page_source)

                if not browser_synced:
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
                    browser_synced = True

            if not valid_cards:
                logger.info(
                    f"[PARSER END] No further event cards found on page {page}. Ending pagination.")
                break

            page_titles: List[str] = []

            for idx, event in enumerate(valid_cards, start=1):
                try:
                    info_container = event.find(
                        "div", class_="event-card-large__info")
                    if not info_container:
                        continue

                    h3_tag = info_container.find("h3", class_="title") or info_container.find(
                        ["h3", "h2", "h4"]
                    )
                    if not h3_tag:
                        continue

                    title = normalize_ws(h3_tag.get_text(" ", strip=True))
                    if not title:
                        continue
                    page_titles.append(title.lower())

                    anchor = h3_tag.find(
                        "a", href=True) or info_container.find("a", href=True)
                    detail_url = resolve_url(
                        anchor["href"]) if anchor else None

                    card_desc: Optional[str] = None
                    start_time_list: List[str] = []

                    icon_texts = info_container.find_all(
                        "div", class_="icon-text")
                    for div in icon_texts:
                        div_copy = BeautifulSoup(str(div), "html.parser")
                        for svg in div_copy.find_all("svg"):
                            svg.decompose()

                        raw_text = normalize_ws(
                            div_copy.get_text(" ", strip=True))
                        span_tag = div.find("span")
                        if span_tag and normalize_ws(span_tag.get_text(" ", strip=True)):
                            card_desc = normalize_ws(
                                span_tag.get_text(" ", strip=True))
                        elif re.search(rf"\b{MONTH_NAMES}\b", raw_text, re.I) and not start_time_list:
                            start_time_list = parse_event_dates(raw_text)

                    dedup_key = detail_url or title.lower()
                    if dedup_key in grouped_items:
                        grouped_items[dedup_key]["dates_set"].update(
                            start_time_list)
                        continue

                    img_tag = event.find("img", src=True) or event.find(
                        "img", attrs={"data-src": True}
                    )
                    image_url = (
                        resolve_url(img_tag.get("data-src")
                                    or img_tag.get("src"))
                        if img_tag
                        else None
                    )

                    ticket_link: Optional[str] = None
                    buttons_div = event.find(
                        "div", class_=re.compile(r"event-card-large__buttons|buttons", re.I)
                    )
                    if buttons_div:
                        t_anchor = buttons_div.find(
                            "a", href=re.compile(r"ticket", re.I))
                        if t_anchor and t_anchor.get("href"):
                            ticket_link = resolve_url(t_anchor["href"])

                    grouped_items[dedup_key] = {
                        "venue_name": DEFAULT_VENUE,
                        "address": DEFAULT_ADDRESS,
                        "title": title,
                        "dates_set": set(start_time_list),
                        "_card_desc": card_desc,
                        "description": card_desc or title,
                        "big_desc": card_desc or title,
                        "external_url": detail_url,
                        "image_url": image_url,
                        "ticket_link": ticket_link,
                    }
                except Exception as item_err:
                    logger.error(
                        f"[ITEM ERROR] Failed parsing Puyallup card #{idx} on page {page}: {item_err}",
                        exc_info=True,
                    )

            sig = tuple(page_titles)
            if sig in seen_page_signatures:
                logger.info(
                    f"[PARSER END] Page {page} repeated prior page content; stopping pagination.")
                break
            seen_page_signatures.add(sig)
            page += 1

        if not grouped_items:
            return pd.DataFrame()

        staged_items: List[Dict[str, Any]] = []
        for entry in grouped_items.values():
            entry["start_time"] = sorted(entry.pop("dates_set"))
            staged_items.append(entry)

        logger.info(
            f"Batch-fetching {len(staged_items)} Puyallup Fairgrounds detail pages via HTTP (workers=10)..."
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
                        dates, description, big_desc, ticket_link = parse_detail_html(
                            drv.page_source,
                            item["start_time"],
                            item.get("_card_desc"),
                            item["title"],
                            item.get("ticket_link"),
                            detail_url,
                        )
                        item["start_time"] = dates
                        item["description"] = description
                        item["big_desc"] = big_desc
                        item["ticket_link"] = ticket_link
                    except Exception as fb_err:
                        logger.warning(
                            f"[FALLBACK WARN] Failed {detail_url}: {fb_err}")
                enriched_items.append(item)

        raw_records = []
        for item in enriched_items:
            item.pop("_card_desc", None)
            if not item.get("start_time"):
                logger.warning(
                    f"[SKIP] No valid dates parsed for '{item.get('title')}'.")
                continue
            raw_records.append(item)

        return pd.DataFrame(raw_records) if raw_records else pd.DataFrame()

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
    logger.info("Executing Puyallup Fairgrounds scraper task...")
    raw_df = scraper()

    if not raw_df.empty:
        pipeline = VividaIngestionPipeline(table_name="events")
        success = pipeline.run_standalone_pipeline(raw_df)
        if success:
            logger.info(
                "[PIPELINE SUCCESS] Puyallup Fairgrounds scraper execution completed successfully.")
        else:
            logger.error(
                "[PIPELINE ERROR] Puyallup Fairgrounds scraper pipeline execution reported errors.")
    else:
        logger.warning("[EXIT] No events scraped. Pipeline handoff skipped.")
