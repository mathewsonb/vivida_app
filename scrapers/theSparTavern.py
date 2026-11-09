"""
theSparTavern.py
Autonomous REST API Scraper for The Spar Tavern (Tacoma, WA).
Fetches Google Calendar v3 events and routes directly to Supabase.
"""

import logging
import sys
from typing import Optional

from bs4 import BeautifulSoup
import pandas as pd
import requests

# Import consolidated vivida_etl package
from vivida_etl import (
    VividaIngestionPipeline,
    clean_str,
)

# Configure logging for VM execution
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
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


def extract_clean_text_from_html(html_content: Optional[str]) -> Optional[str]:
    """Strips HTML formatting tags from raw API description text."""
    if not html_content:
        return None
    soup = BeautifulSoup(html_content, "html.parser")
    return clean_str(soup.get_text(separator=" ", strip=True))


def scraper(days_ahead: int = 30) -> pd.DataFrame:
    """Queries Google Calendar v3 API for The Spar Tavern events and returns raw DataFrame."""
    logger.info("Starting The Spar Tavern Google Calendar API fetch...")

    # Calculate dynamic UTC timestamps for query window
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
        logger.error(f"Failed to query Google Calendar API endpoint: {e}")
        return pd.DataFrame()

    items = payload.get("items", [])
    logger.info(f"Retrieved {len(items)} event items from Google Calendar API.")

    events_data = []

    for event in items:
        if event.get("status") == "cancelled":
            continue

        raw_summary = clean_str(event.get("summary"))
        raw_desc = extract_clean_text_from_html(event.get("description"))

        title = f"{raw_summary} - {raw_desc}" if raw_summary and raw_desc else (raw_summary or raw_desc)
        if not title:
            continue

        item_data = {
            "venue_name": DEFAULT_VENUE,
            "address": DEFAULT_ADDRESS,
            "title": title,
            "start_time": None,
            "description": None,
            "big_desc": None,
            "external_url": event.get("htmlLink", "").strip() or None,
            "image_url": DEFAULT_IMAGE_URL,
            "ticket_link": None
        }

        # Date parsing
        start_obj = event.get("start", {})
        end_obj = event.get("end", {})

        raw_start = start_obj.get("dateTime") or start_obj.get("date")
        raw_end = end_obj.get("dateTime") or end_obj.get("date")

        if raw_start:
            dt_start = pd.to_datetime(raw_start, errors="coerce", format="mixed")
            dt_end = pd.to_datetime(raw_end, errors="coerce", format="mixed") if raw_end else None

            if pd.notnull(dt_start):
                start_str = dt_start.strftime("%Y-%m-%d")
                if pd.notnull(dt_end) and dt_end.date() > dt_start.date():
                    end_str = dt_end.strftime("%Y-%m-%d")
                    item_data["start_time"] = [start_str, end_str]
                else:
                    item_data["start_time"] = [start_str]

        # Combine summary and description for full text
        full_text = "\n".join(filter(None, [raw_summary, raw_desc, clean_str(event.get("location"))]))
        item_data["description"] = full_text or title
        item_data["big_desc"] = full_text or title

        events_data.append(item_data)

    if not events_data:
        return pd.DataFrame()

    return pd.DataFrame(events_data)


if __name__ == "__main__":
    logger.info("Executing The Spar Tavern scraper task...")
    raw_df = scraper(days_ahead=30)

    if not raw_df.empty:
        # Run autonomous pipeline transformation & Supabase upsert
        pipeline = VividaIngestionPipeline(table_name="events")
        success = pipeline.run_standalone_pipeline(raw_df)
        if success:
            logger.info("The Spar Tavern scraper execution completed successfully.")
        else:
            logger.error("The Spar Tavern scraper pipeline execution reported errors.")
    else:
        logger.warning("No events scraped. Exiting task.")