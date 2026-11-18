"""
emeraldQueenCasino.py
Autonomous Scraper for Emerald Queen Casino (Tacoma, WA).
Uses HTTP-first MotoCMS listing + concurrent HTTP detail enrichment (workers=10)
with lazy Xvfb/Chrome hydration fallback and centralized parse_event_dates normalization.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
import glob
import logging
import re
import shutil
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

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
    create_stealth_driver
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(filename)s:%(lineno)d] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("scrape_eqc")

BASE_URL = "https://emeraldqueen.com"
TICKETS_URL = "https://emeraldqueen.com/tickets/"
DEFAULT_VENUE = "Emerald Queen Casino"
DEFAULT_ADDRESS = "2920 E R St, Tacoma, WA 98404"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def normalize_ws(text: Optional[str]) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("\xa0", " ")).strip()


def resolve_url(raw_url: Optional[str]) -> Optional[str]:
    if not raw_url:
        return None
    clean = str(raw_url).strip()
    if (
        not clean
        or clean.startswith("#")
        or clean.lower().startswith("javascript:")
        or clean.lower().startswith("data:")
    ):
        return None
    if clean.startswith("//"):
        return f"https:{clean}"
    if clean.startswith("http://") or clean.startswith("https://"):
        return clean
    return f"{BASE_URL}{clean if clean.startswith('/') else '/' + clean}"


def build_http_session(pool_size: int = 12) -> requests.Session:
    session = requests.Session()
    session.headers.update(HEADERS)
    retries = Retry(
        total=2,
        backoff_factor=0.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    adapter = HTTPAdapter(
        max_retries=retries,
        pool_connections=pool_size,
        pool_maxsize=pool_size,
    )
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def parse_eqc_date_block(raw_date_text: Optional[str]) -> List[str]:
    """Parses EQC date blocks including bullet separators and multi-night '&' schedules."""
    if not raw_date_text:
        return []
    cleaned = normalize_ws(raw_date_text.replace("•", " "))
    # Handle "Nov 20 & 21, 2026" or "Nov 20 & Nov 21, 2026"
    if "&" in cleaned or " and " in cleaned.lower():
        year_match = re.search(r"\b(20\d{2})\b", cleaned)
        year_suffix = f", {year_match.group(1)}" if year_match else ""
        month_match = re.match(
            r"^(?:[A-Za-z]+,?\s+)?([A-Za-z]+)\s+\d{1,2}", cleaned
        )
        lead_month = month_match.group(1) if month_match else ""

        parts = re.split(r"\s*(?:&|\band\b)\s*", cleaned, flags=re.IGNORECASE)
        collected: List[str] = []
        for part in parts:
            part_clean = part.strip().rstrip(",")
            if re.match(r"^\d{1,2}\b", part_clean) and lead_month:
                part_clean = f"{lead_month} {part_clean}"
            if year_suffix and not re.search(r"\b20\d{2}\b", part_clean):
                part_clean = f"{part_clean}{year_suffix}"
            collected.extend(parse_event_dates(part_clean))
        if collected:
            return sorted(set(collected))

    return parse_event_dates(cleaned)


def parse_detail_html(
    html: str,
    card_desc: Optional[str],
    card_title: str,
    detail_url: str,
    current_image: Optional[str],
) -> Tuple[Optional[str], str, Optional[str]]:
    """Extracts Ticketmaster URL, rich description, and og:image fallback from detail HTML."""
    det_soup = BeautifulSoup(clean_html_source(html), "html.parser")
    if not det_soup:
        return detail_url, card_desc or card_title, current_image

    content_root = det_soup.find(id="section-content") or det_soup

    ticket_link = detail_url
    tm_link = content_root.find(
        "a", href=re.compile(r"ticketmaster|tickets", re.I)
    )
    if tm_link and tm_link.get("href"):
        ticket_link = resolve_url(tm_link["href"]) or detail_url

    image_url = current_image
    if not image_url:
        og_img = det_soup.find("meta", property="og:image")
        if og_img and og_img.get("content"):
            image_url = resolve_url(og_img["content"])

    rich_text = fetch_event_text(content_root)
    big_desc = normalize_ws(rich_text) if rich_text else (
        card_desc or card_title)

    return ticket_link, big_desc, image_url


def fetch_detail_via_http(
    session: requests.Session, item: Dict[str, Any]
) -> Tuple[Dict[str, Any], bool]:
    detail_url = item.get("external_url")
    if not detail_url:
        return item, True

    try:
        resp = session.get(detail_url, timeout=15)
        resp.raise_for_status()
        if len(resp.text) < 500:
            return item, False

        ticket_link, big_desc, image_url = parse_detail_html(
            resp.text,
            item.get("description"),
            item["title"],
            detail_url,
            item.get("image_url"),
        )
        item["ticket_link"] = ticket_link
        item["big_desc"] = big_desc
        if not item.get("description"):
            item["description"] = big_desc[:250]
        item["image_url"] = image_url
        return item, True
    except Exception:
        return item, False


def scraper() -> pd.DataFrame:
    """Scrapes Emerald Queen Casino events via HTTP-first listing + concurrent detail enrichment."""
    logger.info(f"[START] Scraping Emerald Queen Casino: {TICKETS_URL}")
    session = build_http_session(pool_size=12)

    try:
        response = session.get(TICKETS_URL, timeout=15)
        response.raise_for_status()
    except Exception as e:
        logger.error(f"[HTTP ERROR] GET failed for {TICKETS_URL}: {e}")
        return pd.DataFrame()

    cleaned_main_html = clean_html_source(response.text)
    soup = BeautifulSoup(cleaned_main_html, "html.parser")

    target_table = soup.find(
        "section", class_=re.compile(r"content.*moto-section"))
    if not target_table:
        logger.warning(
            "[PARSER WARNING] Could not locate target section container on EQC tickets page.")
        return pd.DataFrame()

    event_items = target_table.find_all(
        "div",
        class_=re.compile(r"moto-widget-row.*moto-spacing-top-medium"),
    )
    logger.info(
        f"[PARSER] Found {len(event_items)} candidate rows on EQC tickets page.")

    staged_items: List[Dict[str, Any]] = []
    for event in event_items:
        details = event.find_all(
            "div", class_=re.compile(r"moto-widget-text-content"))
        title = normalize_ws(details[0].get_text(
            " ", strip=True)) if len(details) >= 1 else ""
        if not title:
            continue

        raw_date_str = details[1].get_text(
            " ", strip=True) if len(details) >= 2 else ""
        start_dates = parse_eqc_date_block(raw_date_str)
        if not start_dates:
            continue

        card_desc = (
            normalize_ws(details[2].get_text(" ", strip=True))
            if len(details) >= 3
            else None
        )

        anchor = event.find("a", href=True)
        external_url = resolve_url(anchor["href"]) if anchor else TICKETS_URL

        tm_card_link = event.find("a", href=re.compile(r"ticketmaster", re.I))
        ticket_link = (
            resolve_url(tm_card_link["href"])
            if tm_card_link and tm_card_link.get("href")
            else external_url
        )

        img = event.find("img")
        image_url = (
            resolve_url(img.get("data-src") or img.get("src"))
            if img
            else None
        )

        staged_items.append(
            {
                "venue_name": DEFAULT_VENUE,
                "address": DEFAULT_ADDRESS,
                "title": title,
                "start_time": start_dates,
                "description": card_desc or title,
                "external_url": external_url,
                "image_url": image_url,
                "ticket_link": ticket_link,
                "big_desc": card_desc or title,
            }
        )

    if not staged_items:
        logger.warning(
            "[PARSER WARNING] 0 valid events extracted from EQC tickets page.")
        return pd.DataFrame()

    logger.info(
        f"[PARSER SUCCESS] Extracted {len(staged_items)} valid EQC events. Enriching details (workers=10)..."
    )

    # Concurrent HTTP detail enrichment
    enriched_items: List[Dict[str, Any]] = []
    fallback_items: List[Dict[str, Any]] = []

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [
            executor.submit(fetch_detail_via_http, session, item)
            for item in staged_items
        ]
        for fut in as_completed(futures):
            updated_item, ok = fut.result()
            if ok:
                enriched_items.append(updated_item)
            else:
                fallback_items.append(updated_item)

    if fallback_items:
        display = None
        driver = None
        try:
            from pyvirtualdisplay import Display

            display = Display(visible=0, size=(1920, 1080), backend="xvfb")
            display.start()
            driver = create_stealth_driver()

            for item in fallback_items:
                detail_url = item.get("external_url")
                if detail_url:
                    try:
                        driver.get(detail_url)
                        time.sleep(1.5)
                        ticket_link, big_desc, image_url = parse_detail_html(
                            driver.page_source,
                            item.get("description"),
                            item["title"],
                            detail_url,
                            item.get("image_url"),
                        )
                        item["ticket_link"] = ticket_link
                        item["big_desc"] = big_desc
                        if not item.get("description"):
                            item["description"] = big_desc[:250]
                        item["image_url"] = image_url
                    except Exception as fb_err:
                        logger.warning(
                            f"[FALLBACK WARN] Failed {detail_url}: {fb_err}")
                enriched_items.append(item)
        except Exception as err:
            logger.warning(
                f"[FALLBACK WARN] Headless Chrome fallback unavailable ({err}); keeping card data.")
            enriched_items.extend(fallback_items)
        finally:
            if driver:
                try:
                    driver.quit()
                except Exception:
                    pass
            if display:
                try:
                    display.stop()
                except Exception:
                    pass

    # Consolidate multi-night rows sharing the same title / external_url
    grouped: Dict[str, Dict[str, Any]] = {}
    for item in enriched_items:
        key = (item.get("external_url") or item["title"]).lower()
        if key in grouped:
            grouped[key]["dates_set"].update(item["start_time"])
            continue
        entry = dict(item)
        entry["dates_set"] = set(item["start_time"])
        grouped[key] = entry

    final_rows: List[Dict[str, Any]] = []
    for entry in grouped.values():
        entry["start_time"] = sorted(entry.pop("dates_set"))
        final_rows.append(entry)

    return pd.DataFrame(final_rows) if final_rows else pd.DataFrame()


if __name__ == "__main__":
    logger.info("Executing Emerald Queen Casino scraper task...")
    raw_df = scraper()

    if not raw_df.empty:
        pipeline = VividaIngestionPipeline(table_name="events")
        success = pipeline.run_standalone_pipeline(raw_df)
        if success:
            logger.info(
                "[PIPELINE SUCCESS] EQC scraper execution completed successfully.")
        else:
            logger.error(
                "[PIPELINE ERROR] EQC scraper pipeline execution reported errors.")
    else:
        logger.warning("[EXIT] No events scraped. Exiting task.")
