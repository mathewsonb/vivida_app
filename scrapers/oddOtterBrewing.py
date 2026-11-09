"""
scrape_odd_otter.py
Autonomous API Scraper for Odd Otter Brewing Company (Tacoma, WA).
Fetches events via Google Calendar v3 API and routes directly to Supabase.
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
logger = logging.getLogger("scrape_odd_otter")

CALENDAR_ID = "oddotterevents@gmail.com"
API_KEY = "AIzaSyDOtGM5jr8bNp1utVpG2_gSRH03RNGBkI8"
DEFAULT_VENUE = "Odd Otter Brewing Company"
DEFAULT_ADDRESS = "716 Pacific Ave, Tacoma, WA 98402"
DEFAULT_IMAGE_URL = (
    "https://d1w7312wesee68.cloudfront.net/Ow1NOZqxUuq1KeCZrrP0XFscbwjDFH6cmZejMaLWjQM/"
    "ext:webp/quality:85/preset:xxl/plain/s3://toast-sites-resources-prod/restaurantImages/"
    "c1a91c11-32d5-405a-a174-d6f05e89a0bb/a4375b5c04414b1886bd27233ee15b75.jpg"
)


def extract_clean_text_from_html(html_content: Optional[str]) -> Optional[str]:
    """Strips HTML formatting tags from raw API description text."""
    if not html_content:
        return None
    soup = BeautifulSoup(html_content, "html.parser")
    return clean_str(soup.get_text(separator=" ", strip=True))


def scraper(days_ahead: int = 30) -> pd.DataFrame:
    """Queries Google Calendar v3 API for Odd Otter events and returns structured DataFrame."""
    logger.info("Starting Odd Otter Brewing Google Calendar API fetch...")
    
    # Define time windows in valid ISO-8601 UTC format
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
        # Filter out cancelled events
        if event.get("status") == "cancelled":
            continue

        item_data = {
            "venue_name": DEFAULT_VENUE,
            "address": DEFAULT_ADDRESS,
            "title": None,
            "start_time": None,
            "description": None,
            "big_desc": None,
            "external_url": None,
            "image_url": DEFAULT_IMAGE_URL,
            "ticket_link": None
        }

        # 1. Title / Summary
        summary = event.get("summary")
        if summary:
            item_data["title"] = clean_str(summary)

        # 2. Date parsing (supporting both dateTime and date for all-day events)
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

        # 3. External HTML Link
        html_link = event.get("htmlLink")
        if html_link:
            item_data["external_url"] = html_link.strip()

        # 4. Description & Rich Text Cleaning
        raw_desc = event.get("description")
        cleaned_desc = extract_clean_text_from_html(raw_desc)
        
        if cleaned_desc:
            item_data["description"] = cleaned_desc
            item_data["big_desc"] = cleaned_desc
        else:
            item_data["description"] = item_data["title"]
            item_data["big_desc"] = item_data["title"]

        # Optional: Check for custom location inside event object
        loc_override = event.get("location")
        if loc_override and len(loc_override.strip()) > 5:
            item_data["address"] = clean_str(loc_override)

        events_data.append(item_data)

    if not events_data:
        return pd.DataFrame()

    return pd.DataFrame(events_data)


if __name__ == "__main__":
    logger.info("Executing Odd Otter Brewing scraper task...")
    raw_df = scraper(days_ahead=30)

    if not raw_df.empty:
        # Run autonomous pipeline transformation & Supabase upsert
        pipeline = VividaIngestionPipeline(table_name="events")
        success = pipeline.run_standalone_pipeline(raw_df)
        if success:
            logger.info("Odd Otter Brewing scraper execution completed successfully.")
        else:
            logger.error("Odd Otter Brewing scraper pipeline execution reported errors.")
    else:
        logger.warning("No events scraped. Exiting task.")