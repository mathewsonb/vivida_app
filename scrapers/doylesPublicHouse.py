"""
doylesPublicHouse.py
Autonomous Scraper for Doyle's Public House (Tacoma, WA).
Refactored for Xvfb headless execution and isolated Supabase ingestion.
"""

import logging
import re
import sys
import time
from typing import Optional
from urllib.parse import urljoin

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


def fetch_rendered_page(driver: webdriver.Chrome, url: str, timeout: int = 12) -> Optional[BeautifulSoup]:
    """Navigates active headless driver to target URL, scrolls to trigger JS rendering, and returns BeautifulSoup DOM."""
    try:
        driver.get(url)
        time.sleep(3)  # Allow JS engine to bootstrap
        
        # Scroll down to trigger dynamic loading
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight/2);")
        time.sleep(2)
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(2)

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

        # 1. Fetch main calendar page
        soup = fetch_rendered_page(driver, TARGET_URL)
        if not soup:
            logger.error("Could not load main events page.")
            return pd.DataFrame()

        # Clean script/style nodes
        for element in soup(["script", "style"]):
            element.decompose()

        # Flexible Popmenu selector list
        event_items = soup.find_all("div", class_="col-md-4 col-xs-12 pm-calendar-event-card-wrapper")
        
        # Secondary fallback: find anchors with /events/ in href
        if not event_items:
            anchors = soup.find_all("a", href=re.compile(r"/events/"))
            event_items = [a.find_parent(["div", "article", "li"]) or a for a in anchors]

        logger.info(f"Found {len(event_items)} candidate event elements.")

        now = pd.Timestamp.now()

        # 2. Iterate event cards
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

            # Extract Title & Link
            anchor = event.find("a", href=True) if event.name != "a" else event
            heading = event.find(["h1", "h2", "h3", "h4", "h5", "div", "span"], class_=re.compile(r"title|header|name", re.I))
            
            if heading and heading.get_text(strip=True):
                item_data["title"] = heading.get_text(strip=True)
            elif anchor and anchor.get_text(strip=True):
                item_data["title"] = anchor.get_text(strip=True)

            if not item_data["title"] or len(item_data["title"]) < 3:
                continue

            if anchor and anchor.get("href"):
                item_data["external_url"] = urljoin(BASE_URL, anchor["href"].strip())

            # Extract Date Components
            month_tag = event.find(["span", "div"], class_=re.compile(r"month", re.I))
            day_tag = event.find(["span", "div"], class_=re.compile(r"date|day", re.I))

            if month_tag and day_tag:
                month_str = month_tag.get_text(strip=True)
                day_str = day_tag.get_text(strip=True)
                try:
                    raw_date_str = f"{month_str} {day_str}, {now.year}"
                    start_dt = pd.to_datetime(raw_date_str, errors="coerce", format="mixed")
                    if pd.notnull(start_dt):
                        if start_dt.month < now.month:
                            start_dt = start_dt + pd.DateOffset(years=1)
                        item_data["start_time"] = [start_dt.strftime("%Y-%m-%d")]
                except Exception as e:
                    logger.warning(f"Failed to parse date '{month_str} {day_str}': {e}")

            # Extract Description
            desc_div = event.find(["div", "p", "span"], class_=re.compile(r"description|content|summary", re.I))
            if desc_div:
                item_data["description"] = desc_div.get_text(separator=" ", strip=True)
                item_data["big_desc"] = item_data["description"]

            # Extract Image
            img_tag = event.find("img", src=True)
            if img_tag and img_tag["src"]:
                item_data["image_url"] = urljoin(BASE_URL, img_tag["src"].strip())

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
        pipeline = VividaIngestionPipeline(table_name="events")
        success = pipeline.run_standalone_pipeline(raw_df)
        if success:
            logger.info("Doyle's Public House scraper execution completed successfully.")
        else:
            logger.error("Doyle's Public House scraper pipeline execution reported errors.")
    else:
        logger.warning("No events scraped. Exiting task.")