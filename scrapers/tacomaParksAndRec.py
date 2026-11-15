"""
tacomaParksAndRec.py
Autonomous Production Scraper for Metro Parks Tacoma (The Events Calendar / Tribe Engine).
Features text-first date range parsing to preserve multi-day event spans (e.g., "Wed, Oct 7 - Fri, Oct 9, 2026")
and single-day event times without date dropping.
"""

import logging
import os
import re
import sys
import time
from typing import Dict, List, Optional, Set

from bs4 import BeautifulSoup
import pandas as pd
from pyvirtualdisplay import Display
import undetected_chromedriver as uc

from vivida_etl import (
    VividaIngestionPipeline,
    clean_html_source,
    fetch_event_text,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(filename)s:%(lineno)d] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("scrape_parks_tacoma")

BASE_URL = "https://www.parkstacoma.gov"
TARGET_URL = "https://www.parkstacoma.gov/events/list/"
DEFAULT_VENUE = "Metro Parks Tacoma"
DEFAULT_ADDRESS = "Tacoma, WA"


def create_stealth_driver() -> uc.Chrome:
    """Instantiates an undetected Chrome driver configured for Xvfb display rendering."""
    options = uc.ChromeOptions()
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-gpu")
    
    if os.path.exists("/usr/bin/google-chrome"):
        options.binary_location = "/usr/bin/google-chrome"
    elif os.path.exists("/usr/bin/chromium"):
        options.binary_location = "/usr/bin/chromium"

    return uc.Chrome(options=options, version_main=None)


def generate_date_sequence(from_dt: pd.Timestamp, to_dt: Optional[pd.Timestamp]) -> List[str]:
    """Generates a full list of daily ISO date strings for multi-day events up to 60 days."""
    if not from_dt or pd.isnull(from_dt):
        return []

    start_str = from_dt.strftime("%Y-%m-%d")
    if not to_dt or pd.isnull(to_dt) or to_dt.date() <= from_dt.date():
        return [start_str]

    diff_days = (to_dt.date() - from_dt.date()).days
    if diff_days <= 60:
        date_range = pd.date_range(start=from_dt.date(), end=to_dt.date(), freq="D")
        return [d.strftime("%Y-%m-%d") for d in date_range]

    return [start_str]


def clean_and_parse_single_date(raw_str: str) -> Optional[pd.Timestamp]:
    """Strips weekday prefixes, times, and non-breaking spaces before converting to Timestamp."""
    if not raw_str:
        return None
    
    # 1. Replace non-breaking spaces & collapse whitespace
    clean_str = re.sub(r"\s+", " ", str(raw_str).replace("\xa0", " ")).strip()
    
    # 2. Strip leading weekday names (e.g., "Wednesday, ", "Thu, ")
    clean_str = re.sub(r"^(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*,?\s*", "", clean_str, flags=re.IGNORECASE)
    
    # 3. Strip time strings if attached (e.g., "10:00 am", "7:30 pm")
    clean_str = re.sub(r"\b\d{1,2}:\d{2}\s*(?:am|pm)?\b.*", "", clean_str, flags=re.IGNORECASE).strip()

    dt = pd.to_datetime(clean_str, errors="coerce", format="mixed")
    return dt if pd.notnull(dt) else None


def parse_tribe_date_range(event_el: BeautifulSoup) -> Optional[List[str]]:
    """Parses Tribe Events Calendar date blocks for both single-day and multi-day spans."""
    if not event_el:
        return None

    # Locate start and end date spans
    from_span = event_el.find("span", class_=re.compile(r"tribe-event-date-start", re.I))
    to_span = event_el.find("span", class_=re.compile(r"tribe-event-date-end", re.I))

    # Case 1: Multi-day event with explicit start AND end date spans
    if from_span and to_span:
        from_text = from_span.get_text(separator=" ", strip=True)
        to_text = to_span.get_text(separator=" ", strip=True)
        
        dt_start = clean_and_parse_single_date(from_text)
        dt_end = clean_and_parse_single_date(to_text)
        
        if dt_start:
            return generate_date_sequence(dt_start, dt_end)

    # Case 2: Single-day event with start span present (time span ignored)
    if from_span:
        from_text = from_span.get_text(separator=" ", strip=True)
        dt_start = clean_and_parse_single_date(from_text)
        if dt_start:
            return [dt_start.strftime("%Y-%m-%d")]

    # Case 3: Fallback to <time> tag attribute if spans aren't present
    time_tag = event_el.find("time", class_=re.compile(r"tribe-events-calendar-list__event-datetime", re.I)) or event_el.find("time")
    if time_tag:
        datetime_attr = time_tag.get("datetime") or time_tag.get("data-datetime")
        if datetime_attr:
            dt_attr = pd.to_datetime(datetime_attr, errors="coerce")
            if pd.notnull(dt_attr):
                return [dt_attr.strftime("%Y-%m-%d")]

    return None


def scraper() -> pd.DataFrame:
    """Scrapes Metro Parks Tacoma events using undetected Chrome in Xvfb display and returns raw DataFrame."""
    logger.info("==================================================================")
    logger.info(f"[START] Initializing PyVirtualDisplay server for Metro Parks Tacoma: {TARGET_URL}")
    logger.info("==================================================================")
    
    display = Display(visible=0, size=(1920, 1080), backend="xvfb")
    display.start()

    driver = None
    raw_records: List[Dict[str, Optional[str]]] = []
    seen_urls: Set[str] = set()

    try:
        logger.info("[SELENIUM] Launching undetected Chrome driver session...")
        driver = create_stealth_driver()

        # 1. Fetch main event list container
        logger.info(f"[SELENIUM NAV] Fetching target: {TARGET_URL}")
        driver.get(TARGET_URL)
        time.sleep(6)

        driver.execute_script("window.scrollTo(0, document.body.scrollHeight / 2);")
        time.sleep(2)

        main_html = clean_html_source(driver.page_source)
        soup = BeautifulSoup(main_html, "html.parser")

        target_section = (
            soup.find("ul", class_=re.compile(r"tribe-events-calendar-list")) or 
            soup.find("div", id="tribe-events-pg-template") or 
            soup
        )
        
        for element in target_section(["script", "style"]):
            element.decompose()

        event_items = target_section.find_all(
            "div", 
            class_=re.compile(r"tribe-events-calendar-list__event-row|tribe-events-single")
        )
        if not event_items:
            event_items = target_section.find_all("article")

        logger.info(f"[PARSER SUCCESS] Found {len(event_items)} Metro Parks Tacoma event rows.")

        # 2. Iterate event list rows and step into detail pages
        for idx, event in enumerate(event_items, start=1):
            try:
                # Title & External URL
                h4_tag = event.find(["h4", "h3", "h2"], class_=re.compile(r"title", re.I)) or event.find(["h4", "h3"])
                card_title = h4_tag.get_text(strip=True, separator=" ") if h4_tag else None

                anchor = h4_tag.find("a", href=True) if h4_tag else event.find("a", href=True)
                detail_url = None
                if anchor:
                    href = anchor["href"].strip()
                    detail_url = href if href.startswith("http") else f"{BASE_URL}{href if href.startswith('/') else '/' + href}"

                if not card_title:
                    continue

                dedup_key = detail_url or card_title
                if dedup_key in seen_urls:
                    continue
                seen_urls.add(dedup_key)

                logger.info(f"--- [EVENT {len(seen_urls)}/{len(event_items)}] Processing: {card_title} ---")

                # Venue Name
                venue_span = event.find("span", class_=re.compile(r"tribe-events-calendar-list__event-venue-title|venue"))
                venue_name = venue_span.get_text(strip=True, separator=" ") if venue_span else DEFAULT_VENUE

                # Address
                addr_span = event.find("span", class_=re.compile(r"tribe-events-calendar-list__event-venue-address|address"))
                address = addr_span.get_text(strip=True) if addr_span else DEFAULT_ADDRESS

                # Start and End Date Range Parsing
                start_time_seq = parse_tribe_date_range(event)

                # Short Description
                desc_div = event.find("div", class_=re.compile(r"tribe-events-calendar-list__event-description|excerpt"))
                description = desc_div.get_text(strip=True, separator=" ") if desc_div else card_title

                # Card Image URL
                img_tag = event.find("img", src=True)
                image_url = None
                if img_tag and img_tag["src"]:
                    src = img_tag["src"].strip()
                    image_url = src if src.startswith("http") else f"{BASE_URL}{src if src.startswith('/') else '/' + src}"

                # Ticket / Booking Link
                t_anchor = event.find("a", href=re.compile(r"ticket|register|buy", re.IGNORECASE))
                ticket_link = t_anchor["href"].strip() if t_anchor and t_anchor.get("href") else detail_url

                # 3. Step into Detail Page for rich content & fallback date check
                big_desc = description
                if detail_url:
                    logger.info(f"  [DETAIL FETCH] Navigating to detail page: {detail_url}")
                    driver.get(detail_url)
                    time.sleep(3)
                    
                    det_soup = BeautifulSoup(clean_html_source(driver.page_source), "html.parser")
                    if det_soup:
                        # Fallback date check on detail page if listing card date failed
                        if not start_time_seq:
                            start_time_seq = parse_tribe_date_range(det_soup)

                        container = (
                            det_soup.find("section", class_=re.compile(r"event-details")) or 
                            det_soup.find("div", class_=re.compile(r"tribe-events-single|entry-content|elementor-text-editor")) or 
                            det_soup.find("main") or 
                            det_soup
                        )
                        
                        rich_text = fetch_event_text(container)
                        if rich_text:
                            big_desc = re.sub(r"\s+", " ", rich_text)

                        if not ticket_link or ticket_link == detail_url:
                            t_btn = det_soup.find("a", href=re.compile(r"ticket|register|buy", re.IGNORECASE))
                            if t_btn and t_btn.get("href"):
                                ticket_link = t_btn["href"].strip()

                item_data = {
                    "venue_name": venue_name,
                    "address": address,
                    "title": card_title,
                    "start_time": start_time_seq,
                    "description": description,
                    "big_desc": big_desc,
                    "external_url": detail_url,
                    "image_url": image_url,
                    "ticket_link": ticket_link
                }

                raw_records.append(item_data)
                logger.info(f"  Title      : {item_data['title']}")
                logger.info(f"  Start Time : {item_data['start_time']}")

            except Exception as item_err:
                logger.error(f"[ITEM ERROR] Failed parsing Metro Parks event #{idx}: {item_err}", exc_info=True)
                continue

    except Exception as e:
        logger.error(f"[FATAL ERROR] Exception encountered during scraping loop: {e}", exc_info=True)
    finally:
        if driver:
            logger.info("[CLEANUP] Closing Chrome driver instance...")
            try:
                driver.quit()
            except Exception:
                pass
        logger.info("[CLEANUP] Stopping PyVirtualDisplay server...")
        display.stop()

    if not raw_records:
        return pd.DataFrame()

    return pd.DataFrame(raw_records)


if __name__ == "__main__":
    logger.info("Executing Metro Parks Tacoma scraper task...")
    raw_df = scraper()

    if not raw_df.empty:
        logger.info("==================================================================")
        logger.info(f"[PIPELINE HANDOFF] Passing {len(raw_df)} event records to VividaIngestionPipeline...")
        logger.info("==================================================================")
        
        pipeline = VividaIngestionPipeline(table_name="events")
        success = pipeline.run_standalone_pipeline(raw_df)
        
        if success:
            logger.info("[PIPELINE SUCCESS] Metro Parks Tacoma scraper execution completed successfully.")
        else:
            logger.error("[PIPELINE ERROR] Metro Parks Tacoma scraper pipeline execution reported errors.")
    else:
        logger.warning("[EXIT] No events scraped. Pipeline handoff skipped.")