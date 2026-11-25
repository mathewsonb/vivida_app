"""
mcmenaminsElksTemple.py
Autonomous Scraper for McMenamins Elks Temple / Spanish Ballroom (Tacoma, WA).
Uses HTTP-first listing with automatic cross-platform browser hydration fallback,
in-card description extraction + rate-limit-safe detail fetching (workers=3),
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
logger = logging.getLogger("scrape_mcmenamins")

BASE_URL = "https://www.mcmenamins.com"
TARGET_URL = "https://www.mcmenamins.com/elks-temple/spanish-ballroom"
DEFAULT_VENUE = "McMenamins Elks Temple"
DEFAULT_ADDRESS = "565 Broadway, Tacoma, WA 98402"

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


def build_http_session(pool_size: int = 6) -> requests.Session:
    session = requests.Session()
    session.headers.update(HEADERS)
    retries = Retry(
        total=1,
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


def extract_css_bg_image(style_attr: Optional[str]) -> Optional[str]:
    """Safely extracts background image URL from inline CSS style attributes."""
    if not style_attr:
        return None
    match = re.search(
        r'url\((?:["\']?)(.*?)(?:["\']?)\)', style_attr, re.IGNORECASE
    )
    if match:
        return resolve_url(match.group(1).strip())
    return None


def extract_ticket_url(element) -> Optional[str]:
    """Extracts external ticket link from buttons or anchors via href/onclick regex."""
    if not element:
        return None

    href = element.get("href")
    if href:
        resolved = resolve_url(href)
        if resolved:
            return resolved

    onclick = element.get("onclick")
    if onclick:
        match = re.search(r"https?://[^\s'\"]+", onclick)
        if match:
            return resolve_url(match.group(0))
    return None


def find_calendar_items(soup: BeautifulSoup) -> List[BeautifulSoup]:
    """Locates McMenamins calendar-item cards inside #full_calendar or main document."""
    target_section = soup.find("div", id="full_calendar") or soup
    for element in target_section(["script", "style"]):
        element.decompose()
    return target_section.find_all("div", class_=re.compile(r"^calendar-item", re.I))


def extract_card_description(event: BeautifulSoup, title: str) -> Optional[str]:
    """Extracts inline summary/subhead/notes directly from the calendar card if present."""
    snippets: List[str] = []
    for p in event.find_all(
        ["p", "div"],
        class_=re.compile(r"subhead|subtitle|desc|summary|info|doors|age", re.I),
    ):
        txt = normalize_ws(p.get_text(" ", strip=True))
        if txt and txt.lower() != title.lower() and txt not in snippets:
            snippets.append(txt)

    if not snippets:
        for p in event.find_all("p"):
            classes = " ".join(p.get("class") or [])
            if "uk-panel-time" in classes:
                continue
            txt = normalize_ws(p.get_text(" ", strip=True))
            if txt and txt.lower() != title.lower() and txt not in snippets:
                snippets.append(txt)

    combined = " ".join(snippets).strip()
    return combined if len(combined) >= 25 else None


def parse_detail_html(
    html: str,
    current_dates: List[str],
    card_desc: Optional[str],
    card_title: str,
    current_ticket: Optional[str],
    current_image: Optional[str],
    detail_url: str,
) -> Tuple[List[str], str, str, Optional[str], Optional[str]]:
    """Extracts fallback dates, rich description, ticket link, and image from detail HTML."""
    det_soup = BeautifulSoup(clean_html_source(html), "html.parser")
    if not det_soup:
        fallback_desc = card_desc or card_title
        return (
            current_dates,
            fallback_desc,
            fallback_desc,
            current_ticket or detail_url,
            current_image,
        )

    container = (
        det_soup.find("div", class_=re.compile(r"uk-width-xlarge-7-10", re.I))
        or det_soup.find("main")
        or det_soup
    )

    dates = current_dates
    if not dates:
        time_el = container.find(
            "p", class_=re.compile(r"uk-panel-time|date", re.I)
        ) or det_soup.find("meta", property="event:start_time")
        if time_el:
            raw_dt = time_el.get("content") or time_el.get_text(" ", strip=True)
            dates = parse_event_dates(normalize_ws(raw_dt))

    ticket_link = current_ticket
    if not ticket_link:
        t_btn = container.find(
            ["a", "button"], href=re.compile(r"etix|ticket|buy", re.I)
        ) or container.find(
            ["a", "button"], class_=re.compile(r"uk-button-primary", re.I)
        )
        if t_btn:
            ticket_link = extract_ticket_url(t_btn)

    image_url = current_image
    if not image_url:
        og_img = det_soup.find("meta", property="og:image")
        if og_img and og_img.get("content"):
            image_url = resolve_url(og_img["content"])

    rich_text = fetch_event_text(container)
    big_desc = normalize_ws(rich_text) if rich_text else (card_desc or card_title)
    description = card_desc or (big_desc[:250] if big_desc else card_title)

    return dates, description, big_desc, ticket_link or detail_url, image_url


def fetch_detail_via_http(
    session: requests.Session, item: Dict[str, Any], delay_sec: float = 0.15
) -> Tuple[Dict[str, Any], bool]:
    detail_url = item.get("external_url")
    if not detail_url:
        return item, True

    time.sleep(delay_sec)
    try:
        resp = session.get(detail_url, timeout=10)
        resp.raise_for_status()
        if len(resp.text) < 500:
            return item, False

        dates, description, big_desc, ticket_link, image_url = parse_detail_html(
            resp.text,
            item["start_time"],
            item.get("_card_desc"),
            item["title"],
            item.get("ticket_link"),
            item.get("image_url"),
            detail_url,
        )
        item["start_time"] = dates
        item["description"] = description
        item["big_desc"] = big_desc
        item["ticket_link"] = ticket_link
        item["image_url"] = image_url
        return item, True
    except Exception:
        if not item.get("ticket_link"):
            item["ticket_link"] = detail_url
        return item, False


def scraper() -> pd.DataFrame:
    """Scrapes McMenamins Elks Temple events via HTTP-first + polite concurrent detail fetching."""
    logger.info(f"[START] Scraping McMenamins Elks Temple: {TARGET_URL}")
    session = build_http_session(pool_size=6)

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
        # 1. HTTP-first listing fetch
        try:
            resp = session.get(TARGET_URL, timeout=15)
            resp.raise_for_status()
            soup_http = BeautifulSoup(clean_html_source(resp.text), "html.parser")
            candidates = find_calendar_items(soup_http)
            if candidates:
                event_items = candidates
                logger.info(
                    f"[HTTP FAST-PATH] Loaded McMenamins calendar via HTTP ({len(event_items)} cards)."
                )
            else:
                logger.info(
                    "[HTTP LISTING] Page shell loaded with 0 static event cards; launching browser fallback..."
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
                WebDriverWait(drv, 10).until(
                    EC.presence_of_element_located(
                        (By.CSS_SELECTOR, "#full_calendar, div.calendar-item")
                    )
                )
            except Exception:
                pass
            time.sleep(2)

            soup_browser = BeautifulSoup(
                clean_html_source(drv.page_source), "html.parser"
            )
            event_items = find_calendar_items(soup_browser)

        if not event_items:
            logger.warning(
                "[PARSER WARNING] Found 0 event cards matching class pattern 'calendar-item'."
            )
            return pd.DataFrame()

        logger.info(f"[PARSER SUCCESS] Found {len(event_items)} McMenamins event cards.")

        # 2. Extract card metadata and consolidate multi-night runs
        grouped_items: Dict[str, Dict[str, Any]] = {}

        for idx, event in enumerate(event_items, start=1):
            try:
                h3_tag = event.find("h3")
                title = (
                    normalize_ws(h3_tag.get_text(" ", strip=True)) if h3_tag else ""
                )
                if not title:
                    continue

                start_time_list: List[str] = []
                time_p = event.find("p", class_=re.compile(r"uk-panel-time", re.I))
                if time_p:
                    start_time_list = parse_event_dates(
                        normalize_ws(time_p.get_text(" ", strip=True))
                    )

                anchor = event.find("a", href=True)
                detail_url = resolve_url(anchor["href"]) if anchor else None
                dedup_key = detail_url or title.lower()

                if dedup_key in grouped_items:
                    grouped_items[dedup_key]["dates_set"].update(start_time_list)
                    continue

                card_desc = extract_card_description(event, title)

                panel_div = event.find("div", class_=re.compile(r"uk-panel", re.I))
                image_url = (
                    extract_css_bg_image(panel_div["style"])
                    if panel_div and panel_div.get("style")
                    else None
                )

                ticket_btn = event.find(
                    ["button", "a"], class_=re.compile(r"uk-button-primary", re.I)
                )
                ticket_link = extract_ticket_url(ticket_btn) if ticket_btn else None

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
                    "ticket_link": ticket_link or detail_url,
                }
            except Exception as item_err:
                logger.error(
                    f"[ITEM ERROR] Failed parsing McMenamins card #{idx}: {item_err}",
                    exc_info=True,
                )

        if not grouped_items:
            return pd.DataFrame()

        staged_items: List[Dict[str, Any]] = []
        for entry in grouped_items.values():
            entry["start_time"] = sorted(entry.pop("dates_set"))
            staged_items.append(entry)

        # 3. Polite concurrent HTTP detail enrichment (workers=3 avoids McMenamins rate limiting)
        logger.info(
            f"Batch-fetching {len(staged_items)} McMenamins detail pages via HTTP (workers=3)..."
        )
        enriched_items: List[Dict[str, Any]] = []
        parsed_count = 0
        fallback_kept_count = 0

        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = [
                executor.submit(fetch_detail_via_http, session, item)
                for item in staged_items
            ]
            for fut in as_completed(futures):
                updated_item, ok = fut.result()
                enriched_items.append(updated_item)
                if ok:
                    parsed_count += 1
                else:
                    fallback_kept_count += 1

        logger.info(
            f"HTTP detail batch complete: {parsed_count} full detail pages parsed, "
            f"{fallback_kept_count} kept with card-level metadata."
        )

        raw_records = []
        for item in enriched_items:
            item.pop("_card_desc", None)
            if not item.get("start_time"):
                logger.warning(
                    f"[SKIP] No valid dates parsed for '{item.get('title')}'."
                )
                continue
            raw_records.append(item)

        return pd.DataFrame(raw_records) if raw_records else pd.DataFrame()

    except Exception as e:
        logger.error(
            f"[FATAL ERROR] Exception encountered during scraping loop: {e}",
            exc_info=True,
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
    logger.info("Executing McMenamins Elks Temple autonomous scraper task...")
    raw_df = scraper()

    if not raw_df.empty:
        pipeline = VividaIngestionPipeline(table_name="events")
        success = pipeline.run_standalone_pipeline(raw_df)

        if success:
            logger.info(
                "[PIPELINE SUCCESS] McMenamins scraper pipeline execution completed successfully."
            )
        else:
            logger.error(
                "[PIPELINE ERROR] McMenamins scraper pipeline execution reported errors."
            )
    else:
        logger.warning("[EXIT] No events scraped. Pipeline handoff skipped.")