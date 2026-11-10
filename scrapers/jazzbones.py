"""
jazzbones.py
Autonomous Scraper for Jazzbones (Tacoma, WA).
Refactored for Xvfb headless execution and isolated Supabase ingestion.
"""

import logging
import os
import re
import sys
from typing import Optional

from bs4 import BeautifulSoup
import pandas as pd
from pyvirtualdisplay import Display
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

# Import consolidated vivida_etl package
from vivida_etl import (
    VividaIngestionPipeline,
    clean_html_source,
    fetch_event_text,
    get_headless_chrome_options,
)

# Configure logging for VM execution
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(filename)s:%(lineno)d] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("scrape_jazzbones")

BASE_URL = "https://jazzbones.com"
TARGET_URL = "https://jazzbones.com/#/events"
DEFAULT_VENUE = "Jazzbones"
DEFAULT_ADDRESS = "2803 6th Ave, Tacoma, WA 98406"


def fetch_rendered_page(driver: webdriver.Chrome, url: str, wait_selector: Optional[str] = None, timeout: int = 10) -> Optional[BeautifulSoup]:
    """Navigates active headless driver to target URL and returns pre-treated BeautifulSoup DOM."""
    try:
        driver.get(url)
        if wait_selector:
            try:
                WebDriverWait(driver, timeout).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, wait_selector))
                )
            except Exception:
                pass
        
        cleaned_html = clean_html_source(driver.page_source)
        return BeautifulSoup(cleaned_html, "html.parser")
    except Exception as e:
        logger.warning(f"Failed to fetch page '{url}': {e}")
        return None


def extract_css_bg_image(style_attr: str) -> Optional[str]:
    """Safely extracts background image URL from inline CSS style attributes."""
    if not style_attr:
        return None
    match = re.search(r'url\((?:["\']?)(.*?)(?:["\']?)\)', style_attr, re.IGNORECASE)
    if match:
        url = match.group(1).strip()
        return url if url.startswith("http") else f"{BASE_URL}/{url.lstrip('/')}"
    return None


def scraper() -> pd.DataFrame:
    """Scrapes Jazzbones events using a single headless Chrome instance and returns raw DataFrame."""
    logger.info("==================================================================")
    logger.info("[START] Initializing PyVirtualDisplay server for Jazzbones scrape...")
    logger.info("==================================================================")
    
    display = Display(visible=0, size=(1920, 1080))
    display.start()

    events_data = []
    driver = None

    try:
        chrome_opts = get_headless_chrome_options()
        driver = webdriver.Chrome(options=chrome_opts)

        # 1. Fetch main event grid page
        logger.info(f"[SELENIUM NAV] Fetching main calendar grid: {TARGET_URL}")
        soup = fetch_rendered_page(driver, TARGET_URL, wait_selector="div.vp-events-container, div.vp-event-card")
        if not soup:
            logger.error("Could not load main events grid.")
            return pd.DataFrame()

        target_section = soup.find("div", class_="vp-events-container") or soup
        
        # Clean script and style tags
        for element in target_section(["script", "style"]):
            element.decompose()

        event_items = target_section.find_all("div", class_=re.compile(r"vp-event-card"))
        if not event_items:
            logger.warning("[PARSER WARNING] Found 0 event cards matching class 'vp-event-card'.")
            return pd.DataFrame()

        logger.info(f"[PARSER SUCCESS] Found {len(event_items)} Jazzbones event cards. Beginning detail traversal...")

        now = pd.Timestamp.now()

        # 2. Iterate event cards and step into detail pages
        for idx, event in enumerate(event_items, start=1):
            logger.info(f"--- [EVENT {idx}/{len(event_items)}] Parsing listing card & detail page ---")
            
            item_data = {
                "venue_name": DEFAULT_VENUE,
                "address": DEFAULT_ADDRESS,
                "title": None,
                "start_time": None,
                "description": None,
                "big_desc": None,
                "external_url": None,
                "image_url": None,
                "ticket_link": None
            }

            # Title
            title_div = event.find("div", class_=re.compile(r"vp-event-name"))
            if title_div:
                item_data["title"] = title_div.get_text(strip=True)

            # Date parsing with year-boundary rollover check
            date_span = event.find("span", class_=re.compile(r"vp-date"))
            if date_span:
                raw_date = date_span.get_text(strip=True)
                clean_date_str = re.sub(
                    r"^(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday|Mon|Tue|Wed|Thu|Fri|Sat|Sun)[s\,]?\s*",
                    "",
                    raw_date,
                    flags=re.IGNORECASE
                ).strip()
                
                try:
                    full_date_str = f"{clean_date_str}, {now.year}" if str(now.year) not in clean_date_str else clean_date_str
                    start_dt = pd.to_datetime(full_date_str, errors="coerce", format="mixed")
                    
                    if pd.notnull(start_dt):
                        if start_dt.month < now.month and str(now.year) not in clean_date_str:
                            start_dt = start_dt + pd.DateOffset(years=1)
                        item_data["start_time"] = [start_dt.strftime("%Y-%m-%d")]
                except Exception as e:
                    logger.warning(f"Failed to parse date '{raw_date}': {e}")

            # Short Description
            desc_div = event.find("div", class_=re.compile(r"vp-details"))
            if desc_div:
                item_data["description"] = desc_div.get_text(separator=" ", strip=True)

            # External URL
            link_anchor = event.find("a", class_=re.compile(r"vp-event-link"), href=True)
            if link_anchor:
                href = link_anchor["href"].strip()
                item_data["external_url"] = href if href.startswith("http") else f"{BASE_URL}/{href.lstrip('/')}"
                item_data["ticket_link"] = item_data["external_url"]

            # Image URL from background-image style
            img_div = event.find("div", class_=re.compile(r"vp-cover-img"))
            if img_div and img_div.get("style"):
                item_data["image_url"] = extract_css_bg_image(img_div["style"])

            # 3. Step into Detail Modal / Page if external URL is available
            if item_data["external_url"]:
                logger.info(f"[DETAIL FETCH] Navigating to detail page: {item_data['external_url']}")
                det_soup = fetch_rendered_page(
                    driver, 
                    item_data["external_url"], 
                    wait_selector="div.vp-event-details-modal, div.vp-event-listing"
                )
                
                if det_soup:
                    modal_div = det_soup.find("div", class_=re.compile(r"vp-event-details-modal|vp-event-listing")) or det_soup
                    
                    # Extract external ticket link if present on detail page
                    t_btn = modal_div.find("a", href=re.compile(r"ticket|buy|booking|eventbrite|ticketweb", re.I))
                    if t_btn and t_btn.get("href"):
                        item_data["ticket_link"] = t_btn.get("href").strip()

                    rich_text = fetch_event_text(modal_div)
                    if rich_text:
                        item_data["big_desc"] = re.sub(r"\s+", " ", rich_text)
                    else:
                        item_data["big_desc"] = item_data["description"]
            else:
                item_data["big_desc"] = item_data["description"]

            events_data.append(item_data)
            logger.info(f"  Title     : {item_data['title']}")
            logger.info(f"  Start Date: {item_data['start_time']}")

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

    if not events_data:
        return pd.DataFrame()

    return pd.DataFrame(events_data)


if __name__ == "__main__":
    logger.info("Executing Jazzbones autonomous scraper pipeline task...")
    raw_df = scraper()

    if not raw_df.empty:
        logger.info("==================================================================")
        logger.info(f"[PIPELINE HANDOFF] Passing {len(raw_df)} event records to VividaIngestionPipeline...")
        logger.info("==================================================================")
        
        pipeline = VividaIngestionPipeline(table_name="events")
        success = pipeline.run_standalone_pipeline(raw_df)
        
        if success:
            logger.info("[PIPELINE SUCCESS] Jazzbones scraper pipeline execution completed successfully.")
        else:
            logger.error("[PIPELINE ERROR] Jazzbones scraper pipeline execution reported errors.")
    else:
        logger.warning("[EXIT] No events scraped. Pipeline handoff skipped.")