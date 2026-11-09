"""
tacomaArtMuseum.py
Autonomous Scraper for Tacoma Art Museum (Tacoma, WA).
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
logger = logging.getLogger("scrape_tam")

BASE_URL = "https://www.tacomaartmuseum.org"
TARGET_URL = "https://www.tacomaartmuseum.org/events/"
DEFAULT_VENUE = "Tacoma Art Museum"
DEFAULT_ADDRESS = "1701 Pacific Avenue, Tacoma, WA 98402"


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
    """Scrapes Tacoma Art Museum events using a single headless Chrome instance and returns raw DataFrame."""
    logger.info(f"Starting Tacoma Art Museum scrape from {TARGET_URL}...")
    events_data = []

    driver = None
    try:
        chrome_opts = get_headless_chrome_options()
        driver = webdriver.Chrome(options=chrome_opts)

        # 1. Fetch main event list container
        soup = fetch_rendered_page(driver, TARGET_URL, wait_selector="div.main_column, ul.events-list, li")
        if not soup:
            logger.error("Could not load main TAM events page.")
            return pd.DataFrame()

        target_section = soup.find("div", class_="main_column") or soup
        
        # Strip script and style tags
        for element in target_section(["script", "style"]):
            element.decompose()

        event_items = target_section.find_all("li")
        logger.info(f"Found {len(event_items)} TAM candidate event list items.")

        now = pd.Timestamp.now()

        # 2. Iterate event list items and step into detail pages
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

            # Title & Link extraction
            anchor = event.find("a", href=True)
            if anchor:
                href = anchor["href"].strip()
                item_data["external_url"] = href if href.startswith("http") else f"{BASE_URL}{href if href.startswith('/') else '/' + href}"
                
                deets_div = anchor.find("div", class_="deets")
                if deets_div:
                    raw_title = deets_div.get_text(strip=True)
                    item_data["title"] = re.sub(r"\s+", " ", raw_title)

            if not item_data["title"] and anchor:
                item_data["title"] = anchor.get_text(strip=True)

            # Date parsing with year rollover support
            num_div = event.find("div", class_=re.compile(r"num|date"))
            if num_div:
                day_str = num_div.get_text(strip=True)
                try:
                    full_date_str = f"{day_str}, {now.year}"
                    start_dt = pd.to_datetime(full_date_str, errors="coerce", format="mixed")
                    
                    if pd.notnull(start_dt):
                        if start_dt.month < now.month:
                            start_dt = start_dt + pd.DateOffset(years=1)
                        item_data["start_time"] = [start_dt.strftime("%Y-%m-%d")]
                except Exception as e:
                    logger.warning(f"Failed to parse date string '{day_str}': {e}")

            # 3. Step into Detail Page if external URL is available
            if item_data["external_url"]:
                det_soup = fetch_rendered_page(
                    driver, 
                    item_data["external_url"], 
                    wait_selector="div.rtecontent, div.tribe-events-content, main"
                )
                
                if det_soup:
                    container = (
                        det_soup.find("div", class_=re.compile(r"rtecontent|tribe-events-content")) or 
                        det_soup.find("main") or 
                        det_soup
                    )
                    
                    # Extract big description using clean_html_source and fetch_event_text
                    rich_text = fetch_event_text(container)
                    if rich_text:
                        item_data["big_desc"] = re.sub(r"\s+", " ", rich_text)

                    # Extract primary short description (h3 or first paragraph)
                    h3_tag = container.find("h3")
                    if h3_tag:
                        item_data["description"] = h3_tag.get_text(separator=" ", strip=True)
                    else:
                        p_tag = container.find("p")
                        if p_tag:
                            item_data["description"] = p_tag.get_text(separator=" ", strip=True)
                        else:
                            item_data["description"] = item_data["big_desc"][:250] if item_data["big_desc"] else None

                    # Extract detail image URL
                    img_tag = container.find("img", src=True)
                    if img_tag and img_tag["src"]:
                        src = img_tag["src"].strip()
                        item_data["image_url"] = src if src.startswith("http") else f"{BASE_URL}{src if src.startswith('/') else '/' + src}"

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
    logger.info("Executing Tacoma Art Museum scraper task...")
    raw_df = scraper()

    if not raw_df.empty:
        # Run autonomous pipeline transformation & Supabase upsert
        pipeline = VividaIngestionPipeline(table_name="events")
        success = pipeline.run_standalone_pipeline(raw_df)
        if success:
            logger.info("TAM scraper execution completed successfully.")
        else:
            logger.error("TAM scraper pipeline execution reported errors.")
    else:
        logger.warning("No events scraped. Exiting task.")