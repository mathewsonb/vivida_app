"""
tacomaComedyClub.py
Autonomous JSON-LD Scraper for Tacoma Comedy Club (Tacoma, WA).
Extracts schema.org metadata via pooled HTTP requests, groups multi-showtime
headliner runs by (title, address) into discrete date arrays, and routes
consolidated records to VividaIngestionPipeline.
"""

import html
import json
import logging
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

from bs4 import BeautifulSoup
import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from vivida_etl import (
    VividaIngestionPipeline,
    parse_event_dates,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(filename)s:%(lineno)d] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("scrape_tacoma_comedy_club")

BASE_URL = "https://www.tacomacomedyclub.com"
TARGET_URL = "https://www.tacomacomedyclub.com/events"
DEFAULT_VENUE = "Tacoma Comedy Club"
DEFAULT_ADDRESS = "933 Market Street, Tacoma, WA 98402"
SECONDARY_ADDRESS = "3829 6th Ave, Tacoma, WA 98406"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def normalize_ws(text: Optional[str]) -> str:
    if not text:
        return ""
    unescaped = html.unescape(str(text)).replace("\xa0", " ")
    return re.sub(r"\s+", " ", unescaped).strip()


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
    return f"{BASE_URL}{clean if clean.startswith('/') else '/' + clean}"


def clean_html_to_text(html_content: Optional[str]) -> Optional[str]:
    """Strips HTML formatting tags, decodes entities, and normalizes whitespace."""
    if not html_content:
        return None
    unescaped = html.unescape(str(html_content))
    soup = BeautifulSoup(unescaped, "html.parser")
    text = soup.get_text(separator=" ", strip=True)
    cleaned = normalize_ws(text)
    return cleaned if cleaned else None


def extract_jsonld_image(raw_image: Any) -> Optional[str]:
    """Handles schema.org image fields as str, list, or ImageObject dict."""
    if isinstance(raw_image, str):
        return resolve_url(raw_image)
    if isinstance(raw_image, list) and raw_image:
        return extract_jsonld_image(raw_image[0])
    if isinstance(raw_image, dict):
        return resolve_url(raw_image.get("url") or raw_image.get("contentUrl"))
    return None


def resolve_branch_address(evt: Dict[str, Any], raw_description: str) -> str:
    """Determines Downtown (933 Market St) vs. 6th Ave (3829 6th Ave) branch from JSON-LD location or description."""
    loc = evt.get("location")
    loc_blob = ""
    if isinstance(loc, dict):
        addr = loc.get("address")
        if isinstance(addr, dict):
            loc_blob = f"{loc.get('name', '')} {addr.get('streetAddress', '')}"
        elif isinstance(addr, str):
            loc_blob = f"{loc.get('name', '')} {addr}"
    elif isinstance(loc, str):
        loc_blob = loc

    combined = f"{loc_blob} {raw_description}".lower()
    if any(
        kw in combined
        for kw in ["3829 6th", "6th ave", "6th &", "6th and", "downtown vs 6th"]
    ):
        return SECONDARY_ADDRESS
    return DEFAULT_ADDRESS


def is_event_entity(obj: Any) -> bool:
    if not isinstance(obj, dict):
        return False
    t = obj.get("@type")
    if isinstance(t, str):
        return t == "Event" or t.endswith("Event")
    if isinstance(t, list):
        return any(isinstance(x, str) and (x == "Event" or x.endswith("Event")) for x in t)
    return False


def collect_jsonld_events(node: Any, out: List[Dict[str, Any]]) -> None:
    """Recursively extracts all Event / ComedyEvent objects from arbitrary JSON-LD payloads."""
    if isinstance(node, list):
        for item in node:
            collect_jsonld_events(item, out)
    elif isinstance(node, dict):
        if is_event_entity(node):
            out.append(node)
        for key in ("@graph", "Events", "events", "itemListElement"):
            if key in node:
                collect_jsonld_events(node[key], out)


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
    """Scrapes Tacoma Comedy Club JSON-LD events and consolidates multi-showtime runs."""
    logger.info(
        f"[START] Fetching Tacoma Comedy Club JSON-LD payload from {TARGET_URL}...")
    session = build_http_session()

    try:
        response = session.get(TARGET_URL, timeout=15)
        response.raise_for_status()
    except Exception as e:
        logger.error(
            f"[FETCH ERROR] Failed to fetch Tacoma Comedy Club page: {e}")
        return pd.DataFrame()

    soup = BeautifulSoup(response.text, "html.parser")
    json_ld_scripts = soup.find_all("script", type="application/ld+json")

    if not json_ld_scripts:
        logger.error("[PARSER ERROR] No JSON-LD script blocks found on page.")
        return pd.DataFrame()

    events_raw: List[Dict[str, Any]] = []
    for script in json_ld_scripts:
        raw_json = script.string or script.get_text()
        if not raw_json or not raw_json.strip():
            continue
        try:
            data = json.loads(raw_json)
            collect_jsonld_events(data, events_raw)
        except json.JSONDecodeError:
            continue

    logger.info(
        f"[PARSER SUCCESS] Retrieved {len(events_raw)} raw JSON-LD showtime entries.")

    # Group multi-showtime entries by (normalized_title_lower, address) into discrete date arrays
    grouped: Dict[Tuple[str, str], Dict[str, Any]] = {}
    skipped_no_date = 0

    for evt in events_raw:
        clean_title = normalize_ws(evt.get("name"))
        if not clean_title:
            continue

        iso_start = str(evt.get("startDate") or "").strip()
        start_dt = pd.to_datetime(iso_start, errors="coerce")
        if pd.notnull(start_dt):
            parsed_dates = [start_dt.strftime("%Y-%m-%d")]
        else:
            parsed_dates = parse_event_dates(iso_start)

        if not parsed_dates:
            skipped_no_date += 1
            continue

        raw_desc = str(evt.get("description") or "")
        cleaned_text = clean_html_to_text(raw_desc)
        address = resolve_branch_address(evt, raw_desc)
        image_url = extract_jsonld_image(evt.get("image"))
        ticket_url = resolve_url(evt.get("url"))

        group_key = (clean_title.lower(), address)
        if group_key not in grouped:
            grouped[group_key] = {
                "venue_name": DEFAULT_VENUE,
                "address": address,
                "title": clean_title,
                "dates_set": set(parsed_dates),
                "description": cleaned_text or clean_title,
                "big_desc": cleaned_text or clean_title,
                "external_url": ticket_url,
                "image_url": image_url,
                "ticket_link": ticket_url,
            }
        else:
            entry = grouped[group_key]
            entry["dates_set"].update(parsed_dates)
            # Keep richer description / valid image if the first showtime entry was sparse
            if cleaned_text and len(cleaned_text) > len(entry["big_desc"] or ""):
                entry["description"] = cleaned_text
                entry["big_desc"] = cleaned_text
            if not entry["image_url"] and image_url:
                entry["image_url"] = image_url
            if not entry["ticket_link"] and ticket_url:
                entry["ticket_link"] = ticket_url
                entry["external_url"] = ticket_url

    if skipped_no_date:
        logger.warning(
            f"[SKIP] Dropped {skipped_no_date} JSON-LD entries with unparseable startDate.")

    raw_records: List[Dict[str, Any]] = []
    for entry in grouped.values():
        sorted_dates = sorted(entry.pop("dates_set"))
        entry["start_time"] = sorted_dates
        raw_records.append(entry)

    logger.info(
        f"[CONSOLIDATED] Grouped {len(events_raw)} showtimes into "
        f"{len(raw_records)} unique Tacoma Comedy Club runs."
    )

    return pd.DataFrame(raw_records) if raw_records else pd.DataFrame()


if __name__ == "__main__":
    logger.info("Executing Tacoma Comedy Club scraper task...")
    raw_df = scraper()

    if not raw_df.empty:
        pipeline = VividaIngestionPipeline(table_name="events")
        success = pipeline.run_standalone_pipeline(raw_df)
        if success:
            logger.info(
                "[PIPELINE SUCCESS] Tacoma Comedy Club scraper execution completed successfully.")
        else:
            logger.error(
                "[PIPELINE ERROR] Tacoma Comedy Club scraper pipeline execution reported errors.")
    else:
        logger.warning("[EXIT] No events scraped. Pipeline handoff skipped.")
