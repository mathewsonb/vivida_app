"""
tacomaArtsMonth.py
Autonomous REST API Scraper for Tacoma Arts Monthly (Time.ly Engine).
Fetches calendar events via Time.ly API and routes directly to Supabase.
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
logger = logging.getLogger("scrape_tacoma_arts_monthly")

CALENDAR_ID = 54705487
API_KEY = "c6e5e0363b5925b28552de8805464c66f25ba0ce"
BASE_URL = f"https://calendar.time.ly/api/calendars/{CALENDAR_ID}"
DEFAULT_VENUE = "Tacoma Arts Monthly"
DEFAULT_ADDRESS = "Tacoma, WA"


def clean_html_text(raw_html: Optional[str]) -> Optional[str]:
    """Strips HTML tags and normalizes whitespace in raw description strings."""
    if not raw_html:
        return None
    soup = BeautifulSoup(raw_html, "html.parser")
    return clean_str(soup.get_text(separator=" ", strip=True))


def parse_venue_address(venue_dict: dict) -> Optional[str]:
    """Safely extracts formatted address string from Time.ly venue taxonomy."""
    if not venue_dict:
        return None

    address = venue_dict.get("address", "")
    city = venue_dict.get("city", "")
    postal_code = venue_dict.get("postal_code", "")

    if street := address.split(",")[0].strip() if address else None:
        parts = [street]
        if city:
            parts.append(city)
        if postal_code:
            parts.append(f"WA {postal_code}")
        elif "WA" not in parts[-1]:
            parts.append("WA")
        return ", ".join(parts)

    return address or None


def scraper(days_ahead: int = 30) -> pd.DataFrame:
    """Queries Time.ly API for Tacoma Arts Monthly events and returns raw DataFrame."""
    logger.info("Starting Tacoma Arts Monthly Time.ly API fetch...")

    # Calculate dynamic UTC timestamps for query window
    now = pd.Timestamp.now(tz="UTC")
    start_utc = int(now.timestamp())
    end_utc = int((now + pd.Timedelta(days=days_ahead)).timestamp())

    params = {
        "timezone": "America/Los_Angeles",
        "view": "tile",
        "start_date_utc": str(start_utc),
        "end_date_utc": str(end_utc),
        "per_page": "500",
        "page": "1"
    }

    headers = {
        "accept": "application/json, text/plain, */*",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "x-api-key": API_KEY,
        "referer": "https://calendar.time.ly/u1mdrp81/tile"
    }

    try:
        response = requests.get(f"{BASE_URL}/events", params=params, headers=headers, timeout=15)
        response.raise_for_status()
        data = response.json()
    except Exception as e:
        logger.error(f"Failed to query Time.ly events API: {e}")
        return pd.DataFrame()

    items = data.get("data", {}).get("items", []) if isinstance(data, dict) else []
    logger.info(f"Retrieved {len(items)} events from Time.ly API list endpoint.")

    events_data = []

    for item in items:
        event_id = item.get("id")
        if not event_id:
            continue

        # Fetch detail JSON for full description & taxonomies
        detail_url = f"{BASE_URL}/events/{event_id}"
        try:
            det_resp = requests.get(detail_url, headers=headers, timeout=10)
            det_resp.raise_for_status()
            event_detail = det_resp.json().get("data", {})
        except Exception as e:
            logger.warning(f"Failed to fetch detail JSON for event ID {event_id}: {e}")
            event_detail = item

        item_data = {
            "venue_name": DEFAULT_VENUE,
            "address": DEFAULT_ADDRESS,
            "title": clean_str(item.get("title")),
            "start_time": None,
            "description": None,
            "big_desc": None,
            "external_url": item.get("url", "").strip() or None,
            "image_url": None,
            "ticket_link": None
        }

        # Date parsing
        raw_start = item.get("start_datetime")
        raw_end = item.get("end_datetime")

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

        # Venue Taxonomies parsing
        taxonomies = event_detail.get("taxonomies", {})
        venue_list = taxonomies.get("taxonomy_venue", [])
        if venue_list and len(venue_list) > 0:
            venue_obj = venue_list[0]
            if venue_name := venue_obj.get("title"):
                item_data["venue_name"] = clean_str(venue_name)
            if parsed_addr := parse_venue_address(venue_obj):
                item_data["address"] = parsed_addr

        # Description fields cleaning
        raw_short = event_detail.get("description_short") or item.get("description_short")
        raw_long = event_detail.get("description") or item.get("description")

        cleaned_short = clean_html_text(raw_short)
        cleaned_long = clean_html_text(raw_long)

        item_data["description"] = cleaned_short or cleaned_long or item_data["title"]
        item_data["big_desc"] = cleaned_long or cleaned_short or item_data["title"]

        # Image parsing
        images = event_detail.get("images", []) or item.get("images", [])
        if images and len(images) > 0:
            img_sizes = images[0].get("sizes", {})
            full_img = img_sizes.get("full", {}).get("url") or img_sizes.get("medium", {}).get("url")
            if full_img:
                item_data["image_url"] = full_img.strip()

        # Ticket Link parsing
        if event_detail.get("ticket_url"):
            item_data["ticket_link"] = event_detail["ticket_url"].strip()

        events_data.append(item_data)

    if not events_data:
        return pd.DataFrame()

    return pd.DataFrame(events_data)


if __name__ == "__main__":
    logger.info("Executing Tacoma Arts Monthly scraper task...")
    raw_df = scraper(days_ahead=30)

    if not raw_df.empty:
        # Run autonomous pipeline transformation & Supabase upsert
        pipeline = VividaIngestionPipeline(table_name="events")
        success = pipeline.run_standalone_pipeline(raw_df)
        if success:
            logger.info("Tacoma Arts Monthly scraper execution completed successfully.")
        else:
            logger.error("Tacoma Arts Monthly scraper pipeline execution reported errors.")
    else:
        logger.warning("No events scraped. Exiting task.")