"""
theSparTavern.py
Autonomous REST API Scraper for The Spar Tavern (Tacoma, WA).
Fetches Google Calendar v3 events directly via REST API, applies title casing to performers/venues,
and routes records to VividaIngestionPipeline.
"""

import logging
import string
import sys
from typing import Dict, List, Optional

from bs4 import BeautifulSoup
import pandas as pd
import requests

from vivida_etl import (
    VividaIngestionPipeline,
    clean_str,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(filename)s:%(lineno)d] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("scrape_spar_tavern")

CALENDAR_ID = "1c3f32a7ad4284a3bbc1d990d9e1fc58ac1229629b3c07cf3bf2ed2a3d2600b0@group.calendar.google.com"
API_KEY = "AIzaSyDOtGM5jr8bNp1utVpG2_gSRH03RNGBkI8"
DEFAULT_VENUE = "The Spar Tavern"
DEFAULT_ADDRESS = "2121 N 30th St, Tacoma, WA 98403"
DEFAULT_IMAGE_URL = (
    "https://encrypted-tbn0.gstatic.com/images?q=tbn:ANd9GcTobzx5jiQULdt9mKXAkQDJxSQRpB40gdGk5q6Ix1Ld1Q&s"
)


def format_title_case(text: Optional[str]) -> Optional[str]:
    """Formats raw strings into proper title casing while correctly handling apostrophes."""
    if not text:
        return None
    clean_text = clean_str(text)
    if not clean_text:
        return None
    return " ".join(string.capwords(word) for word in clean_text.split(" "))


def extract_clean_text_from_html(html_content: Optional[str]) -> Optional[str]:
    """Strips HTML formatting tags from raw API description text."""
    if not html_content:
        return None
    soup = BeautifulSoup(html_content, "html.parser")
    return clean_str(soup.get_text(separator=" ", strip=True))


def scraper(days_ahead: int = 30) -> pd.DataFrame:
    """Queries Google Calendar v3 API for The Spar Tavern events and returns populated DataFrame."""
    logger.info("Starting The Spar Tavern Google Calendar API fetch...")

    now = pd.Timestamp.now(tz="UTC")
    time_min = now.strftime("%Y-%m-%dT00:00:00Z")
    time_max = (now + pd.Timedelta(days=days_ahead)).strftime("%Y-%m-%dT23:59:59Z")
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

    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

    try:
        response = requests.get(api_url, headers=headers, timeout=15)
        response.raise_for_status()
        payload = response.json()
    except Exception as e:
        logger.error(f"Failed to query Google Calendar API endpoint: {e}", exc_info=True)
        return pd.DataFrame()

    items = payload.get("items", [])
    logger.info(f"Retrieved {len(items)} event items from Google Calendar API.")

    events_data: List[Dict[str, Optional[str]]] = []

    for event in items:
        if event.get("status") == "cancelled":
            continue

        raw_summary = clean_str(event.get("summary"))
        raw_desc = extract_clean_text_from_html(event.get("description"))

        # Apply Title Casing to performer/event titles
        title_raw = raw_summary or raw_desc
        title = format_title_case(title_raw)
        if not title:
            continue

        # Extract start and end times
        start_obj = event.get("start", {})
        end_obj = event.get("end", {})

        is_all_day = "date" in start_obj and "dateTime" not in start_obj
        raw_start = start_obj.get("dateTime") or start_obj.get("date")
        raw_end = end_obj.get("dateTime") or end_obj.get("date")

        start_time_seq = None
        if raw_start:
            dt_start = pd.to_datetime(raw_start, errors="coerce")
            dt_end = pd.to_datetime(raw_end, errors="coerce") if raw_end else None

            if pd.notnull(dt_start):
                start_str = dt_start.strftime("%Y-%m-%d")
                
                # Google Calendar all-day events use exclusive end dates
                if is_all_day and pd.notnull(dt_end):
                    dt_end = dt_end - pd.Timedelta(days=1)

                if pd.notnull(dt_end) and dt_end.date() > dt_start.date():
                    end_str = dt_end.strftime("%Y-%m-%d")
                    start_time_seq = [start_str, end_str]
                else:
                    start_time_seq = [start_str]

        if not start_time_seq:
            continue

        # Build clean description payload and apply title casing to venue overrides
        desc_text = raw_desc or title_raw
        location_override = format_title_case(event.get("location"))
        venue = location_override if location_override else DEFAULT_VENUE

        item_data = {
            "venue_name": venue,
            "address": DEFAULT_ADDRESS,
            "title": title,
            "start_time": start_time_seq,
            "description": desc_text,
            "big_desc": desc_text,
            "external_url": event.get("htmlLink", "").strip() or None,
            "image_url": DEFAULT_IMAGE_URL,
            "ticket_link": None
        }

        events_data.append(item_data)
        logger.info(f"  Title      : {item_data['title']}")
        logger.info(f"  Venue      : {item_data['venue_name']}")
        logger.info(f"  Start Time : {item_data['start_time']}")

    if not events_data:
        return pd.DataFrame()

    return pd.DataFrame(events_data)


if __name__ == "__main__":
    logger.info("Executing The Spar Tavern scraper task...")
    raw_df = scraper(days_ahead=30)

    if not raw_df.empty:
        logger.info("==================================================================")
        logger.info(f"[PIPELINE HANDOFF] Passing {len(raw_df)} event records to VividaIngestionPipeline...")
        logger.info("==================================================================")

        pipeline = VividaIngestionPipeline(table_name="events")
        success = pipeline.run_standalone_pipeline(raw_df)

        if success:
            logger.info("[PIPELINE SUCCESS] The Spar Tavern scraper execution completed successfully.")
        else:
            logger.error("[PIPELINE ERROR] The Spar Tavern scraper pipeline execution reported errors.")
    else:
        logger.warning("[EXIT] No events scraped. Pipeline handoff skipped.")