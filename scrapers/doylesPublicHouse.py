"""
scrape_doyles.py
Autonomous Scraper for Doyle's Public House (Tacoma, WA).
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
logger = logging.getLogger("scrape_doyles")

BASE_URL = "https://www.doylespublichouse.com"
TARGET_URL = "https://www.doylespublichouse.com/events"
DEFAULT_VENUE = "Doyle's Public House"
DEFAULT_ADDRESS = "208 St. Helens Ave South, Tacoma, WA 98402"


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


def scraper() -> pd.DataFrame:
    """Scrapes Doyle's Public House events using a single headless Chrome instance and returns raw DataFrame."""
    logger.info(f"Starting Doyle's Public House scrape from {TARGET_URL}...")
    events_data = []

    driver = None
    try:
        chrome_opts = get_headless_chrome_options()
        driver = webdriver.Chrome(options=chrome_opts)

        # 1. Fetch main calendar page (Popmenu engine)
        soup = fetch_rendered_page(driver, TARGET_URL, wait_selector="div.pm-calendar-event, div.pm-calendar-event-section-wrapper")
        if not soup:
            logger.error("Could not load main events page.")
            return pd.DataFrame()

        target_section = soup.find("div", class_=re.compile(r"pm-calendar-event-section-wrapper")) or soup
        
        # Clean script/style nodes
        for element in target_section(["script", "style"]):
            element.decompose()

        event_items = target_section.find_all("div", class_=re.compile(r"pm-calendar-event-card"))
        logger.info(f"Found {len(event_items)} Popmenu event cards.")

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

            # Extract Title & Relative/Absolute Link
            h2_tag = event.find("h2")
            if h2_tag:
                anchor = h2_tag.find("a", href=True)
                if anchor:
                    item_data["title"] = anchor.get_text(strip=True)
                    href = anchor["href"].strip()
                    item_data["external_url"] = href if href.startswith("http") else f"{BASE_URL}{href}"

            # Extract Date Components (Month / Day)
            month_tag = event.find("span", class_=re.compile(r"calendar-event-month"))
            day_tag = event.find("span", class_=re.compile(r"calendar-event-date"))

            if month_tag and day_tag:
                month_str = month_tag.get_text(strip=True)
                day_str = day_tag.get_text(strip=True)
                
                try:
                    raw_date_str = f"{month_str} {day_str}, {now.year}"
                    start_dt = pd.to_datetime(raw_date_str, errors="coerce", format="mixed")

                    if pd.notnull(start_dt):
                        # Year boundary rollover check
                        if start_dt.month < now.month:
                            start_dt = start_dt + pd.DateOffset(years=1)
                        item_data["start_time"] = [start_dt.strftime("%Y-%m-%d")]
                except Exception as e:
                    logger.warning(f"Failed to parse date '{month_str} {day_str}': {e}")

            # Extract Primary Short Description
            desc_div = event.find("div", class_=re.compile(r"pm-calendar-event-content-right"))
            if desc_div:
                item_data["description"] = desc_div.get_text(separator=" ", strip=True)

            # Extract Card Image
            img_tag = event.find("img", src=True)
            if img_tag and img_tag["src"]:
                src = img_tag["src"].strip()
                item_data["image_url"] = src if src.startswith("http") else f"{BASE_URL}{src}"

            # 3. Step into Detail Page for expanded text content
            if item_data["external_url"]:
                det_soup = fetch_rendered_page(
                    driver, 
                    item_data["external_url"], 
                    wait_selector="main#page-content, div.pm-event-details"
                )
                
                if det_soup:
                    main_content = det_soup.find("main", id="page-content") or det_soup
                    rich_text = fetch_event_text(main_content)
                    if rich_text:
                        item_data["big_desc"] = re.sub(r"\s+", " ", rich_text)

            events_data.append(item_data)

    except Exception as e:
        logger.error(f"Error during headless execution loop: {e}")
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
    logger.info("Executing Doyle's Public House scraper task...")
    raw_df = scraper()

    if not raw_df.empty:
        # Run autonomous pipeline transformation & Supabase upsert
        pipeline = VividaIngestionPipeline(table_name="events")
        success = pipeline.run_standalone_pipeline(raw_df)
        if success:
            logger.info("Doyle's Public House scraper execution completed successfully.")
        else:
            logger.error("Doyle's Public House scraper pipeline execution reported errors.")
    else:
        logger.warning("No events scraped. Exiting task.")