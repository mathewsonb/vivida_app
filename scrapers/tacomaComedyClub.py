"""
tacomaComedyClub.py
Autonomous JSON-LD Scraper for Tacoma Comedy Club (Tacoma, WA).
Extracts schema.org metadata directly via requests and routes to Supabase.
"""

import json
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
logger = logging.getLogger("scrape_tacoma_comedy_club")

TARGET_URL = "https://www.tacomacomedyclub.com/events"
DEFAULT_VENUE = "Tacoma Comedy Club"
DEFAULT_ADDRESS = "933 Market Street, Tacoma, WA 98402"
SECONDARY_ADDRESS = "3829 6th Ave, Tacoma, WA 98406"


def clean_html_to_text(html_content: Optional[str]) -> Optional[str]:
    """Strips HTML formatting tags and normalizes whitespace."""
    if not html_content:
        return None
    soup = BeautifulSoup(html_content, "html.parser")
    return clean_str(soup.get_text(separator=" ", strip=True))


def parse_address_from_description(raw_description_html: str) -> str:
    """Parses exact venue branch location from description keywords."""
    if not raw_description_html:
        return DEFAULT_ADDRESS
    if any(kw in raw_description_html for kw in ["3829 6th Ave", "6th &", "6th and"]):
        return SECONDARY_ADDRESS
    return DEFAULT_ADDRESS


def scraper() -> pd.DataFrame:
    """Scrapes Tacoma Comedy Club events using structured JSON-LD and returns raw DataFrame."""
    logger.info(f"Starting Tacoma Comedy Club JSON-LD scrape from {TARGET_URL}...")
    
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    
    try:
        response = requests.get(TARGET_URL, headers=headers, timeout=15)
        response.raise_for_status()
    except Exception as e:
        logger.error(f"Failed to fetch Tacoma Comedy Club page: {e}")
        return pd.DataFrame()

    soup = BeautifulSoup(response.text, "html.parser")
    json_ld_script = soup.find("script", type="application/ld+json")

    if not json_ld_script or not json_ld_script.string:
        logger.error("JSON-LD script block not found on page.")
        return pd.DataFrame()

    try:
        data = json.loads(json_ld_script.string)
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse JSON-LD payload: {e}")
        return pd.DataFrame()

    events_raw = data.get("Events", []) if isinstance(data, dict) else []
    logger.info(f"Retrieved {len(events_raw)} raw JSON-LD event entities.")

    grouped_events = {}

    for evt in events_raw:
        title = clean_str(evt.get("name"))
        if not title:
            continue

        raw_desc = evt.get("description", "")
        clean_desc = clean_html_to_text(raw_desc)
        address = parse_address_from_description(raw_desc)
        image_url = evt.get("image")
        ticket_url = evt.get("url")

        # Parse start date
        iso_start = evt.get("startDate", "")
        start_dt = pd.to_datetime(iso_start, errors="coerce")
        start_date_str = start_dt.strftime("%Y-%m-%d") if pd.notnull(start_dt) else None

        group_key = (title, address)

        if group_key not in grouped_events:
            grouped_events[group_key] = {
                "venue_name": DEFAULT_VENUE,
                "address": address,
                "title": title,
                "start_time": [],
                "description": clean_desc or title,
                "big_desc": clean_desc or title,
                "external_url": ticket_url,
                "image_url": image_url,
                "ticket_link": ticket_url
            }

        if start_date_str and start_date_str not in grouped_events[group_key]["start_time"]:
            grouped_events[group_key]["start_time"].append(start_date_str)

    # Format dates and build final list
    final_events = []
    for item in grouped_events.values():
        item["start_time"].sort()
        if not item["start_time"]:
            item["start_time"] = None
        final_events.append(item)

    if not final_events:
        return pd.DataFrame()

    return pd.DataFrame(final_events)


if __name__ == "__main__":
    logger.info("Executing Tacoma Comedy Club scraper task...")
    raw_df = scraper()

    if not raw_df.empty:
        # Run autonomous pipeline transformation & Supabase upsert
        pipeline = VividaIngestionPipeline(table_name="events")
        success = pipeline.run_standalone_pipeline(raw_df)
        if success:
            logger.info("Tacoma Comedy Club scraper execution completed successfully.")
        else:
            logger.error("Tacoma Comedy Club scraper pipeline execution reported errors.")
    else:
        logger.warning("No events scraped. Exiting task.")