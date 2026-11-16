"""
theSparTavern.py
Autonomous REST API Scraper for The Spar Tavern (Tacoma, WA).
Fetches Google Calendar v3 events and routes directly to Supabase.
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
    fetch_event_text,
    parse_event_dates,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("scrape_spar_tavern")

CALENDAR_ID = "1c3f32a7ad4284a3bbc1d990d9e1fc58ac1229629b3c07cf3bf2ed2a3d2600b0@group.calendar.google.com"
API_KEY = "AIzaSyDOtGM5jr8bNp1utVpG2_gSRH03RNGBkI8"
DEFAULT_VENUE = "The Spar Tavern"
DEFAULT_ADDRESS = "2121 N 30th St, Tacoma, WA 98403"
VENUE_URL = "https://thespartavern.com/live-music"
DEFAULT_IMAGE_URL = (
    "https://encrypted-tbn0.gstatic.com/images?q=tbn:ANd9GcTobzx5jiQULdt9mKXAkQDJxSQRpB40gdGk5q6Ix1Ld1Q&s"
)


def normalize_display_text(val: Optional[str]) -> Optional[str]:
    """Collapses whitespace while preserving original capitalization for display fields."""
    if not val:
        return None
    cleaned = re.sub(r"\s+", " ", str(val)).strip()
    return cleaned or None


def extract_clean_text_from_html(html_content: Optional[str]) -> Optional[str]:
    """Strips HTML formatting tags from raw API description text while preserving case."""
    if not html_content:
        return None
    soup = BeautifulSoup(html_content, "html.parser")
    text = fetch_event_text(soup) or soup.get_text(separator=" ", strip=True)
    return normalize_display_text(text)


def parse_gcal_local_dates(start_obj: dict, end_obj: dict) -> List[str]:
    """
    Normalizes Google Calendar start/end objects in America/Los_Angeles time
    into flat YYYY-MM-DD strings via parse_event_dates.
    """
    is_all_day = "date" in start_obj and "dateTime" not in start_obj
    raw_start = start_obj.get("dateTime") or start_obj.get("date")
    raw_end = end_obj.get("dateTime") or end_obj.get("date")

    if not raw_start:
        return []

    if is_all_day:
        dt_start_local = pd.to_datetime(raw_start, errors="coerce")
    else:
        dt_start_utc = pd.to_datetime(raw_start, errors="coerce", utc=True)
        dt_start_local = (
            dt_start_utc.tz_convert("America/Los_Angeles")
            if pd.notnull(dt_start_utc)
            else pd.NaT
        )

    if pd.isna(dt_start_local):
        return parse_event_dates(raw_start)

    start_str = dt_start_local.strftime("%Y-%m-%d")

    if raw_end:
        if is_all_day:
            dt_end_local = pd.to_datetime(raw_end, errors="coerce")
            if pd.notnull(dt_end_local):
                # Google Calendar all-day end dates are exclusive
                inclusive_end = dt_end_local - pd.Timedelta(days=1)
                if inclusive_end.date() > dt_start_local.date():
                    return parse_event_dates(
                        f"{start_str} - {inclusive_end.strftime('%Y-%m-%d')}"
                    )
        else:
            dt_end_utc = pd.to_datetime(raw_end, errors="coerce", utc=True)
            if pd.notnull(dt_end_utc):
                dt_end_local = dt_end_utc.tz_convert("America/Los_Angeles")
                if (dt_end_local - dt_start_local) >= pd.Timedelta(hours=18):
                    return parse_event_dates(
                        f"{start_str} - {dt_end_local.strftime('%Y-%m-%d')}"
                    )

    return parse_event_dates(start_str)


def scraper(days_ahead: int = 30) -> pd.DataFrame:
    """Queries Google Calendar v3 API for The Spar Tavern events and returns raw DataFrame."""
    logger.info("Starting The Spar Tavern Google Calendar API fetch...")

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

        raw_summary = normalize_display_text(event.get("summary"))
        raw_desc = extract_clean_text_from_html(event.get("description"))

        title = raw_summary or (raw_desc[:120] if raw_desc else None)
        if not title:
            continue

        start_times = parse_gcal_local_dates(
            event.get("start") or {}, event.get("end") or {}
        )
        if not start_times:
            continue

        location_str = normalize_display_text(event.get("location"))
        full_text = "\n".join(
            filter(None, [raw_summary, raw_desc, location_str]))

        events_data.append(
            {
                "venue_name": DEFAULT_VENUE,
                "address": DEFAULT_ADDRESS,
                "title": title,
                "start_time": start_times,
                "description": raw_desc or raw_summary,
                "big_desc": full_text or title,
                "external_url": VENUE_URL,
                "image_url": DEFAULT_IMAGE_URL,
                "ticket_link": None,
            }
        )

    return pd.DataFrame(events_data) if events_data else pd.DataFrame()


if __name__ == "__main__":
    logger.info("Executing The Spar Tavern scraper task...")
    raw_df = scraper(days_ahead=30)

    if not raw_df.empty:
        pipeline = VividaIngestionPipeline(table_name="events")
        success = pipeline.run_standalone_pipeline(raw_df)
        if success:
            logger.info(
                "The Spar Tavern scraper execution completed successfully."
            )
        else:
            logger.error(
                "The Spar Tavern scraper pipeline execution reported errors."
            )
    else:
        logger.warning("No events scraped. Exiting task.")
