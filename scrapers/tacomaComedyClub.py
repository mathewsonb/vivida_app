"""
tacomaComedyClub.py
Autonomous JSON-LD Scraper for Tacoma Comedy Club (Tacoma, WA).
Extracts schema.org metadata via pooled HTTP requests, converts UTC showtime
timestamps to America/Los_Angeles local dates via parse_event_dates, groups
multi-showtime headliner runs by (title, address) into discrete date arrays,
and routes consolidated records to VividaIngestionPipeline.
"""

import html
import json
import logging
import os
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
TARGET_URL = (
    os.getenv("TCC_EVENTS_URL") or "https://www.tacomacomedyclub.com/events"
).strip()
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


def extract_offer_details(
    evt: Dict[str, Any],
) -> Tuple[Optional[str], Optional[str]]:
    """Extracts checkout URL and price summary from schema.org offers block."""
    offers = evt.get("offers")
    if isinstance(offers, list):
        offer_items = [o for o in offers if isinstance(o, dict)]
    elif isinstance(offers, dict):
        offer_items = [offers]
    else:
        offer_items = []

    offer_url: Optional[str] = None
    prices: List[str] = []

    for offer in offer_items:
        if not offer_url:
            offer_url = resolve_url(offer.get("url"))
        raw_price = offer.get("price")
        if raw_price is not None and str(raw_price).strip() != "":
            p_str = str(raw_price).strip()
            if p_str in ("0", "0.0", "0.00"):
                prices.append("Free ($0)")
            else:
                currency = str(offer.get("priceCurrency") or "$").strip()
                prefix = "$" if currency == "USD" else currency
                prices.append(
                    f"{prefix}{p_str}" if not p_str.startswith("$") else p_str
                )

    price_note = f"Tickets: {', '.join(dict.fromkeys(prices))}." if prices else None
    return offer_url, price_note


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
        return any(
            isinstance(x, str) and (x == "Event" or x.endswith("Event")) for x in t
        )
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
        f"[START] Fetching Tacoma Comedy Club JSON-LD payload from {TARGET_URL}..."
    )
    session = build_http_session()
    events_raw: List[Dict[str, Any]] = []
    visited_urls = set()
    next_url: Optional[str] = TARGET_URL

    try:
        while next_url and next_url not in visited_urls and len(visited_urls) < 5:
            visited_urls.add(next_url)
            try:
                response = session.get(next_url, timeout=15)
                response.raise_for_status()
            except Exception as e:
                logger.error(
                    f"[FETCH ERROR] Failed to fetch Tacoma Comedy Club page ({next_url}): {e}"
                )
                break

            soup = BeautifulSoup(response.text, "html.parser")
            json_ld_scripts = soup.find_all("script", type="application/ld+json")

            for script in json_ld_scripts:
                raw_json = script.string or script.get_text()
                if not raw_json or not raw_json.strip():
                    continue
                try:
                    data = json.loads(raw_json)
                    collect_jsonld_events(data, events_raw)
                except json.JSONDecodeError:
                    continue

            next_link = soup.find("link", rel="next") or soup.find("a", rel="next")
            next_url = resolve_url(next_link.get("href")) if next_link else None
    finally:
        session.close()

    if not events_raw:
        logger.error("[PARSER ERROR] No JSON-LD Event objects extracted from page.")
        return pd.DataFrame()

    logger.info(
        f"[PARSER SUCCESS] Retrieved {len(events_raw)} raw JSON-LD showtime entries."
    )

    # Group multi-showtime entries by (normalized_title_lower, address) into discrete local date arrays
    grouped: Dict[Tuple[str, str], Dict[str, Any]] = {}
    skipped_no_date = 0
    skipped_cancelled = 0

    for evt in events_raw:
        status = str(evt.get("eventStatus") or "")
        if "EventCancelled" in status:
            skipped_cancelled += 1
            continue

        clean_title = normalize_ws(evt.get("name"))
        if not clean_title:
            continue

        # parse_event_dates converts UTC/offset ISO-8601 startDate timestamps to America/Los_Angeles
        parsed_dates = parse_event_dates(evt.get("startDate"))
        if not parsed_dates:
            skipped_no_date += 1
            continue

        raw_desc = str(evt.get("description") or "")
        cleaned_text = clean_html_to_text(raw_desc)
        address = resolve_branch_address(evt, raw_desc)
        image_url = extract_jsonld_image(evt.get("image"))

        offer_url, price_note = extract_offer_details(evt)
        event_url = resolve_url(evt.get("url"))
        ticket_url = offer_url or event_url
        external_url = event_url or offer_url

        age_range = normalize_ws(evt.get("typicalAgeRange"))
        age_note = (
            f"Age Restriction: {age_range}."
            if age_range
            else "Age Restriction: 18+ or 21+ unless stated otherwise."
        )
        enrichment_suffix = " ".join(n for n in (age_note, price_note) if n)
        base_desc = cleaned_text or clean_title
        rich_big_desc = f"{base_desc} {enrichment_suffix}".strip()

        group_key = (clean_title.lower(), address)
        if group_key not in grouped:
            grouped[group_key] = {
                "venue_name": DEFAULT_VENUE,
                "address": address,
                "title": clean_title,
                "dates_set": set(parsed_dates),
                "description": base_desc,
                "big_desc": rich_big_desc,
                "external_url": external_url,
                "image_url": image_url,
                "ticket_link": ticket_url,
            }
        else:
            entry = grouped[group_key]
            entry["dates_set"].update(parsed_dates)
            if cleaned_text and len(cleaned_text) > len(entry["description"] or ""):
                entry["description"] = cleaned_text
                entry["big_desc"] = rich_big_desc
            if not entry["image_url"] and image_url:
                entry["image_url"] = image_url
            if not entry["ticket_link"] and ticket_url:
                entry["ticket_link"] = ticket_url
            if not entry["external_url"] and external_url:
                entry["external_url"] = external_url

    if skipped_cancelled:
        logger.info(f"[SKIP] Excluded {skipped_cancelled} cancelled JSON-LD entries.")
    if skipped_no_date:
        logger.warning(
            f"[SKIP] Dropped {skipped_no_date} JSON-LD entries with unparseable startDate."
        )

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
                "[PIPELINE SUCCESS] Tacoma Comedy Club scraper execution completed successfully."
            )
        else:
            logger.error(
                "[PIPELINE ERROR] Tacoma Comedy Club scraper pipeline execution reported errors."
            )
    else:
        logger.warning("[EXIT] No events scraped. Pipeline handoff skipped.")