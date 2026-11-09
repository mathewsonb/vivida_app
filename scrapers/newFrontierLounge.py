"""
scrape_new_frontier.py
Autonomous Scraper for New Frontier Lounge (Tacoma, WA).
Refactored for Xvfb headless execution and isolated Supabase ingestion.
"""

import logging
import re
import sys
from typing import Optional

from bs4 import BeautifulSoup
import pandas as pd
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
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("scrape_new_frontier")

BASE_URL = "https://www.newfrontierlounge.com"
TARGET_URL = "https://www.newfrontierlounge.com/calendar#/events"
DEFAULT_VENUE = "New Frontier Lounge"
DEFAULT_ADDRESS = "301 E 25th Street, Tacoma, WA 98421"


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
    """Scrapes New Frontier Lounge events using a single headless Chrome instance and returns raw DataFrame."""
    logger.info(f"Starting New Frontier Lounge scrape from {TARGET_URL}...")
    events_data = []

    driver = None
    try:
        chrome_opts = get_headless_chrome_options()
        driver = webdriver.Chrome(options=chrome_opts)

        # 1. Fetch main event grid page
        soup = fetch_rendered_page(driver, TARGET_URL, wait_selector="div.vp-events-container, div.vp-event-card")
        if not soup:
            logger.error("Could not load main events grid.")
            return pd.DataFrame()

        target_section = soup.find("div", class_="vp-events-container") or soup
        
        # Clean script and style tags
        for element in target_section(["script", "style"]):
            element.decompose()

        event_items = target_section.find_all("div", class_=re.compile(r"vp-event-card"))
        logger.info(f"Found {len(event_items)} New Frontier Lounge event cards.")

        now = pd.Timestamp.now()

        # 2. Iterate event cards and step into detail pages
        for event in event_items:
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
                date_parts = raw_date.split()
                if len(date_parts) >= 3:
                    month_str = date_parts[1].strip()
                    day_str = date_parts[2].strip()
                    try:
                        full_date_str = f"{month_str} {day_str}, {now.year}"
                        start_dt = pd.to_datetime(full_date_str, errors="coerce", format="mixed")
                        
                        if pd.notnull(start_dt):
                            if start_dt.month < now.month:
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
                item_data["external_url"] = href if href.startswith("http") else f"{BASE_URL}/calendar{href if href.startswith('/') else '/' + href}"

            # Image URL from background-image style
            img_div = event.find("div", class_=re.compile(r"vp-cover-img"))
            if img_div and img_div.get("style"):
                item_data["image_url"] = extract_css_bg_image(img_div["style"])

            # 3. Step into Detail Modal / Page if external URL is available
            if item_data["external_url"]:
                det_soup = fetch_rendered_page(
                    driver, 
                    item_data["external_url"], 
                    wait_selector="div.vp-event-details-modal, div.vp-event-listing"
                )
                
                if det_soup:
                    modal_div = det_soup.find("div", class_=re.compile(r"vp-event-details-modal|vp-event-listing")) or det_soup
                    rich_text = fetch_event_text(modal_div)
                    if rich_text:
                        item_data["big_desc"] = re.sub(r"\s+", " ", rich_text)
                    else:
                        item_data["big_desc"] = item_data["description"]

            events_data.append(item_data)

    except Exception as e:
        logger.error(f"Error during headless browser execution loop: {e}")
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass

    if not events_data:
        return pd.DataFrame()

    return pd.DataFrame(events_data)


if __name__ == "__main__":
    logger.info("Executing New Frontier Lounge scraper task...")
    raw_df = scraper()

    if not raw_df.empty:
        # Run autonomous pipeline transformation & Supabase upsert
        pipeline = VividaIngestionPipeline(table_name="events")
        success = pipeline.run_standalone_pipeline(raw_df)
        if success:
            logger.info("New Frontier Lounge scraper execution completed successfully.")
        else:
            logger.error("New Frontier Lounge scraper pipeline execution reported errors.")
    else:
        logger.warning("No events scraped. Exiting task.")