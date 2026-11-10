"""
tacomaDome.py
Autonomous Production Scraper for Tacoma Dome (Tacoma, WA).
Fetches upcoming arena/dome events via undetected_chromedriver + Xvfb,
expands date sequences for multi-day events, and routes records to VividaIngestionPipeline.
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

# Import consolidated vivida_etl pipeline package
from vivida_etl import (
    VividaIngestionPipeline,
    clean_html_source,
    fetch_event_text,
)

# Configure stdout logging with explicit timestamps and line numbers
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(filename)s:%(lineno)d] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("scrape_tacoma_dome")

BASE_URL = "https://www.tacomadome.org"
TARGET_URL = "https://www.tacomadome.org/events"
DEFAULT_VENUE = "Tacoma Dome"
DEFAULT_ADDRESS = "2727 E D St, Tacoma, WA 98402"


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


def extract_css_bg_image(style_attr: str) -> Optional[str]:
    """Safely extracts background image URL from inline CSS style attributes."""
    if not style_attr:
        return None
    match = re.search(r'url\((?:["\']?)(.*?)(?:["\']?)\)', style_attr, re.IGNORECASE)
    if match:
        url = match.group(1).strip()
        return url if url.startswith("http") else f"{BASE_URL}{url if url.startswith('/') else '/' + url}"
    return None


def generate_date_sequence(first_day: pd.Timestamp, last_day: pd.Timestamp) -> Optional[List[str]]:
    """Expands start/end date pairs into a complete list of daily ISO date strings."""
    if pd.isna(first_day):
        return None

    start_str = first_day.strftime("%Y-%m-%d")
    if pd.isna(last_day) or last_day <= first_day:
        return [start_str]

    diff_days = (last_day.date() - first_day.date()).days
    if diff_days <= 60:
        date_range = pd.date_range(start=first_day.date(), end=last_day.date(), freq="D")
        return [d.strftime("%Y-%m-%d") for d in date_range]

    return [start_str]


def scraper() -> pd.DataFrame:
    """Scrapes Tacoma Dome events using undetected Chrome in Xvfb display and returns raw DataFrame."""
    logger.info("==================================================================")
    logger.info(f"[START] Initializing PyVirtualDisplay server for Tacoma Dome scrape: {TARGET_URL}")
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
        logger.info(f"[SELENIUM NAV] Fetching main events list: {TARGET_URL}")
        driver.get(TARGET_URL)
        time.sleep(6)

        driver.execute_script("window.scrollTo(0, document.body.scrollHeight / 2);")
        time.sleep(2)

        main_html = clean_html_source(driver.page_source)
        soup = BeautifulSoup(main_html, "html.parser")

        target_section = (
            soup.find("div", class_=re.compile(r"full_column|non-widget-area")) or 
            soup
        )
        
        # Strip script and style elements
        for element in target_section(["script", "style"]):
            element.decompose()

        event_items = target_section.find_all("div", class_=re.compile(r"^eventItem.*clearfix$"))
        logger.info(f"[PARSER SUCCESS] Found {len(event_items)} Tacoma Dome event blocks.")

        # 2. Iterate event blocks and step into detail pages
        for idx, event in enumerate(event_items, start=1):
            try:
                # Title
                h3_tag = event.find("h3")
                card_title = h3_tag.get_text(strip=True, separator=" ") if h3_tag else None

                if not card_title:
                    continue

                # External URL
                anchor = event.find("a", href=True)
                detail_url = None
                if anchor:
                    href = anchor["href"].strip()
                    detail_url = href if href.startswith("http") else f"{BASE_URL}{href if href.startswith('/') else '/' + href}"

                dedup_key = detail_url or card_title
                if dedup_key in seen_urls:
                    continue
                seen_urls.add(dedup_key)

                logger.info(f"--- [EVENT {len(seen_urls)}/{len(event_items)}] Processing: {card_title} ---")

                # Raw Date string from aria-label or text
                date_div = event.find("div", class_=re.compile(r"date"))
                raw_date_str = None
                if date_div:
                    raw_date_str = date_div.get("aria-label") or date_div.get_text(strip=True)

                # Short Description
                h4_tag = event.find("h4")
                description = h4_tag.get_text(strip=True, separator=" ") if h4_tag else card_title

                # Background Image URL
                bg_div = event.find("div", class_=re.compile(r"background-img"))
                image_url = extract_css_bg_image(bg_div["style"]) if bg_div and bg_div.get("style") else None

                # Ticket Link
                t_anchor = event.find("a", class_=re.compile(r"tickets|onsalenow", re.IGNORECASE))
                ticket_link = t_anchor["href"].strip() if t_anchor and t_anchor.get("href") else detail_url

                # Step into Detail Page for rich text
                big_desc = description
                if detail_url:
                    logger.info(f"  [DETAIL FETCH] Navigating to detail page: {detail_url}")
                    driver.get(detail_url)
                    time.sleep(3)
                    
                    det_soup = BeautifulSoup(clean_html_source(driver.page_source), "html.parser")
                    if det_soup:
                        rich_text = fetch_event_text(det_soup)
                        if rich_text:
                            big_desc = re.sub(r"\s+", " ", rich_text)

                item_data = {
                    "venue_name": DEFAULT_VENUE,
                    "address": DEFAULT_ADDRESS,
                    "title": card_title,
                    "start_time": None,
                    "raw_date_str": raw_date_str,
                    "description": description,
                    "big_desc": big_desc,
                    "external_url": detail_url,
                    "image_url": image_url,
                    "ticket_link": ticket_link
                }

                raw_records.append(item_data)

            except Exception as item_err:
                logger.error(f"[ITEM ERROR] Failed parsing Tacoma Dome event #{idx}: {item_err}", exc_info=True)
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

    td_df = pd.DataFrame(raw_records)

    # 3. Multi-day Date Range Extraction & Sequence Formatting
    if "raw_date_str" in td_df.columns:
        td_df[["first_day", "last_day"]] = (
            td_df["raw_date_str"].astype(str).str.split(" to ", expand=True).apply(lambda col: col.str.strip())
        )

        extracted_year = td_df["last_day"].str.extract(r"(\b\d{4}\b)", expand=False)
        needs_year = extracted_year.notna()

        td_df.loc[needs_year, "first_day"] = (
            td_df.loc[needs_year, "first_day"].str.strip() + " " + extracted_year[needs_year]
        )

        td_df["first_day"] = pd.to_datetime(td_df["first_day"], errors="coerce")
        td_df["last_day"] = pd.to_datetime(td_df["last_day"], errors="coerce")

        td_df["start_time"] = [
            generate_date_sequence(x, y) for x, y in zip(td_df["first_day"], td_df["last_day"])
        ]

        td_df = td_df.drop(columns=["first_day", "last_day", "raw_date_str"], errors="ignore")

    return td_df


if __name__ == "__main__":
    logger.info("Executing Tacoma Dome scraper task...")
    raw_df = scraper()

    if not raw_df.empty:
        logger.info("==================================================================")
        logger.info(f"[PIPELINE HANDOFF] Passing {len(raw_df)} event records to VividaIngestionPipeline...")
        logger.info("==================================================================")
        
        pipeline = VividaIngestionPipeline(table_name="events")
        success = pipeline.run_standalone_pipeline(raw_df)
        
        if success:
            logger.info("[PIPELINE SUCCESS] Tacoma Dome scraper execution completed successfully.")
        else:
            logger.error("[PIPELINE ERROR] Tacoma Dome scraper pipeline execution reported errors.")
    else:
        logger.warning("[EXIT] No events scraped. Pipeline handoff skipped.")