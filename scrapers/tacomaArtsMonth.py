"""
tacomaArtsMonth.py
Autonomous REST API Scraper for Tacoma Arts Month (Tacoma, WA - Time.ly Calendar API).
Queries dynamic rolling date windows, batch-fetches event detail payloads concurrently
(workers=10), strips HTML descriptions, normalizes [start, end] date ranges,
and hands off clean records to VividaIngestionPipeline.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
import html
import logging
import re
import sys
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from vivida_etl import (
    VividaIngestionPipeline,
    parse_event_dates,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(filename)s:%(lineno)d] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("scrape_tacoma_arts_month")

CALENDAR_ID = 54705487
BASE_API_URL = f"https://calendar.time.ly/api/calendars/{CALENDAR_ID}/events"
TIMELY_API_KEY = "c6e5e0363b5925b28552de8805464c66f25ba0ce"
DEFAULT_VENUE = "Tacoma Arts Month"
DEFAULT_ADDRESS = "Tacoma, WA"

HEADERS = {
    "accept": "application/json, text/plain, */*",
    "accept-language": "en-US,en;q=0.9",
    "referer": "https://calendar.time.ly/u1mdrp81/tile?timely_id=timely-iframe-embed-0-event-popup&popup=1",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
    ),
    "x-api-key": TIMELY_API_KEY,
}


def normalize_ws(text: Optional[str]) -> str:
    if not text:
        return ""
    unescaped = html.unescape(str(text)).replace("\xa0", " ")
    return re.sub(r"\s+", " ", unescaped).strip()


def clean_html_to_text(raw_html: Optional[str]) -> str:
    if not raw_html:
        return ""
    soup = BeautifulSoup(html.unescape(str(raw_html)), "html.parser")
    return normalize_ws(soup.get_text(" ", strip=True))


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


def extract_timely_dates(item: Dict[str, Any], event_detail: Dict[str, Any]) -> List[str]:
    """Extracts [start] or [start, end] ISO dates per Vivida's 3-rule date standard."""
    start_raw = item.get(
        "start_datetime") or event_detail.get("start_datetime")
    end_raw = item.get("end_datetime") or event_detail.get("end_datetime")

    start_dt = pd.to_datetime(
        start_raw, errors="coerce", format="mixed") if start_raw else pd.NaT
    end_dt = pd.to_datetime(end_raw, errors="coerce",
                            format="mixed") if end_raw else pd.NaT

    if pd.notnull(start_dt):
        s_str = start_dt.strftime("%Y-%m-%d")
        if pd.notnull(end_dt):
            e_str = end_dt.strftime("%Y-%m-%d")
            if e_str > s_str:
                return [s_str, e_str]
        return [s_str]

    if start_raw:
        return parse_event_dates(str(start_raw))
    return []


def extract_venue_and_address(
    item: Dict[str, Any], event_detail: Dict[str, Any]
) -> tuple[str, str]:
    """Resolves venue_name and full street address from Time.ly taxonomy or item venue object."""
    venue_obj: Dict[str, Any] = {}
    taxonomies = event_detail.get("taxonomies")
    if isinstance(taxonomies, dict):
        venues = taxonomies.get("taxonomy_venue")
        if isinstance(venues, list) and venues and isinstance(venues[0], dict):
            venue_obj = venues[0]

    if not venue_obj and isinstance(item.get("venue"), dict):
        venue_obj = item["venue"]

    venue_name = normalize_ws(venue_obj.get(
        "title") or venue_obj.get("name")) or DEFAULT_VENUE

    raw_addr = normalize_ws(venue_obj.get("address"))
    city = normalize_ws(venue_obj.get("city"))
    state = normalize_ws(venue_obj.get("province")
                         or venue_obj.get("state")) or "WA"
    zip_code = normalize_ws(venue_obj.get(
        "postal_code") or venue_obj.get("zip"))

    if raw_addr:
        street = raw_addr.split(",")[0].strip()
        if city and zip_code:
            address = f"{street}, {city}, {state} {zip_code}"
        elif city:
            address = f"{street}, {city}, {state}"
        else:
            address = raw_addr
    elif city:
        address = f"{city}, {state} {zip_code}".strip()
    else:
        address = DEFAULT_ADDRESS

    return venue_name, address


def extract_image_url(item: Dict[str, Any], event_detail: Dict[str, Any]) -> Optional[str]:
    for source in (event_detail.get("images"), item.get("images")):
        if isinstance(source, list) and source and isinstance(source[0], dict):
            sizes = source[0].get("sizes")
            if isinstance(sizes, dict):
                for key in ("full", "large", "medium", "thumbnail"):
                    url = sizes.get(key, {}).get("url") if isinstance(
                        sizes.get(key), dict) else None
                    if url:
                        return str(url).strip()
            if source[0].get("url"):
                return str(source[0]["url"]).strip()
    return None


def fetch_and_parse_event(
    session: requests.Session, item: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """Fetches full Time.ly event detail JSON and normalizes a single record."""
    event_id = item.get("id")
    event_detail: Dict[str, Any] = {}

    if event_id:
        try:
            resp = session.get(f"{BASE_API_URL}/{event_id}", timeout=15)
            if resp.status_code == 200:
                payload = resp.json()
                if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
                    event_detail = payload["data"]
        except Exception as err:
            logger.debug(
                f"Detail fetch fallback for event_id={event_id}: {err}")

    title = normalize_ws(event_detail.get("title") or item.get("title"))
    if not title:
        return None

    start_time = extract_timely_dates(item, event_detail)
    if not start_time:
        logger.warning(f"[SKIP] No valid dates parsed for '{title}'.")
        return None

    venue_name, address = extract_venue_and_address(item, event_detail)

    big_desc = clean_html_to_text(
        event_detail.get("description") or item.get("description")
    )
    short_desc = clean_html_to_text(
        event_detail.get("description_short") or item.get("description_short")
    )
    description = short_desc or (big_desc[:280] if big_desc else title)
    big_desc = big_desc or description

    external_url = normalize_ws(
        event_detail.get("url") or item.get("url")) or None
    image_url = extract_image_url(item, event_detail)

    ticket_link = None
    ticket_url = normalize_ws(
        event_detail.get("ticket_url")
        or event_detail.get("cost_external_url")
        or item.get("ticket_url")
    )
    if ticket_url:
        ticket_link = ticket_url
    elif str(event_detail.get("can_purchase_tickets", "")).lower() == "true":
        ticket_link = external_url

    return {
        "venue_name": venue_name,
        "address": address,
        "title": title,
        "start_time": start_time,
        "description": description,
        "big_desc": big_desc,
        "external_url": external_url,
        "image_url": image_url,
        "ticket_link": ticket_link,
    }


def scraper(days_ahead: int = 120) -> pd.DataFrame:
    """Scrapes Tacoma Arts Month events via Time.ly JSON REST API across a rolling date window."""
    tz = ZoneInfo("America/Los_Angeles")
    now_local = datetime.now(tz)
    start_day = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    end_day = start_day + timedelta(days=days_ahead)

    start_utc = int(start_day.timestamp())
    end_utc = int(end_day.timestamp())

    logger.info(
        f"[START] Querying Time.ly API (calendar={CALENDAR_ID}) from "
        f"{start_day.strftime('%Y-%m-%d')} to {end_day.strftime('%Y-%m-%d')}..."
    )

    session = build_http_session(pool_size=12)
    items: List[Dict[str, Any]] = []
    seen_ids: set[Any] = set()
    page = 1

    while True:
        params = {
            "timezone": "America/Los_Angeles",
            "view": "tile",
            "start_date_utc": str(start_utc),
            "end_date_utc": str(end_utc),
            "per_page": "200",
            "page": str(page),
        }
        try:
            resp = session.get(BASE_API_URL, params=params, timeout=20)
            resp.raise_for_status()
            payload = resp.json()
        except Exception as e:
            logger.error(
                f"[FETCH ERROR] Time.ly API listing failed on page {page}: {e}")
            break

        page_items = (
            payload.get("data", {}).get("items", [])
            if isinstance(payload, dict)
            else (payload if isinstance(payload, list) else [])
        )
        if not page_items:
            break

        new_count = 0
        for it in page_items:
            eid = it.get(
                "id") or f"{it.get('title')}_{it.get('start_datetime')}"
            if eid not in seen_ids:
                seen_ids.add(eid)
                items.append(it)
                new_count += 1

        logger.info(
            f"[LISTING PAGE {page}] Retrieved {new_count} unique event items.")
        if new_count == 0 or len(page_items) < 200:
            break
        page += 1

    if not items:
        logger.warning("[PARSER WARNING] No events returned from Time.ly API.")
        return pd.DataFrame()

    logger.info(
        f"Batch-fetching {len(items)} Time.ly event detail payloads concurrently (workers=10)..."
    )

    raw_records: List[Dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [
            executor.submit(fetch_and_parse_event, session, item)
            for item in items
        ]
        for fut in as_completed(futures):
            rec = fut.result()
            if rec:
                raw_records.append(rec)

    logger.info(
        f"[PARSER SUCCESS] Normalized {len(raw_records)} valid Tacoma Arts Month records.")
    return pd.DataFrame(raw_records) if raw_records else pd.DataFrame()


if __name__ == "__main__":
    logger.info("Executing Tacoma Arts Month scraper task...")
    raw_df = scraper()

    if not raw_df.empty:
        pipeline = VividaIngestionPipeline(table_name="events")
        success = pipeline.run_standalone_pipeline(raw_df)
        if success:
            logger.info(
                "[PIPELINE SUCCESS] Tacoma Arts Month scraper completed successfully.")
        else:
            logger.error(
                "[PIPELINE ERROR] Tacoma Arts Month scraper pipeline execution reported errors.")
    else:
        logger.warning("[EXIT] No events scraped. Pipeline handoff skipped.")
