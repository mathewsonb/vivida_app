"""
scrape_pdza.py
Autonomous Scraper for Point Defiance Zoo & Aquarium (Tacoma, WA).
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
logger = logging.getLogger("scrape_pdza")

BASE_URL = "https://www.pdza.org"
TARGET_URL = "https://www.pdza.org/events/"
DEFAULT_VENUE = "Point Defiance Zoo & Aquarium"
DEFAULT_ADDRESS = "5400 N Pearl St, Tacoma, WA 98407"


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
        return url if url.startswith("http") else f"{BASE_URL}{url if url.startswith('/') else '/' + url}"
    return None


def scraper() -> pd.DataFrame:
    """Scrapes PDZA events using a single headless Chrome instance and returns raw DataFrame."""
    logger.info(f"Starting Point Defiance Zoo & Aquarium scrape from {TARGET_URL}...")
    events_data = []

    driver = None
    try:
        chrome_opts = get_headless_chrome_options()
        driver = webdriver.Chrome(options=chrome_opts)

        # 1. Fetch main event list container
        soup = fetch_rendered_page(driver, TARGET_URL, wait_selector="div.tribe-events-list, div.event-card")
        if not soup:
            logger.error("Could not load main events list page.")
            return pd.DataFrame()

        target_section = soup.find("div", class_=re.compile(r"tribe-events-list")) or soup
        
        # Clean script and style tags
        for element in target_section(["script", "style"]):
            element.decompose()

        event_items = target_section.find_all("div", class_=re.compile(r"event-card"))
        logger.info(f"Found {len(event_items)} PDZA event cards.")

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
            title_anchor = event.find("a", class_=re.compile(r"event-card__title"))
            if title_anchor:
                item_data["title"] = title_anchor.get_text(strip=True, separator=" ")

            # External URL
            link_anchor = event.find("a", class_=re.compile(r"link-arrow|event-card__title"), href=True)
            if link_anchor:
                href = link_anchor["href"].strip()
                item_data["external_url"] = href if href.startswith("http") else f"{BASE_URL}{href if href.startswith('/') else '/' + href}"

            # Date parsing with year-boundary rollover check
            date_div = event.find("div", class_=re.compile(r"event-card__date"))
            if date_div:
                raw_date_text = date_div.get_text(strip=True)
                try:
                    full_date_str = f"{raw_date_text}, {now.year}"
                    start_dt = pd.to_datetime(full_date_str, errors="coerce", format="mixed")
                    
                    if pd.notnull(start_dt):
                        if start_dt.month < now.month:
                            start_dt = start_dt + pd.DateOffset(years=1)
                        item_data["start_time"] = [start_dt.strftime("%Y-%m-%d")]
                except Exception as e:
                    logger.warning(f"Failed to parse date text '{raw_date_text}': {e}")

            # Short Description
            desc_div = event.find("div", class_=re.compile(r"event-card__description"))
            if desc_div:
                item_data["description"] = desc_div.get_text(strip=True, separator=" ")

            # Image URL from background-image style attribute or img tag
            img_anchor = event.find("a", class_=re.compile(r"event-card__image"))
            if img_anchor and img_anchor.get("style"):
                item_data["image_url"] = extract_css_bg_image(img_anchor["style"])
            elif img_anchor and img_anchor.find("img"):
                src = img_anchor.find("img").get("src")
                if src:
                    item_data["image_url"] = src if src.startswith("http") else f"{BASE_URL}{src}"

            # 3. Step into Detail Page if external URL is available
            if item_data["external_url"]:
                det_soup = fetch_rendered_page(
                    driver, 
                    item_data["external_url"], 
                    wait_selector="section.single-event, main"
                )
                
                if det_soup:
                    section = (
                        det_soup.find("section", class_=re.compile(r"single-event")) or 
                        det_soup.find("main") or 
                        det_soup
                    )
                    rich_text = fetch_event_text(section)
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
    logger.info("Executing Point Defiance Zoo & Aquarium scraper task...")
    raw_df = scraper()

    if not raw_df.empty:
        # Run autonomous pipeline transformation & Supabase upsert
        pipeline = VividaIngestionPipeline(table_name="events")
        success = pipeline.run_standalone_pipeline(raw_df)
        if success:
            logger.info("PDZA scraper execution completed successfully.")
        else:
            logger.error("PDZA scraper pipeline execution reported errors.")
    else:
        logger.warning("No events scraped. Exiting task.")