"""
scrape_city_of_up.py
Autonomous Scraper for City of University Place (WA) Events.
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
logger = logging.getLogger("scrape_city_of_up")

BASE_URL = "https://www.cityofup.com"
TARGET_URL = "https://www.cityofup.com/132/Events"
DEFAULT_VENUE = "University Place, WA"


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
    """Scrapes City of UP events using single headless Chrome instance and returns raw DataFrame."""
    logger.info(f"Starting City of University Place scrape from {TARGET_URL}...")
    events_data = []

    driver = None
    try:
        chrome_opts = get_headless_chrome_options()
        driver = webdriver.Chrome(options=chrome_opts)

        # 1. Fetch main calendar page
        # Using resilient class-based selector instead of hardcoded widget GUID
        soup = fetch_rendered_page(driver, TARGET_URL, wait_selector="div.calendarInnerWrapper, .cpGrid")
        if not soup:
            logger.error("Could not load main events page.")
            return pd.DataFrame()

        target_section = soup.find("div", class_="calendarInnerWrapper") or soup.find("div", class_=re.compile(r"cpGrid"))
        if not target_section:
            logger.warning("Could not locate calendar container on page.")
            return pd.DataFrame()

        # Clean script/style nodes
        for element in target_section(["script", "style"]):
            element.decompose()

        event_items = target_section.find_all("li")
        logger.info(f"Found {len(event_items)} event cards in calendar view.")

        now = pd.Timestamp.now()

        # 2. Iterate event list and step into detail pages via same driver
        for event in event_items:
            item_data = {
                "venue_name": DEFAULT_VENUE,
                "address": None,
                "title": None,
                "start_time": None,
                "description": None,
                "big_desc": None,
                "external_url": None,
                "image_url": None,
                "ticket_link": None
            }

            # Extract Title & Relative Link
            anchor = event.find("a", href=True)
            if anchor:
                item_data["title"] = anchor.get_text(strip=True)
                href = anchor["href"].strip()
                item_data["external_url"] = href if href.startswith("http") else f"{BASE_URL}{href}"

            # Extract Raw Date Header
            h4_tag = event.find("h4")
            if h4_tag:
                raw_date_str = h4_tag.get_text(strip=True)
                try:
                    # Expecting formats like "Tuesday, November 15"
                    date_parts = raw_date_str.split(",")
                    if len(date_parts) > 1:
                        date_body = date_parts[1].strip()
                        start_dt = pd.to_datetime(f"{date_body}, {now.year}", errors="coerce", format="mixed")
                        
                        if pd.notnull(start_dt):
                            # Year boundary rollover check
                            if start_dt.month < now.month:
                                start_dt = start_dt + pd.DateOffset(years=1)
                            item_data["start_time"] = [start_dt.strftime("%Y-%m-%d")]
                except Exception as e:
                    logger.warning(f"Failed to parse date string '{raw_date_str}': {e}")

            # 3. Step into Detail Page if URL exists
            if item_data["external_url"]:
                det_soup = fetch_rendered_page(
                    driver, 
                    item_data["external_url"], 
                    wait_selector="div.eventDetails, div[itemprop='description']"
                )
                
                if det_soup:
                    # Extract Venue & Address (itemprop="location")
                    addr_soup = det_soup.find("div", itemprop="location")
                    if addr_soup:
                        venue_name_tag = addr_soup.find("div", itemprop="name")
                        if venue_name_tag:
                            item_data["venue_name"] = venue_name_tag.get_text(strip=True)

                        try:
                            street = addr_soup.find("span", itemprop="streetAddress").get_text(strip=True)
                            city = addr_soup.find("span", itemprop="addressLocality").get_text(strip=True)
                            zip_code = addr_soup.find("span", itemprop="postalCode").get_text(strip=True)
                            item_data["address"] = f"{street}, {city}, WA {zip_code}"
                        except Exception:
                            # Fallback if specific itemprop spans are missing
                            item_data["address"] = addr_soup.get_text(separator=", ", strip=True)

                    # Extract Description
                    desc_container = det_soup.find("div", itemprop="description")
                    if desc_container:
                        # Big description
                        raw_big_desc = desc_container.get_text(separator=" ", strip=True)
                        item_data["big_desc"] = re.sub(r"\s+", " ", raw_big_desc)

                        # Primary short description (first paragraph)
                        p_tags = desc_container.find_all("p")
                        if p_tags:
                            item_data["description"] = p_tags[0].get_text(separator=" ", strip=True)
                        else:
                            item_data["description"] = item_data["big_desc"][:250] if item_data["big_desc"] else None

                    # Extract Detail Image
                    img_tag = det_soup.find("img", src=True)
                    if img_tag and img_tag["src"]:
                        src = img_tag["src"].strip()
                        item_data["image_url"] = src if src.startswith("http") else f"{BASE_URL}{src}"

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
    logger.info("Executing City of University Place scraper task...")
    raw_df = scraper()

    if not raw_df.empty:
        # Run autonomous pipeline transformation & Supabase upsert
        pipeline = VividaIngestionPipeline(table_name="events")
        success = pipeline.run_standalone_pipeline(raw_df)
        if success:
            logger.info("City of UP scraper execution completed successfully.")
        else:
            logger.error("City of UP scraper pipeline execution reported errors.")
    else:
        logger.warning("No events scraped. Exiting task.")