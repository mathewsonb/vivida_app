"""
tacomaPierceCountyEvents.py
Autonomous Scraper for Visit Tacoma / Pierce County Events Calendar (Simpleview Engine).
Fetches initial event queue via Simpleview REST API (with dynamic token/API-key refresh & DOM fallback)
and parses detail pages via concurrent HTTP batch requests, falling back to single-instance
headless Chrome only for pages requiring JS rendering.
"""

import concurrent.futures
import json
import logging
import os
import re
import sys
import threading
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

from bs4 import BeautifulSoup
import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
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
logger = logging.getLogger("scrape_tacoma_pierce_county")

BASE_URL = "https://www.visitpiercecounty.com"
EVENTS_INDEX_URL = f"{BASE_URL}/events/"
FALLBACK_API_KEY = (
    os.getenv("SIMPLEVIEW_API_KEY") or "8fc7af8cca14631dcf4dd6e30d58f7a9"
).strip()
DEFAULT_VENUE = "Tacoma / Pierce County"
DEFAULT_ADDRESS = "Tacoma, WA"

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": EVENTS_INDEX_URL,
}

TOKEN_PATTERNS = [
    r'token["\']?\s*[:=]\s*["\']([a-f0-9]{24,64})["\']',
    r"[?&]token=([a-f0-9]{24,64})",
    r'site_token["\']?\s*[:=]\s*["\']([a-f0-9]{24,64})["\']',
    r'apiKey["\']?\s*[:=]\s*["\']([a-f0-9]{24,64})["\']',
    r'api_key["\']?\s*[:=]\s*["\']([a-f0-9]{24,64})["\']',
    r"/rest_v2/plugins_events_[^\"'\s?&]+/find/\?[^\"'\s]*?token=([a-zA-Z0-9_-]{16,64})",
]

_thread_local = threading.local()


def get_thread_session() -> requests.Session:
    """Returns a thread-local requests.Session with connection pooling & retry backoff."""
    session = getattr(_thread_local, "session", None)
    if session is None:
        session = requests.Session()
        session.headers.update(DEFAULT_HEADERS)
        retry = Retry(
            total=2,
            backoff_factor=0.4,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET"],
        )
        adapter = HTTPAdapter(pool_connections=16, pool_maxsize=16, max_retries=retry)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        _thread_local.session = session
    return session


def normalize_url(raw_url: Optional[str]) -> Optional[str]:
    """Resolves protocol-relative and site-relative Simpleview URLs."""
    if not raw_url:
        return None
    url = str(raw_url).strip()
    if not url or url.startswith("#") or url.startswith("javascript:"):
        return None
    if url.startswith("//"):
        return f"https:{url}"
    if url.startswith("http://") or url.startswith("https://"):
        return url
    return f"{BASE_URL}{url if url.startswith('/') else '/' + url}"


def _extract_token_from_text(blob: str) -> Optional[str]:
    for pattern in TOKEN_PATTERNS:
        match = re.search(pattern, blob, flags=re.IGNORECASE)
        if match:
            candidate = (match.group(1) or "").strip()
            if candidate:
                return candidate
    return None


def get_fresh_simpleview_token(session: requests.Session) -> str:
    """Extracts a live Simpleview REST v2 token/API key from public events HTML or scripts."""
    if os.getenv("SIMPLEVIEW_API_KEY"):
        return FALLBACK_API_KEY

    try:
        resp = session.get(EVENTS_INDEX_URL, timeout=15)
        if resp.status_code == 200 and resp.text:
            token = _extract_token_from_text(resp.text)
            if token:
                logger.info("Acquired fresh Simpleview REST v2 token from events page.")
                return token

            soup = BeautifulSoup(resp.text, "html.parser")
            script_srcs: List[str] = []
            for script in soup.find_all("script", src=True):
                src_url = normalize_url(script.get("src"))
                if src_url and any(
                    k in src_url.lower()
                    for k in ("simpleview", "event", "calendar", "app", "bundle", "main")
                ):
                    if src_url not in script_srcs:
                        script_srcs.append(src_url)

            for script_url in script_srcs[:4]:
                try:
                    s_resp = session.get(script_url, timeout=10)
                    if s_resp.status_code == 200 and s_resp.text:
                        token = _extract_token_from_text(s_resp.text)
                        if token:
                            logger.info(
                                f"Acquired fresh Simpleview REST v2 token from script: {script_url}"
                            )
                            return token
                except Exception:
                    continue
    except Exception as e:
        logger.warning(f"Could not refresh Simpleview token dynamically: {e}")
    return FALLBACK_API_KEY


def fetch_http_page(url: str, timeout: int = 12) -> Optional[BeautifulSoup]:
    """Fetches and pre-cleans a Simpleview page via fast pooled HTTP GET."""
    try:
        session = get_thread_session()
        resp = session.get(url, timeout=timeout)
        if resp.status_code != 200 or not resp.text:
            return None
        soup = BeautifulSoup(clean_html_source(resp.text), "html.parser")
        if soup.find("div", id="main-content") or soup.find(
            "div", class_=re.compile(r"info-section")
        ):
            return soup
        return None
    except Exception:
        return None


def fetch_rendered_page(
    driver: webdriver.Chrome,
    url: str,
    wait_selector: Optional[str] = None,
    timeout: int = 10,
) -> Optional[BeautifulSoup]:
    """Navigates active headless driver to target URL and returns pre-treated BeautifulSoup DOM."""
    try:
        driver.get(url)
        if wait_selector:
            try:
                WebDriverWait(driver, timeout).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, wait_selector))
                )
            except Exception:
                pass

        cleaned_html = clean_html_source(driver.page_source)
        return BeautifulSoup(cleaned_html, "html.parser")
    except Exception as e:
        logger.warning(f"Selenium fallback failed for '{url}': {e}")
        return None


def coerce_api_docs(raw_docs: Any) -> List[Dict[str, Any]]:
    """Unwraps Simpleview REST API docs (handling nested {'count': N, 'docs': [...]}) into dicts."""
    if isinstance(raw_docs, dict):
        raw_docs = raw_docs.get("docs", [])
    if not isinstance(raw_docs, list):
        return []

    parsed_docs: List[Dict[str, Any]] = []
    for item in raw_docs:
        if isinstance(item, dict):
            parsed_docs.append(item)
        elif isinstance(item, str) and item.strip():
            try:
                obj = json.loads(item)
                if isinstance(obj, dict):
                    parsed_docs.append(obj)
            except Exception as e:
                logger.warning(
                    f"Skipping unparseable Simpleview doc string ({item[:80]!r}): {e}"
                )
    return parsed_docs


def extract_api_fallback_dates(api_docs: List[Dict[str, Any]]) -> List[str]:
    """Builds sorted unique ISO dates from Simpleview REST API records for a given event URL."""
    dates: List[str] = []
    for doc in api_docs:
        raw_d = doc.get("date") or doc.get("startDate")
        if raw_d:
            dt = pd.to_datetime(raw_d, errors="coerce")
            if pd.notnull(dt):
                dates.append(dt.strftime("%Y-%m-%d"))
    return sorted(set(dates))


def parse_detail_soup(
    url: str, det_soup: BeautifulSoup, api_group: List[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    """Parses a Simpleview detail BeautifulSoup DOM + API metadata into a normalized event dict."""
    primary_doc = api_group[0] if api_group else {}
    listing_meta = primary_doc.get("listing") or {}

    main_content = det_soup.find("div", id="main-content") or det_soup
    info_sec = main_content.find("div", class_=re.compile(r"info-section"))

    title: Optional[str] = None
    if info_sec:
        h1_tag = info_sec.find("h1")
        if h1_tag:
            title = h1_tag.get_text(strip=True)
    if not title:
        title = str(primary_doc.get("title") or "").strip() or None
    if not title:
        return None

    start_times: List[str] = []
    if info_sec:
        dd_list = info_sec.find_all("dd")
        if dd_list:
            raw_dates_str = (
                dd_list[1].get_text(" ", strip=True)
                if len(dd_list) > 1
                else dd_list[0].get_text(" ", strip=True)
            )
            recur_hint = primary_doc.get("recurType")
            start_times = parse_event_dates(raw_dates_str, frequency=recur_hint)

    api_dates = extract_api_fallback_dates(api_group)
    if api_dates:
        if not start_times or len(api_dates) > len(start_times):
            start_times = api_dates

    if not start_times:
        logger.warning(f"[SKIP] No valid dates parsed for '{title}' ({url}).")
        return None

    venue_name = DEFAULT_VENUE
    two_line = main_content.find("div", class_=re.compile(r"two-line-wrap"))
    if two_line and two_line.find("a"):
        venue_name = two_line.find("a").get_text(strip=True)
    elif listing_meta.get("title"):
        venue_name = str(listing_meta["title"]).strip()
    elif primary_doc.get("location"):
        venue_name = str(primary_doc["location"]).strip()
    elif any(kw in title for kw in [" at ", " At ", " AT ", " @ "]):
        split_parts = re.split(r" at | At | AT | @ ", title, maxsplit=1)
        if len(split_parts) > 1 and split_parts[1].strip():
            venue_name = split_parts[1].strip()

    address = DEFAULT_ADDRESS
    if two_line:
        spans = two_line.find_all("span")
        if len(spans) >= 2:
            st = spans[0].get_text(strip=True)
            ct = spans[1].get_text(strip=True)
            if st or ct:
                address = ", ".join(filter(None, [st, ct]))
    elif primary_doc.get("city"):
        city = str(primary_doc["city"]).strip()
        address = f"{city}, WA" if city else DEFAULT_ADDRESS

    desc_tab = main_content.find("div", id="descriptionTab") or main_content.find(
        "div", class_=re.compile(r"description")
    )
    description = (
        re.sub(r"\s+", " ", desc_tab.get_text(strip=True, separator=" "))
        if desc_tab
        else None
    )

    rich_text = fetch_event_text(main_content)
    big_desc = (
        re.sub(r"\s+", " ", rich_text) if rich_text else (description or title)
    )

    image_url: Optional[str] = None
    img_tag = main_content.find(
        "img", class_=re.compile(r"slide-img|loaded")
    ) or main_content.find("img", src=True)
    if img_tag and img_tag.get("src"):
        image_url = normalize_url(img_tag["src"])
    if not image_url:
        media_raw = primary_doc.get("media_raw")
        if isinstance(media_raw, list) and media_raw:
            first_media = media_raw[0]
            if isinstance(first_media, dict):
                image_url = normalize_url(first_media.get("mediaurl"))

    ticket_link: Optional[str] = None
    actions_div = main_content.find("div", class_=re.compile(r"bottom-actions"))
    if actions_div and actions_div.find("a", href=True):
        ticket_link = normalize_url(actions_div.find("a")["href"])

    return {
        "venue_name": venue_name,
        "address": address,
        "title": title,
        "start_time": start_times,
        "description": description or title,
        "big_desc": big_desc,
        "external_url": url,
        "image_url": image_url,
        "ticket_link": ticket_link,
    }


def fetch_and_parse_via_http(
    item: Tuple[str, List[Dict[str, Any]]]
) -> Tuple[str, Optional[Dict[str, Any]], bool]:
    """Worker task: attempts fast HTTP fetch + parse. Returns (url, record, needs_selenium)."""
    url, api_group = item
    soup = fetch_http_page(url)
    if soup is None:
        return (url, None, True)
    record = parse_detail_soup(url, soup, api_group)
    return (url, record, False)


def scraper(days_ahead: int = 30, max_workers: int = 10) -> pd.DataFrame:
    """Scrapes Visit Tacoma / Pierce County events using REST API queue and batch HTTP + lazy Chrome."""
    logger.info("Starting Visit Tacoma / Pierce County events scrape...")

    now = pd.Timestamp.now(tz="America/Los_Angeles")
    start_date = now.strftime("%Y-%m-%d")
    end_date = (now + pd.Timedelta(days=days_ahead)).strftime("%Y-%m-%d")
    limit = 500

    session = get_thread_session()
    session.headers.update({"X-Requested-With": "XMLHttpRequest"})

    token = get_fresh_simpleview_token(session)

    query_payload = {
        "filter": {
            "active": True,
            "$and": [
                {
                    "categories.catId": {
                        "$in": ["8", "10", "14", "9", "20", "13", "12", "11"]
                    }
                }
            ],
            "date_range": {
                "start": {"$date": f"{start_date}T08:00:00.000Z"},
                "end": {"$date": f"{end_date}T08:00:00.000Z"},
            },
        },
        "options": {
            "limit": limit,
            "skip": 0,
            "count": True,
            "castDocs": True,
            "fields": {
                "_id": 1,
                "location": 1,
                "date": 1,
                "startDate": 1,
                "endDate": 1,
                "recurrence": 1,
                "recurType": 1,
                "latitude": 1,
                "longitude": 1,
                "media_raw": 1,
                "recid": 1,
                "title": 1,
                "url": 1,
                "categories": 1,
                "accountId": 1,
                "city": 1,
                "region": 1,
                "listing.primary_category": 1,
                "listing.title": 1,
                "listing.url": 1,
                "listing.recid": 1,
                "listing.acctid": 1,
                "listing.region": 1,
                "listing.city": 1,
                "listing.rankname": 1,
            },
            "hooks": [],
            "sort": {"date": 1, "rank": 1, "title_sort": 1},
        },
    }

    api_url = (
        f"{BASE_URL}/includes/rest_v2/plugins_events_events_by_date/find/"
        f"?json={quote(json.dumps(query_payload, separators=(',', ':')))}&token={token}"
    )

    docs: List[Dict[str, Any]] = []
    try:
        response = session.get(api_url, timeout=15)
        response.raise_for_status()
        data = response.json()
        docs = coerce_api_docs(data.get("docs", []))
    except Exception as e:
        logger.warning(
            f"Simpleview REST API index failed ({e}); will fall back to HTML index."
        )

    logger.info(f"Retrieved {len(docs)} valid event records from REST API index.")

    docs_by_url: Dict[str, List[Dict[str, Any]]] = {}
    for doc in docs:
        full_url = normalize_url(doc.get("url"))
        if full_url:
            docs_by_url.setdefault(full_url, []).append(doc)

    driver = None
    try:
        if not docs_by_url:
            logger.info(
                "Falling back to rendered /events/ index page to discover event URLs..."
            )
            driver = webdriver.Chrome(options=get_headless_chrome_options())
            index_soup = fetch_rendered_page(
                driver, EVENTS_INDEX_URL, wait_selector="a[href*='/event/']"
            )
            if index_soup:
                for a in index_soup.find_all("a", href=re.compile(r"/event/[^/]+/\d+")):
                    full_url = normalize_url(a["href"])
                    if full_url:
                        docs_by_url.setdefault(full_url, [])
            logger.info(
                f"Discovered {len(docs_by_url)} event URLs from rendered HTML index."
            )

        if not docs_by_url:
            return pd.DataFrame()

        detail_items = sorted(docs_by_url.items(), key=lambda kv: kv[0])
        logger.info(
            f"Batch-fetching {len(detail_items)} event detail pages via HTTP (workers={max_workers})..."
        )

        events_by_url: Dict[str, Dict[str, Any]] = {}
        fallback_urls: List[str] = []

        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            for url, record, needs_selenium in executor.map(
                fetch_and_parse_via_http, detail_items
            ):
                if needs_selenium:
                    fallback_urls.append(url)
                elif record is not None:
                    events_by_url[url] = record

        logger.info(
            f"HTTP batch complete: {len(events_by_url)} parsed, {len(fallback_urls)} queued for Selenium fallback."
        )

        if fallback_urls:
            if driver is None:
                driver = webdriver.Chrome(options=get_headless_chrome_options())
            for url in fallback_urls:
                det_soup = fetch_rendered_page(
                    driver, url, wait_selector="#main-content, div.info-section"
                )
                if not det_soup:
                    continue
                record = parse_detail_soup(url, det_soup, docs_by_url[url])
                if record is not None:
                    events_by_url[url] = record

        events_data = [
            events_by_url[url]
            for url, _ in detail_items
            if url in events_by_url
        ]
        return pd.DataFrame(events_data) if events_data else pd.DataFrame()

    except Exception as e:
        logger.error(f"Error during Visit Tacoma / Pierce County scrape: {e}")
        return pd.DataFrame()
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass
        session.close()


if __name__ == "__main__":
    logger.info("Executing Visit Tacoma / Pierce County scraper task...")
    raw_df = scraper(days_ahead=30)

    if not raw_df.empty:
        pipeline = VividaIngestionPipeline(table_name="events")
        success = pipeline.run_standalone_pipeline(raw_df)
        if success:
            logger.info(
                "Visit Tacoma / Pierce County scraper execution completed successfully."
            )
        else:
            logger.error(
                "Visit Tacoma / Pierce County scraper pipeline execution reported errors."
            )
    else:
        logger.warning("No events scraped. Exiting task.")