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
)

# Configure logging for VM execution
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(filename)s:%(lineno)d] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("scrape_proctor_district")

API_URL = "https://base44.app/api/apps/6911cf52c9f849cb61177089/entities/Event?sort=event_date&limit=500"
DEFAULT_VENUE = "Proctor District"
DEFAULT_ADDRESS = "Proctor District, Tacoma, WA 98407"


def clean_html_text(raw_text: Optional[str]) -> Optional[str]:
    """Strips HTML formatting tags and normalizes whitespace while preserving text casing."""
    if not raw_text:
        return None
    soup = BeautifulSoup(raw_text, "html.parser")
    extracted = soup.get_text(separator=" ", strip=True)
    return re.sub(r"\s+", " ", extracted) if extracted else None


def parse_address_string(loc_raw: Optional[str]) -> Optional[str]:
    """Extracts address portion from location string (splitting on em-dash or hyphen)."""
    if not loc_raw or pd.isna(loc_raw):
        return DEFAULT_ADDRESS
    
    # Split on em-dash (—) or en-dash/hyphen (-)
    parts = re.split(r"[—–-]", str(loc_raw))
    cleaned = parts[-1].strip() if len(parts) > 1 else str(loc_raw).strip()
    
    if not cleaned:
        return DEFAULT_ADDRESS
    
    # Append Tacoma context if missing
    if "Tacoma" not in cleaned and "WA" not in cleaned:
        return f"{cleaned}, Tacoma, WA"
    return cleaned


def scraper() -> pd.DataFrame:
    """Queries Base44 REST API for Proctor District events and returns raw DataFrame."""
    logger.info("==================================================================")
    logger.info("[START] Executing Base44 REST API fetch for Proctor District...")
    logger.info("==================================================================")
    
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    try:
        response = requests.get(API_URL, headers=headers, timeout=15)
        response.raise_for_status()
        payload = response.json()
    except Exception as e:
        logger.error(f"[API ERROR] Failed to query Base44 API endpoint: {e}")
        return pd.DataFrame()

    # Base44 envelope guard (handles list or envelope dict)
    records = payload if isinstance(payload, list) else payload.get("data") or payload.get("items", [])
    logger.info(f"[PARSER SUCCESS] Retrieved {len(records)} raw event records from Base44 API.")

    events_data = []
    # Local timezone threshold (start of current day in Pacific Time)
    start_of_today = pd.Timestamp.now(tz="America/Los_Angeles").floor("D")

    for idx, record in enumerate(records, start=1):
        raw_date = record.get("event_date")
        if not raw_date:
            continue

        # Parse start_time with timezone conversion
        start_time = pd.to_datetime(raw_date, errors="coerce", format="mixed")
        if pd.isna(start_time):
            continue

        # Localize naive dates or convert aware dates to Pacific time
        if start_time.tzinfo is None:
            start_time = start_time.tz_localize("America/Los_Angeles")
        else:
            start_time = start_time.tz_convert("America/Los_Angeles")

        # Filter out events prior to today
        if start_time < start_of_today:
            continue

        # Preserve title casing
        title_raw = record.get("title")
        clean_title = re.sub(r"\s+", " ", str(title_raw).strip()) if title_raw else None

        venue_raw = record.get("business_name")
        clean_venue = re.sub(r"\s+", " ", str(venue_raw).strip()) if venue_raw else DEFAULT_VENUE

        item_data = {
            "venue_name": clean_venue,
            "address": parse_address_string(record.get("location")),
            "title": clean_title,
            "start_time": [start_time.strftime("%Y-%m-%d")],
            "description": None,
            "big_desc": None,
            "external_url": record.get("event_website") or record.get("registration_url"),
            "image_url": record.get("image_url"),
            "ticket_link": record.get("registration_url") or record.get("event_website")
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
        logger.info(f"--- [EVENT {idx}/{len(records)}] ---")
        logger.info(f"  Title     : {item_data['title']}")
        logger.info(f"  Start Date: {item_data['start_time']}")

    if not events_data:
        return pd.DataFrame()

    return pd.DataFrame(events_data)


if __name__ == "__main__":
    logger.info("Executing Proctor District scraper task...")
    raw_df = scraper()

    if not raw_df.empty:
        logger.info("==================================================================")
        logger.info(f"[PIPELINE HANDOFF] Passing {len(raw_df)} event records to VividaIngestionPipeline...")
        logger.info("==================================================================")
        
        pipeline = VividaIngestionPipeline(table_name="events")
        success = pipeline.run_standalone_pipeline(raw_df)
        
        if success:
            logger.info("[PIPELINE SUCCESS] Proctor District scraper pipeline execution completed successfully.")
        else:
            logger.error("[PIPELINE ERROR] Proctor District scraper pipeline execution reported errors.")
    else:
        logger.warning("[EXIT] No events scraped. Pipeline handoff skipped.")