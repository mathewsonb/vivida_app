"""
proctorDistrict.py
Autonomous REST API Scraper for The Proctor District (Tacoma, WA).
Fetches Base44 API event entities, consolidates recurring/multi-day schedules,
and routes directly to Supabase via VividaIngestionPipeline.
"""

import logging
import os
import re
import sys
from typing import Any, Dict, List, Optional

from bs4 import BeautifulSoup
import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from vivida_etl import (
    VividaIngestionPipeline,
    clean_html_source,
    fetch_event_text,
    parse_event_dates,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(filename)s:%(lineno)d] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("scrape_proctor_district")

BASE_URL = "https://www.proctordistrict.com"
API_URL = (
    os.getenv("PROCTOR_API_URL")
    or "https://base44.app/api/apps/6911cf52c9f849cb61177089/entities/Event"
    "?sort=event_date&limit=500"
).strip()
DEFAULT_VENUE = "Proctor District"
DEFAULT_ADDRESS = "Proctor District, Tacoma, WA 98407"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json,text/plain,*/*",
}


def normalize_ws(text: Optional[str]) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("\xa0", " ")).strip()


def resolve_url(raw_url: Optional[str]) -> Optional[str]:
    if not raw_url:
        return None
    clean = str(raw_url).strip()
    if not clean or clean.startswith("#") or clean.lower().startswith("javascript:"):
        return None
    if clean.startswith("//"):
        return f"https:{clean}"
    if clean.startswith("http://") or clean.startswith("https://"):
        return clean
    if clean.startswith("/"):
        return f"{BASE_URL}{clean}"
    if "." in clean.split("/")[0]:
        return f"https://{clean}"
    return None


def clean_html_text(raw_text: Optional[str]) -> Optional[str]:
    """Strips HTML formatting tags and normalizes whitespace while preserving text casing."""
    if not raw_text:
        return None
    soup = BeautifulSoup(clean_html_source(str(raw_text)), "html.parser")
    extracted = fetch_event_text(soup) or soup.get_text(separator=" ", strip=True)
    cleaned = normalize_ws(extracted)
    return cleaned if cleaned else None


def parse_address_string(loc_raw: Optional[str]) -> str:
    """Extracts street address from 'Venue — Address' strings without breaking hyphenated numbers."""
    if not loc_raw or pd.isna(loc_raw):
        return DEFAULT_ADDRESS

    parts = re.split(r"\s+[—–-]\s+|[—–]", str(loc_raw))
    cleaned = normalize_ws(parts[-1]) if len(parts) > 1 else normalize_ws(str(loc_raw))

    if not cleaned:
        return DEFAULT_ADDRESS

    if "Tacoma" not in cleaned and "WA" not in cleaned:
        return f"{cleaned}, Tacoma, WA"
    return cleaned


def parse_record_dates(record: Dict[str, Any], start_of_today: pd.Timestamp) -> List[str]:
    """Parses event_date (and optional end_date) in Pacific Time, filtering past dates."""
    raw_start = record.get("event_date")
    if not raw_start:
        return []

    start_ts = pd.to_datetime(raw_start, errors="coerce", format="mixed")
    if pd.isna(start_ts):
        return []

    if start_ts.tzinfo is None:
        start_ts = start_ts.tz_localize("America/Los_Angeles")
    else:
        start_ts = start_ts.tz_convert("America/Los_Angeles")

    raw_end = record.get("end_date")
    end_ts = (
        pd.to_datetime(raw_end, errors="coerce", format="mixed") if raw_end else pd.NaT
    )
    if pd.notnull(end_ts):
        if end_ts.tzinfo is None:
            end_ts = end_ts.tz_localize("America/Los_Angeles")
        else:
            end_ts = end_ts.tz_convert("America/Los_Angeles")

    effective_end = end_ts if pd.notnull(end_ts) and end_ts >= start_ts else start_ts
    if effective_end < start_of_today:
        return []

    start_iso = start_ts.strftime("%Y-%m-%d")
    if pd.notnull(end_ts) and end_ts.date() > start_ts.date():
        return parse_event_dates(f"{start_iso} - {end_ts.strftime('%Y-%m-%d')}")
    return [start_iso]


def build_http_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(HEADERS)
    retries = Retry(
        total=2,
        backoff_factor=0.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    adapter = HTTPAdapter(max_retries=retries)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def scraper() -> pd.DataFrame:
    """Queries Base44 REST API for Proctor District events and returns normalized DataFrame."""
    logger.info("[START] Executing Base44 REST API fetch for Proctor District...")

    session = build_http_session()
    try:
        response = session.get(API_URL, timeout=15)
        response.raise_for_status()
        payload = response.json()
    except Exception as e:
        logger.error(f"[API ERROR] Failed to query Base44 API endpoint: {e}")
        return pd.DataFrame()
    finally:
        session.close()

    records = (
        payload
        if isinstance(payload, list)
        else payload.get("data") or payload.get("items") or []
    )
    logger.info(
        f"[PARSER SUCCESS] Retrieved {len(records)} raw event records from Base44 API."
    )

    start_of_today = pd.Timestamp.now(tz="America/Los_Angeles").floor("D")
    grouped: Dict[tuple, Dict[str, Any]] = {}

    for record in records:
        if not isinstance(record, dict):
            continue

        clean_title = normalize_ws(str(record.get("title") or ""))
        if not clean_title:
            continue

        date_list = parse_record_dates(record, start_of_today)
        if not date_list:
            continue

        clean_venue = (
            normalize_ws(str(record.get("business_name") or "")) or DEFAULT_VENUE
        )
        group_key = (clean_title.lower(), clean_venue.lower())

        if group_key in grouped:
            grouped[group_key]["dates_set"].update(date_list)
            continue

        ext_url = resolve_url(record.get("event_website")) or resolve_url(
            record.get("registration_url")
        )
        tix_url = resolve_url(record.get("registration_url")) or ext_url
        cleaned_desc = clean_html_text(record.get("description"))

        grouped[group_key] = {
            "venue_name": clean_venue,
            "address": parse_address_string(record.get("location")),
            "title": clean_title,
            "dates_set": set(date_list),
            "description": cleaned_desc or clean_title,
            "big_desc": cleaned_desc or clean_title,
            "external_url": ext_url,
            "image_url": resolve_url(record.get("image_url")),
            "ticket_link": tix_url,
        }

    if not grouped:
        return pd.DataFrame()

    events_data: List[Dict[str, Any]] = []
    for entry in grouped.values():
        entry["start_time"] = sorted(entry.pop("dates_set"))
        events_data.append(entry)

    logger.info(
        f"[NORMALIZED] Prepared {len(events_data)} unique Proctor District event records."
    )
    return pd.DataFrame(events_data)


if __name__ == "__main__":
    logger.info("Executing Proctor District scraper task...")
    raw_df = scraper()

    if not raw_df.empty:
        pipeline = VividaIngestionPipeline(table_name="events")
        success = pipeline.run_standalone_pipeline(raw_df)

        if success:
            logger.info(
                "[PIPELINE SUCCESS] Proctor District scraper pipeline execution completed successfully."
            )
        else:
            logger.error(
                "[PIPELINE ERROR] Proctor District scraper pipeline execution reported errors."
            )
    else:
        logger.warning("[EXIT] No events scraped. Pipeline handoff skipped.")