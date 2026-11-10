"""
tacomaComedyClub.py
Autonomous JSON-LD Scraper for Tacoma Comedy Club (Tacoma, WA).
Extracts schema.org metadata directly via requests and routes flat event
records to VividaIngestionPipeline for Supabase upserts.
"""

import json
import logging
import re
import sys
from typing import Optional

from bs4 import BeautifulSoup
import pandas as pd
import requests

# Import consolidated vivida_etl pipeline package
from vivida_etl import (
    VividaIngestionPipeline,
    clean_str,
)

# Configure stdout logging with explicit timestamps and line numbers
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(filename)s:%(lineno)d] %(message)s",
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
    text = soup.get_text(separator=" ", strip=True)
    return re.sub(r"\s+", " ", text).strip() if text else None


def parse_address_from_description(raw_description_html: str) -> str:
    """Parses exact venue branch location from description keywords."""
    if not raw_description_html:
        return DEFAULT_ADDRESS
    if any(kw in raw_description_html for kw in ["3829 6th Ave", "6th &", "6th and", "Downtown vs 6th"]):
        return SECONDARY_ADDRESS
    return DEFAULT_ADDRESS


def scraper() -> pd.DataFrame:
    """Scrapes Tacoma Comedy Club events using structured JSON-LD and returns raw DataFrame."""
    logger.info("==================================================================")
    logger.info(f"[START] Fetching Tacoma Comedy Club JSON-LD payload from {TARGET_URL}...")
    logger.info("==================================================================")
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    }
    
    try:
        response = requests.get(TARGET_URL, headers=headers, timeout=15)
        response.raise_for_status()
    except Exception as e:
        logger.error(f"[FETCH ERROR] Failed to fetch Tacoma Comedy Club page: {e}")
        return pd.DataFrame()

    soup = BeautifulSoup(response.text, "html.parser")
    json_ld_scripts = soup.find_all("script", type="application/ld+json")

    if not json_ld_scripts:
        logger.error("[PARSER ERROR] No JSON-LD script blocks found on page.")
        return pd.DataFrame()

    events_raw = []
    for script in json_ld_scripts:
        if not script.string:
            continue
        try:
            data = json.loads(script.string)
            if isinstance(data, dict):
                # Handle single Event entity or top-level wrapper with "Events" list
                if data.get("@type") == "Event":
                    events_raw.append(data)
                elif "Events" in data and isinstance(data["Events"], list):
                    events_raw.extend(data["Events"])
            elif isinstance(data, list):
                events_raw.extend([item for item in data if isinstance(item, dict) and item.get("@type") == "Event"])
        except json.JSONDecodeError:
            continue

    logger.info(f"[PARSER SUCCESS] Retrieved {len(events_raw)} raw JSON-LD event entities.")

    raw_records = []

    for idx, evt in enumerate(events_raw, start=1):
        raw_title = evt.get("name")
        if not raw_title or not str(raw_title).strip():
            continue

        clean_title = re.sub(r"\s+", " ", str(raw_title).strip())
        raw_desc = evt.get("description", "")
        cleaned_text = clean_html_to_text(raw_desc)
        address = parse_address_from_description(raw_desc)
        image_url = evt.get("image")
        ticket_url = evt.get("url")

        # Parse start date ISO string
        iso_start = evt.get("startDate", "")
        start_dt = pd.to_datetime(iso_start, errors="coerce")
        start_time_val = [start_dt.strftime("%Y-%m-%d")] if pd.notnull(start_dt) else None

        item_data = {
            "venue_name": DEFAULT_VENUE,
            "address": address,
            "title": clean_title,
            "start_time": start_time_val,
            "description": cleaned_text or clean_title,
            "big_desc": cleaned_text or clean_title,
            "external_url": ticket_url,
            "image_url": image_url,
            "ticket_link": ticket_url
        }

        raw_records.append(item_data)
        logger.info(f"  [EVENT {idx}/{len(events_raw)}] {item_data['title']} -> {item_data['start_time']}")

    if not raw_records:
        return pd.DataFrame()

    return pd.DataFrame(raw_records)


if __name__ == "__main__":
    logger.info("Executing Tacoma Comedy Club scraper task...")
    raw_df = scraper()

    if not raw_df.empty:
        logger.info("==================================================================")
        logger.info(f"[PIPELINE HANDOFF] Passing {len(raw_df)} event records to VividaIngestionPipeline...")
        logger.info("==================================================================")
        
        pipeline = VividaIngestionPipeline(table_name="events")
        success = pipeline.run_standalone_pipeline(raw_df)
        
        if success:
            logger.info("[PIPELINE SUCCESS] Tacoma Comedy Club scraper execution completed successfully.")
        else:
            logger.error("[PIPELINE ERROR] Tacoma Comedy Club scraper pipeline execution reported errors.")
    else:
        logger.warning("[EXIT] No events scraped. Pipeline handoff skipped.")