"""
oddOtterBrewing.py
Autonomous API Scraper for Odd Otter Brewing Company (Tacoma, WA).
Fetches events via Google Calendar v3 API and routes directly to Supabase.
"""

import logging
import re
import sys
from typing import List, Optional

from bs4 import BeautifulSoup
import pandas as pd
import requests

from vivida_etl import (
    VividaIngestionPipeline,
    parse_event_dates,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("scrape_odd_otter")

CALENDAR_ID = "oddotterevents@gmail.com"
API_KEY = "AIzaSyDOtGM5jr8bNp1utVpG2_gSRH03RNGBkI8"
DEFAULT_VENUE = "Odd Otter Brewing Company"
DEFAULT_ADDRESS = "716 Pacific Ave, Tacoma, WA 98402"
VENUE_URL = "https://oddotterbeer.com/"
DEFAULT_IMAGE_URL = (
    "https://d1w7312wesee68.cloudfront.net/Ow1NOZqxUuq1KeCZrrP0XFscbwjDFH6cmZejMaLWjQM/"
    "ext:webp/quality:85/preset:xxl/plain/s3://toast-sites-resources-prod/restaurantImages/"
    "c1a91c11-32d5-405a-a174-d6f05e89a0bb/a4375b5c04414b1886bd27233ee15b75.jpg"
)


def normalize_whitespace(text: Optional[str]) -> Optional[str]:
    """Collapses repeated whitespace while preserving original capitalization."""
    if not text:
        return None
    cleaned = re.sub(r"\s+", " ", str(text)).strip()
    return cleaned or None


def extract_clean_text_from_html(html_content: Optional[str]) -> Optional[str]:
    """Strips HTML formatting tags from raw API description text without lowercasing."""
    if not html_content:
        return None
    soup = BeautifulSoup(html_content, "html.parser")
    return normalize_whitespace(soup.get_text(separator=" ", strip=True))


def parse_gcal_dates(start_obj: dict, end_obj: dict) -> List[str]:
    """
    Normalizes Google Calendar start/end objects into flat YYYY-MM-DD strings
    following Vivida's continuous-range endpoint rule.
    """
    is_all_day = "date" in start_obj and "dateTime" not in start_obj
    raw_start = start_obj.get("dateTime") or start_obj.get("date")
    raw_end = end_obj.get("dateTime") or end_obj.get("date")

    if not raw_start:
        return []

    dt_start = pd.to_datetime(raw_start, errors="coerce")
    if pd.isna(dt_start):
        return parse_event_dates(raw_start)

    start_iso = dt_start.strftime("%Y-%m-%d")

    if raw_end:
        dt_end = pd.to_datetime(raw_end, errors="coerce")
        if pd.notnull(dt_end):
            # Google Calendar all-day end.date is exclusive (e.g., 1-day event on Nov 21 ends Nov 22)
            if is_all_day:
                dt_end = dt_end - pd.Timedelta(days=1)

            # Treat as a continuous multi-day range only if it spans beyond a single overnight show
            spans_multiple_days = (
                dt_end.date() > dt_start.date()
                and (is_all_day or (dt_end - dt_start) >= pd.Timedelta(hours=18))
            )
            if spans_multiple_days:
                end_iso = dt_end.strftime("%Y-%m-%d")
                return parse_event_dates(f"{start_iso} - {end_iso}")

    return parse_event_dates(start_iso)


def scraper(days_ahead: int = 30) -> pd.DataFrame:
    """Queries Google Calendar v3 API for Odd Otter events and returns structured DataFrame."""
    logger.info("Starting Odd Otter Brewing Google Calendar API fetch...")

    now = pd.Timestamp.now(tz="UTC")
    time_min = now.strftime("%Y-%m-%dT00:00:00Z")
    time_max = (now + pd.Timedelta(days=days_ahead)
                ).strftime("%Y-%m-%dT23:59:59Z")
    limit = 250

    api_url = (
        f"https://clients6.google.com/calendar/v3/calendars/{CALENDAR_ID}/events"
        f"?calendarId={CALENDAR_ID}"
        f"&singleEvents=true"
        f"&orderBy=startTime"
        f"&timeZone=America%2FLos_Angeles"
        f"&maxResults={limit}"
        f"&sanitizeHtml=true"
        f"&timeMin={time_min}"
        f"&timeMax={time_max}"
        f"&key={API_KEY}"
    )

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }

    try:
        response = requests.get(api_url, headers=headers, timeout=15)
        response.raise_for_status()
        payload = response.json()
    except Exception as e:
        logger.error(f"Failed to query Google Calendar API endpoint: {e}")
        return pd.DataFrame()

    items = payload.get("items", [])
    logger.info(
        f"Retrieved {len(items)} event items from Google Calendar API.")

    events_data = []

    for event in items:
        if event.get("status") == "cancelled":
            continue

        title = normalize_whitespace(event.get("summary"))
        if not title:
            continue

        start_times = parse_gcal_dates(
            event.get("start") or {}, event.get("end") or {}
        )
        if not start_times:
            continue

        cleaned_desc = extract_clean_text_from_html(event.get("description"))

        address = DEFAULT_ADDRESS
        loc_override = normalize_whitespace(event.get("location"))
        if loc_override and len(loc_override) > 5:
            address = loc_override

        events_data.append(
            {
                "venue_name": DEFAULT_VENUE,
                "address": address,
                "title": title,
                "start_time": start_times,
                "description": cleaned_desc or title,
                "big_desc": cleaned_desc or title,
                "external_url": VENUE_URL,
                "image_url": DEFAULT_IMAGE_URL,
                "ticket_link": None,
            }
        )

    return pd.DataFrame(events_data) if events_data else pd.DataFrame()


if __name__ == "__main__":
    logger.info("Executing Odd Otter Brewing scraper task...")
    raw_df = scraper(days_ahead=30)

    if not raw_df.empty:
        pipeline = VividaIngestionPipeline(table_name="events")
        success = pipeline.run_standalone_pipeline(raw_df)
        if success:
            logger.info(
                "Odd Otter Brewing scraper execution completed successfully.")
        else:
            logger.error(
                "Odd Otter Brewing scraper pipeline execution reported errors.")
    else:
        logger.warning("No events scraped. Exiting task.")
