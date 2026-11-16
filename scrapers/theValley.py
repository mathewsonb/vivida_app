"""
theValley.py
Autonomous Scraper for The Valley (Tacoma, WA - Squarespace Engine).
Uses HTTP-first listing & concurrent detail-page fetching (workers=10),
with lazy headless Chrome fallback and Supabase ingestion.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
import logging
import re
import sys
from typing import Dict, List, Optional, Tuple
from urllib.parse import unquote

from bs4 import BeautifulSoup
import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from vivida_etl import (
    VividaIngestionPipeline,
    clean_html_source,
    fetch_event_text,
    get_headless_chrome_options,
    parse_event_dates,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("scrape_the_valley")

BASE_URL = "https://www.thevalleytacoma.com"
TARGET_URL = "https://www.thevalleytacoma.com/new-events-1"
DEFAULT_VENUE = "The Valley"
DEFAULT_ADDRESS = "1206 Puyallup Ave, Tacoma, WA 98421"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def normalize_ws(text: Optional[str]) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def normalize_url(raw_url: Optional[str]) -> Optional[str]:
    """Resolves protocol-relative and site-relative Squarespace URLs."""
    if not raw_url:
        return None
    url = raw_url.strip()
    if not url or url.startswith("#") or url.startswith("javascript:"):
        return None
    if url.startswith("//"):
        return f"https:{url}"
    if url.startswith("http://") or url.startswith("https://"):
        return url
    return f"{BASE_URL}{url if url.startswith('/') else '/' + url}"


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


def fetch_rendered_page(
    driver,
    url: str,
    wait_selector: Optional[str] = None,
    timeout: int = 10,
) -> Optional[BeautifulSoup]:
    """Navigates active headless driver to target URL and returns pre-treated BeautifulSoup DOM."""
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait

    try:
        driver.get(url)
        if wait_selector:
            try:
                WebDriverWait(driver, timeout).until(
                    EC.presence_of_element_located(
                        (By.CSS_SELECTOR, wait_selector))
                )
            except Exception:
                pass

        cleaned_html = clean_html_source(driver.page_source)
        return BeautifulSoup(cleaned_html, "html.parser")
    except Exception as e:
        logger.warning(f"Failed to fetch page via Chrome '{url}': {e}")
        return None


def parse_address_url(addr_href: Optional[str]) -> str:
    """Decodes address string from Google Maps query parameter in Squarespace event metadata."""
    if not addr_href or "q=" not in addr_href:
        return DEFAULT_ADDRESS

    try:
        raw_q = addr_href.split("q=")[1].split("&")[0]
        decoded = normalize_ws(unquote(raw_q).replace("+", " "))
        return decoded if len(decoded) > 5 else DEFAULT_ADDRESS
    except Exception:
        return DEFAULT_ADDRESS


def extract_squarespace_dates(container: BeautifulSoup) -> List[str]:
    """Extracts single or multi-day [start, end] ISO dates from Squarespace <time> or meta tags."""
    time_tags = container.find_all(
        "time", class_=re.compile(r"event-date|eventlist-meta-time")
    )
    iso_dates: List[str] = []
    for tag in time_tags:
        raw_dt = tag.get("datetime") or tag.get_text(strip=True)
        if raw_dt:
            iso_dates.extend(parse_event_dates(raw_dt))

    if iso_dates:
        unique_sorted = sorted(set(iso_dates))
        return (
            [unique_sorted[0], unique_sorted[-1]]
            if len(unique_sorted) > 1
            else unique_sorted
        )

    meta_date = container.find(
        "li", class_=re.compile(r"eventlist-meta-date|event-meta-date")
    )
    if meta_date:
        return parse_event_dates(normalize_ws(meta_date.get_text(" ", strip=True)))

    return []


def parse_detail_soup(
    det_soup: BeautifulSoup,
    current_dates: List[str],
    current_desc: Optional[str],
    current_ticket: Optional[str],
) -> Tuple[List[str], Optional[str], Optional[str]]:
    """Extracts fallback dates, rich description text, and ticket link from detail DOM."""
    dates = current_dates or extract_squarespace_dates(det_soup)

    container = (
        det_soup.find("article", class_=re.compile(r"eventitem"))
        or det_soup.find("main")
        or det_soup
    )
    rich_text = fetch_event_text(container)
    big_desc = normalize_ws(rich_text) if rich_text else current_desc

    ticket_link = current_ticket
    t_anchor = det_soup.find(
        "a",
        href=re.compile(
            r"ticket|eventbrite|dice|seetickets|venuepilot|tixr|prekindle|buy",
            re.IGNORECASE,
        ),
    )
    if t_anchor and t_anchor.get("href"):
        ticket_link = normalize_url(t_anchor["href"]) or ticket_link

    return dates, big_desc, ticket_link


def fetch_detail_via_http(
    session: requests.Session, item: Dict[str, Optional[str]]
) -> Tuple[Dict[str, Optional[str]], bool]:
    """Fetches and parses a single Squarespace event detail page over pooled HTTP."""
    detail_url = item.get("external_url")
    if not detail_url:
        return item, True

    try:
        resp = session.get(detail_url, timeout=15)
        resp.raise_for_status()
        if len(resp.text) < 500:
            return item, False

        det_soup = BeautifulSoup(clean_html_source(resp.text), "html.parser")
        dates, big_desc, ticket_link = parse_detail_soup(
            det_soup,
            item["start_time"],  # type: ignore[arg-type]
            item.get("big_desc") or item.get("description"),
            item.get("ticket_link"),
        )
        item["start_time"] = dates  # type: ignore[assignment]
        item["big_desc"] = big_desc or item.get(
            "description") or item.get("title")
        item["ticket_link"] = ticket_link or detail_url
        return item, True
    except Exception:
        return item, False


def scraper() -> pd.DataFrame:
    """Scrapes The Valley events via HTTP-first concurrency with lazy headless Chrome fallback."""
    logger.info(f"Starting The Valley Tacoma scrape from {TARGET_URL}...")
    session = build_http_session(pool_size=12)
    driver = None

    try:
        # 1. Fetch main event list page (HTTP first, Chrome fallback)
        soup: Optional[BeautifulSoup] = None
        try:
            resp = session.get(TARGET_URL, timeout=15)
            resp.raise_for_status()
            candidate_soup = BeautifulSoup(
                clean_html_source(resp.text), "html.parser")
            if candidate_soup.find("article", class_=re.compile(r"eventlist-event")):
                soup = candidate_soup
                logger.info(
                    "[HTTP FAST-PATH] Loaded main Squarespace events listing via HTTP.")
        except Exception as http_err:
            logger.warning(
                f"HTTP listing fetch failed ({http_err}); falling back to Chrome...")

        if soup is None:
            from selenium import webdriver

            logger.info(
                "Launching headless Chrome fallback for main listing...")
            driver = webdriver.Chrome(options=get_headless_chrome_options())
            soup = fetch_rendered_page(
                driver,
                TARGET_URL,
                wait_selector="div.eventlist, article.eventlist-event",
            )

        if not soup:
            logger.error("Could not load main events page.")
            return pd.DataFrame()

        target_section = (
            soup.find("div", class_=re.compile(
                r"eventlist--upcoming|eventlist"))
            or soup
        )
        for element in target_section(["script", "style"]):
            element.decompose()

        event_items = target_section.find_all(
            "article", class_=re.compile(r"eventlist-event")
        )
        logger.info(f"Found {len(event_items)} Squarespace event articles.")

        # 2. Parse listing cards
        staged_items: List[Dict[str, Optional[str]]] = []
        for event in event_items:
            title: Optional[str] = None
            external_url: Optional[str] = None

            h1_tag = event.find(
                ["h1", "h2"], class_=re.compile(r"eventlist-title|title")
            )
            if h1_tag:
                anchor = h1_tag.find("a", href=True)
                if anchor:
                    title = normalize_ws(anchor.get_text(" ", strip=True))
                    external_url = normalize_url(anchor["href"])
                else:
                    title = normalize_ws(h1_tag.get_text(" ", strip=True))

            if not title:
                continue

            start_times = extract_squarespace_dates(event)

            desc_div = event.find(
                "div", class_=re.compile(r"sqs-html-content|eventlist-excerpt")
            )
            description = (
                normalize_ws(desc_div.get_text(" ", strip=True))
                if desc_div
                else None
            )

            img_tag = event.find("img", src=True) or event.find(
                "img", attrs={"data-src": True}
            )
            image_url = (
                normalize_url(img_tag.get("data-src") or img_tag.get("src"))
                if img_tag
                else None
            )

            address = DEFAULT_ADDRESS
            addr_anchor = event.find(
                "li", class_=re.compile(r"eventlist-meta-address")
            )
            if addr_anchor and addr_anchor.find("a", href=True):
                address = parse_address_url(addr_anchor.find("a")["href"])

            staged_items.append(
                {
                    "venue_name": DEFAULT_VENUE,
                    "address": address,
                    "title": title,
                    "start_time": start_times,  # type: ignore[dict-item]
                    "description": description or title,
                    "big_desc": description or title,
                    "external_url": external_url,
                    "image_url": image_url,
                    "ticket_link": external_url,
                }
            )

        if not staged_items:
            return pd.DataFrame()

        # 3. Batch-fetch detail pages concurrently via HTTP (workers=10)
        logger.info(
            f"Batch-fetching {len(staged_items)} detail pages via HTTP (workers=10)..."
        )
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
            f"{len(fallback_queue)} queued for Chrome fallback."
        )

        # 4. Lazy Chrome fallback ONLY for detail pages that failed HTTP
        if fallback_queue:
            from selenium import webdriver

            if driver is None:
                driver = webdriver.Chrome(
                    options=get_headless_chrome_options())
            for item in fallback_queue:
                detail_url = item.get("external_url")
                if detail_url:
                    det_soup = fetch_rendered_page(
                        driver,
                        detail_url,
                        wait_selector="article.eventitem, div.sqs-block-html, main",
                    )
                    if det_soup:
                        dates, big_desc, ticket_link = parse_detail_soup(
                            det_soup,
                            item["start_time"],  # type: ignore[arg-type]
                            item.get("big_desc") or item.get("description"),
                            item.get("ticket_link"),
                        )
                        item["start_time"] = dates  # type: ignore[assignment]
                        item["big_desc"] = (
                            big_desc or item.get(
                                "description") or item.get("title")
                        )
                        item["ticket_link"] = ticket_link or detail_url
                enriched_items.append(item)

        events_data = []
        for item in enriched_items:
            if not item.get("start_time"):
                logger.warning(
                    f"[SKIP] No valid dates parsed for '{item.get('title')}'.")
                continue
            events_data.append(item)

        return pd.DataFrame(events_data) if events_data else pd.DataFrame()

    except Exception as e:
        logger.error(
            f"Error during The Valley Tacoma scraping loop: {e}", exc_info=True)
        return pd.DataFrame()
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass


if __name__ == "__main__":
    logger.info("Executing The Valley Tacoma scraper task...")
    raw_df = scraper()

    if not raw_df.empty:
        pipeline = VividaIngestionPipeline(table_name="events")
        success = pipeline.run_standalone_pipeline(raw_df)
        if success:
            logger.info(
                "The Valley Tacoma scraper execution completed successfully."
            )
        else:
            logger.error(
                "The Valley Tacoma scraper pipeline execution reported errors."
            )
    else:
        logger.warning("No events scraped. Exiting task.")
