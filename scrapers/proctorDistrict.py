"""
proctorDistrict.py
Autonomous REST API Scraper for The Proctor District (Tacoma, WA).
Fetches Base44 API event entities and routes directly to Supabase.
"""

import logging
import re
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
logger = logging.getLogger("scrape_proctor_district")

API_URL = "https://base44.app/api/apps/6911cf52c9f849cb61177089/entities/Event?sort=event_date&limit=500"
DEFAULT_VENUE = "Proctor District"
DEFAULT_ADDRESS = "Proctor District, Tacoma, WA 98407"


def clean_html_text(raw_text: Optional[str]) -> Optional[str]:
    """Strips HTML formatting tags and normalizes whitespace."""
    if not raw_text:
        return None
    soup = BeautifulSoup(raw_text, "html.parser")
    extracted = soup.get_text(separator=" ", strip=True)
    return clean_str(re.sub(r"\s+", " ", extracted))


def parse_address_string(loc_raw: Optional[str]) -> Optional[str]:
    """Extracts address portion from location string (splitting on em-dash or hyphen)."""
    if not loc_raw:
        return DEFAULT_ADDRESS
    
    # Split on em-dash (—) or en-dash/hyphen (-)
    parts = re.split(r"[—–-]", loc_raw)
    cleaned = parts[-1].strip() if len(parts) > 1 else loc_raw.strip()
    
    if not cleaned:
        return DEFAULT_ADDRESS
    
    # Append Tacoma context if missing
    if "Tacoma" not in cleaned and "WA" not in cleaned:
        return f"{cleaned}, Tacoma, WA"
    return cleaned


def scraper() -> pd.DataFrame:
    """Queries Base44 REST API for Proctor District events and returns raw DataFrame."""
    logger.info(f"Starting Proctor District API fetch from Base44...")
    
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    try:
        response = requests.get(API_URL, headers=headers, timeout=15)
        response.raise_for_status()
        payload = response.json()
    except Exception as e:
        logger.error(f"Failed to query Base44 API endpoint: {e}")
        return pd.DataFrame()

    # Base44 envelope guard (handles list or envelope dict)
    records = payload if isinstance(payload, list) else payload.get("data") or payload.get("items", [])
    logger.info(f"Retrieved {len(records)} raw event records from Base44 API.")

    events_data = []
    now_utc = pd.Timestamp.now(tz="UTC")

    for record in records:
        raw_date = record.get("event_date")
        if not raw_date:
            continue

        # Parse start_time with UTC timezone awareness
        start_time = pd.to_datetime(raw_date, errors="coerce", format="mixed", utc=True)
        if pd.isna(start_time):
            continue

        # Filter out past events
        if start_time < now_utc:
            continue

        item_data = {
            "venue_name": clean_str(record.get("business_name")) or DEFAULT_VENUE,
            "address": parse_address_string(record.get("location")),
            "title": clean_str(record.get("title")),
            "start_time": [start_time.strftime("%Y-%m-%d")],
            "description": None,
            "big_desc": None,
            "external_url": record.get("event_website") or record.get("registration_url"),
            "image_url": record.get("image_url"),
            "ticket_link": record.get("registration_url")
        }

        # Clean HTML markup from description field
        cleaned_desc = clean_html_text(record.get("description"))
        if cleaned_desc:
            item_data["description"] = cleaned_desc
            item_data["big_desc"] = cleaned_desc
        else:
            item_data["description"] = item_data["title"]
            item_data["big_desc"] = item_data["title"]

        events_data.append(item_data)

    if not events_data:
        return pd.DataFrame()

    return pd.DataFrame(events_data)


if __name__ == "__main__":
    logger.info("Executing Proctor District scraper task...")
    raw_df = scraper()

    if not raw_df.empty:
        # Run autonomous pipeline transformation & Supabase upsert
        pipeline = VividaIngestionPipeline(table_name="events")
        success = pipeline.run_standalone_pipeline(raw_df)
        if success:
            logger.info("Proctor District scraper execution completed successfully.")
        else:
            logger.error("Proctor District scraper pipeline execution reported errors.")
    else:
        logger.warning("No events scraped. Exiting task.")