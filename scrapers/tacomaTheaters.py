"""
tacomaTheaters.py
Autonomous Production Scraper for Tacoma City Theaters (Pantages, Rialto, Theatre on the Square).
Uses HTTP-first listing & concurrent detail-page fetching (workers=10), with lazy Xvfb/Chrome
pagination fallback for "Load More" unfolding.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
import glob
import logging
import re
import shutil
import sys
import time
from typing import Dict, List, Optional, Set, Tuple

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
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(filename)s:%(lineno)d] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("scrape_tacoma_city_theaters")

BASE_URL = "https://www.tacomacitytheaters.org"
TARGET_URL = "https://www.tacomacitytheaters.org/events"
DEFAULT_VENUE = "Tacoma City Theaters"
DEFAULT_ADDRESS = "901 Broadway, Tacoma, WA 98402"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

VENUE_ADDRESS_MAP: Dict[str, str] = {
    "pantages": "901 Broadway, Tacoma, WA 98402",
    "rialto": "310 S 9th St, Tacoma, WA 98402",
    "theatre on the square": "915 Broadway, Tacoma, WA 98402",
    "theater on the square": "915 Broadway, Tacoma, WA 98402",
}


def normalize_ws(text: Optional[str]) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def resolve_url(href: Optional[str]) -> Optional[str]:
    if not href:
        return None
    clean = href.strip()
    if not clean or clean.startswith("#") or clean.lower().startswith("javascript:"):
        return None
    if clean.startswith("http"):
        return clean
    return f"{BASE_URL}{clean if clean.startswith('/') else '/' + clean}"


def resolve_venue_address(venue_name: str) -> str:
    """Maps specific Tacoma City Theaters stages to their exact street addresses."""
    norm = normalize_ws(venue_name).lower()
    for key, addr in VENUE_ADDRESS_MAP.items():
        if key in norm:
            return addr
    return DEFAULT_ADDRESS


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


def create_stealth_driver():
    """Lazy-instantiates undetected Chrome driver configured for Xvfb display rendering."""
    import undetected_chromedriver as uc

    options = uc.ChromeOptions()
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-gpu")

    cached_chrome = sorted(
        glob.glob("/home/vscode/.cache/selenium/chrome/linux64/*/chrome")
    )
    chrome_bin = (
        shutil.which("google-chrome")
        or shutil.which("google-chrome-stable")
        or shutil.which("chromium")
        or shutil.which("chromium-browser")
        or (cached_chrome[-1] if cached_chrome else None)
    )

    if not chrome_bin:
        raise RuntimeError(
            "No Chrome or Chromium executable found. Install google-chrome-stable first."
        )

    options.binary_location = chrome_bin
    return uc.Chrome(options=options, version_main=None)


def fetch_unfolded_listing_html(session: requests.Session) -> str:
    """
    Tries fast HTTP fetch first; falls back to Xvfb + undetected Chrome only if
    the page requires JS rendering or has an active 'Load More' pagination button.
    """
    try:
        resp = session.get(TARGET_URL, timeout=15)
        resp.raise_for_status()
        html = resp.text
        soup = BeautifulSoup(html, "html.parser")
        cards = soup.select("div.eventItem")
        load_btn = soup.select_one(
            "#loadMoreEvents, a.loadMore, button.loadMore, a.btn-load-more"
        )
        if cards and not load_btn:
            logger.info(
                f"[HTTP FAST-PATH] Retrieved all {len(cards)} event cards directly via HTTP."
            )
            return html
        logger.info(
            f"[LISTING] Initial HTTP fetch found {len(cards)} cards with Load More pagination; "
            "launching Xvfb + Chrome to unfold full listing..."
        )
    except Exception as http_err:
        logger.warning(
            f"[LISTING] HTTP listing fetch failed ({http_err}); falling back to Chrome..."
        )

    from pyvirtualdisplay import Display
    from selenium.webdriver.common.by import By

    display = Display(visible=0, size=(1920, 1080), backend="xvfb")
    display.start()
    driver = None

    try:
        driver = create_stealth_driver()
        driver.get(TARGET_URL)
        time.sleep(5)

        load_clicks = 0
        while True:
            try:
                buttons = driver.find_elements(By.ID, "loadMoreEvents")
                if not buttons:
                    buttons = driver.find_elements(
                        By.CSS_SELECTOR, "a.loadMore, button.loadMore, a.btn-load-more"
                    )

                if buttons and buttons[0].is_displayed() and buttons[0].is_enabled():
                    btn = buttons[0]
                    driver.execute_script(
                        "arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});",
                        btn,
                    )
                    time.sleep(0.5)
                    driver.execute_script("arguments[0].click();", btn)
                    load_clicks += 1
                    logger.info(
                        f"[PAGINATION] Clicked 'Load More' button (click #{load_clicks})..."
                    )
                    time.sleep(2.5)
                else:
                    logger.info(
                        f"[PAGINATION COMPLETE] All events unfolded. Total clicks: {load_clicks}."
                    )
                    break
            except Exception as nav_err:
                logger.info(
                    f"[PAGINATION END] Pagination stopped after {load_clicks} clicks: {nav_err}"
                )
                break

        return driver.page_source
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass
        display.stop()


def parse_detail_html(
    html: str, current_dates: List[str], current_desc: str, current_ticket: Optional[str], detail_url: str
) -> Tuple[List[str], str, Optional[str]]:
    """Extracts fallback dates, rich description text, and ticket link from detail HTML."""
    det_soup = BeautifulSoup(clean_html_source(html), "html.parser")
    if not det_soup:
        return current_dates, current_desc, current_ticket

    dates = current_dates
    if not dates:
        det_date_el = det_soup.select_one("div.date") or det_soup.find("time")
        if det_date_el:
            dates = parse_event_dates(normalize_ws(
                det_date_el.get_text(" ", strip=True)))

    container = (
        det_soup.select_one("div.leftColumn")
        or det_soup.select_one("div.event-description")
        or det_soup.find("main")
        or det_soup
    )
    rich_text = fetch_event_text(container)
    big_desc = normalize_ws(rich_text) if rich_text else current_desc

    ticket_link = current_ticket
    if not ticket_link or ticket_link == detail_url:
        t_btn = det_soup.select_one("a.tickets") or det_soup.find(
            "a",
            href=re.compile(r"ticket|buy|tessitura|bit\.ly", re.IGNORECASE),
        )
        if t_btn and t_btn.get("href"):
            ticket_link = resolve_url(t_btn["href"]) or ticket_link

    return dates, big_desc, ticket_link


def fetch_detail_via_http(
    session: requests.Session, item: Dict[str, Optional[str]]
) -> Tuple[Dict[str, Optional[str]], bool]:
    """Fetches and parses a single detail page over pooled HTTP."""
    detail_url = item.get("external_url")
    if not detail_url:
        return item, True

    try:
        resp = session.get(detail_url, timeout=15)
        resp.raise_for_status()
        if len(resp.text) < 500:
            return item, False

        dates, big_desc, ticket_link = parse_detail_html(
            resp.text,
            item["start_time"],  # type: ignore[arg-type]
            item["big_desc"] or item["description"] or "",
            item.get("ticket_link"),
            detail_url,
        )
        item["start_time"] = dates  # type: ignore[assignment]
        item["big_desc"] = big_desc
        item["ticket_link"] = ticket_link
        return item, True
    except Exception:
        return item, False


def scraper() -> pd.DataFrame:
    """Scrapes Tacoma City Theaters using unfolded listing + concurrent HTTP detail fetching."""
    logger.info(f"[START] Scraping Tacoma City Theaters: {TARGET_URL}")
    session = build_http_session(pool_size=12)

    try:
        raw_listing_html = fetch_unfolded_listing_html(session)
    except Exception as e:
        logger.error(
            f"[FATAL ERROR] Could not load Tacoma City Theaters listing: {e}", exc_info=True)
        return pd.DataFrame()

    main_html = clean_html_source(raw_listing_html)
    soup = BeautifulSoup(main_html, "html.parser")

    target_section = (
        soup.find("div", class_="eventList event_list_grid event_list is-loaded")
        or soup
    )
    for element in target_section(["script", "style"]):
        element.decompose()

    event_items = target_section.select("div.eventItem")
    if not event_items:
        event_items = target_section.find_all(
            "div", class_=re.compile(r"^eventItem entry", re.I)
        )

    logger.info(
        f"[PARSER SUCCESS] Found {len(event_items)} unfolded Tacoma City Theaters event cards."
    )

    staged_items: List[Dict[str, Optional[str]]] = []
    seen_urls: Set[str] = set()

    for idx, event in enumerate(event_items, start=1):
        try:
            title_el = (
                event.select_one("h3.title a")
                or event.select_one("h3 a")
                or event.find("h3")
            )
            if not title_el:
                continue

            card_title = normalize_ws(title_el.get_text(" ", strip=True))
            if not card_title:
                continue

            href = (
                title_el.get("href", "").strip()
                if title_el.name == "a"
                else ""
            )
            if not href:
                anchor = event.find("a", href=True)
                href = anchor["href"].strip() if anchor else ""

            detail_url = resolve_url(href)

            dedup_key = detail_url or card_title.lower()
            if dedup_key in seen_urls:
                continue
            seen_urls.add(dedup_key)

            loc_el = event.select_one("div.location")
            venue_name = normalize_ws(loc_el.get_text(
                " ", strip=True)) if loc_el else ""
            if not venue_name:
                logo_el = event.select_one("a.venue_logo")
                venue_name = (
                    normalize_ws(logo_el.get("title", ""))
                    if logo_el and logo_el.get("title")
                    else DEFAULT_VENUE
                )

            date_el = event.select_one("div.date")
            raw_date_str = normalize_ws(date_el.get_text(
                " ", strip=True)) if date_el else ""
            start_time_seq = parse_event_dates(
                raw_date_str) if raw_date_str else []

            tagline_el = event.select_one("h4.tagline")
            description = (
                normalize_ws(tagline_el.get_text(" ", strip=True))
                if tagline_el
                else card_title
            )

            img_el = event.select_one(
                "div.thumb img") or event.find("img", src=True)
            image_url = resolve_url(img_el.get("src")) if img_el else None

            ticket_el = event.select_one("a.tickets") or event.find(
                "a", href=re.compile(r"ticket|buy|bit\.ly", re.IGNORECASE)
            )
            ticket_link = (
                resolve_url(ticket_el.get("href"))
                if ticket_el and ticket_el.get("href")
                else detail_url
            )

            staged_items.append(
                {
                    "venue_name": venue_name,
                    "address": resolve_venue_address(venue_name),
                    "title": card_title,
                    "start_time": start_time_seq,  # type: ignore[dict-item]
                    "raw_date_str": raw_date_str,
                    "description": description or card_title,
                    "big_desc": description or card_title,
                    "external_url": detail_url,
                    "image_url": image_url,
                    "ticket_link": ticket_link,
                }
            )
        except Exception as item_err:
            logger.error(
                f"[ITEM ERROR] Failed parsing card #{idx}: {item_err}",
                exc_info=True,
            )

    if not staged_items:
        return pd.DataFrame()

    # Batch-fetch all detail pages concurrently via HTTP (workers=10)
    logger.info(
        f"Batch-fetching {len(staged_items)} event detail pages via HTTP (workers=10)...")
    enriched_items: List[Dict[str, Optional[str]]] = []
    fallback_queue: List[Dict[str, Optional[str]]] = []

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
        f"{len(fallback_queue)} queued for Selenium fallback."
    )

    # Lazy Selenium fallback ONLY for detail pages that failed HTTP
    if fallback_queue:
        from pyvirtualdisplay import Display

        display = Display(visible=0, size=(1920, 1080), backend="xvfb")
        display.start()
        driver = None
        try:
            driver = create_stealth_driver()
            for item in fallback_queue:
                detail_url = item.get("external_url")
                if detail_url:
                    try:
                        driver.get(detail_url)
                        time.sleep(2.5)
                        dates, big_desc, ticket_link = parse_detail_html(
                            driver.page_source,
                            item["start_time"],  # type: ignore[arg-type]
                            item["big_desc"] or item["description"] or "",
                            item.get("ticket_link"),
                            detail_url,
                        )
                        item["start_time"] = dates  # type: ignore[assignment]
                        item["big_desc"] = big_desc
                        item["ticket_link"] = ticket_link
                    except Exception as fb_err:
                        logger.warning(
                            f"[FALLBACK WARN] Failed {detail_url}: {fb_err}")
                enriched_items.append(item)
        finally:
            if driver:
                try:
                    driver.quit()
                except Exception:
                    pass
            display.stop()

    raw_records = []
    for item in enriched_items:
        if not item.get("start_time"):
            logger.warning(
                f"[SKIP] No valid dates parsed for '{item.get('title')}' (raw='{item.get('raw_date_str')}')"
            )
            continue
        item.pop("raw_date_str", None)
        raw_records.append(item)

    return pd.DataFrame(raw_records) if raw_records else pd.DataFrame()


if __name__ == "__main__":
    logger.info("Executing Tacoma City Theaters scraper task...")
    raw_df = scraper()

    if not raw_df.empty:
        pipeline = VividaIngestionPipeline(table_name="events")
        success = pipeline.run_standalone_pipeline(raw_df)
        if success:
            logger.info(
                "[PIPELINE SUCCESS] Tacoma City Theaters scraper execution completed successfully."
            )
        else:
            logger.error(
                "[PIPELINE ERROR] Tacoma City Theaters scraper pipeline execution reported errors."
            )
    else:
        logger.warning("[EXIT] No events scraped. Pipeline handoff skipped.")
