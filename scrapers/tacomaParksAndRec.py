"""
tacomaParksAndRec.py
Autonomous Scraper for Metro Parks Tacoma (The Events Calendar / Tribe).
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
logger = logging.getLogger("scrape_parks_tacoma")

BASE_URL = "https://www.parkstacoma.gov"
TARGET_URL = "https://www.parkstacoma.gov/events/list/"
DEFAULT_VENUE = "Metro Parks Tacoma"
DEFAULT_ADDRESS = "Tacoma, WA"


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


def parse_tribe_date(date_element) -> Optional[str]:
    """Safely extracts ISO-8601 date string from dynamic Tribe calendar date spans."""
    if not date_element:
        return None
    
    raw_str = date_element.get_text(strip=True)
    if not raw_str:
        return None

    try:
        dt = pd.to_datetime(raw_str, errors="coerce", format="mixed")
        if pd.notnull(dt):
            return dt.strftime("%Y-%m-%d")
    except Exception:
        pass
    return None


def scraper() -> pd.DataFrame:
    """Scrapes Metro Parks Tacoma events using a single headless Chrome instance and returns raw DataFrame."""
    logger.info(f"Starting Metro Parks Tacoma scrape from {TARGET_URL}...")
    events_data = []

    driver = None
    try:
        chrome_opts = get_headless_chrome_options()
        driver = webdriver.Chrome(options=chrome_opts)

        # 1. Fetch main event list container
        soup = fetch_rendered_page(
            driver, 
            TARGET_URL, 
            wait_selector="ul.tribe-events-calendar-list, div.tribe-common-g-row"
        )
        if not soup:
            logger.error("Could not load main Metro Parks Tacoma events page.")
            return pd.DataFrame()

        target_section = (
            soup.find("ul", class_=re.compile(r"tribe-events-calendar-list")) or 
            soup.find("div", id="tribe-events-pg-template") or 
            soup
        )
        
        # Strip script and style tags
        for element in target_section(["script", "style"]):
            element.decompose()

        event_items = target_section.find_all(
            "div", 
            class_=re.compile(r"tribe-events-calendar-list__event-row")
        )
        logger.info(f"Found {len(event_items)} Metro Parks Tacoma event rows.")

        # 2. Iterate event list rows and step into detail pages
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

            # Title & External URL
            h4_tag = event.find("h4")
            if h4_tag:
                item_data["title"] = h4_tag.get_text(strip=True, separator=" ")
                anchor = h4_tag.find("a", href=True)
                if anchor:
                    href = anchor["href"].strip()
                    item_data["external_url"] = href if href.startswith("http") else f"{BASE_URL}{href if href.startswith('/') else '/' + href}"

            # Venue Name
            venue_span = event.find("span", class_=re.compile(r"tribe-events-calendar-list__event-venue-title"))
            if venue_span:
                item_data["venue_name"] = venue_span.get_text(strip=True, separator=" ")

            # Address
            addr_span = event.find("span", class_=re.compile(r"tribe-events-calendar-list__event-venue-address"))
            if addr_span:
                item_data["address"] = addr_span.get_text(strip=True)

            # Start and End Date Parsing
            from_span = event.find("span", class_=re.compile(r"tribe-event-date-start"))
            to_span = event.find("span", class_=re.compile(r"tribe-event-date-end"))

            from_date = parse_tribe_date(from_span)
            to_date = parse_tribe_date(to_span)

            dates = [d for d in [from_date, to_date] if d]
            if dates:
                item_data["start_time"] = dates

            # Short Description
            desc_div = event.find("div", class_=re.compile(r"tribe-events-calendar-list__event-description"))
            if desc_div:
                item_data["description"] = desc_div.get_text(strip=True, separator=" ")

            # Card Image URL
            img_tag = event.find("img", src=True)
            if img_tag and img_tag["src"]:
                src = img_tag["src"].strip()
                item_data["image_url"] = src if src.startswith("http") else f"{BASE_URL}{src if src.startswith('/') else '/' + src}"

            # Ticket / Booking Link
            t_anchor = event.find("a", href=re.compile(r"ticket|register|buy", re.IGNORECASE))
            if t_anchor and t_anchor.get("href"):
                item_data["ticket_link"] = t_anchor["href"].strip()

            # 3. Step into Detail Page for rich content
            if item_data["external_url"]:
                det_soup = fetch_rendered_page(
                    driver, 
                    item_data["external_url"], 
                    wait_selector="section.event-details, div.tribe-events-single, main"
                )
                
                if det_soup:
                    container = (
                        det_soup.find("section", class_=re.compile(r"event-details")) or 
                        det_soup.find("div", class_=re.compile(r"tribe-events-single")) or 
                        det_soup.find("main") or 
                        det_soup
                    )
                    
                    rich_text = fetch_event_text(container)
                    if rich_text:
                        item_data["big_desc"] = re.sub(r"\s+", " ", rich_text)
                    else:
                        item_data["big_desc"] = item_data["description"]

                    if not item_data["ticket_link"]:
                        t_btn = det_soup.find("a", href=re.compile(r"ticket|register|buy", re.IGNORECASE))
                        if t_btn and t_btn.get("href"):
                            item_data["ticket_link"] = t_btn["href"].strip()
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
    logger.info("Executing Metro Parks Tacoma scraper task...")
    raw_df = scraper()

    if not raw_df.empty:
        # Run autonomous pipeline transformation & Supabase upsert
        pipeline = VividaIngestionPipeline(table_name="events")
        success = pipeline.run_standalone_pipeline(raw_df)
        if success:
            logger.info("Metro Parks Tacoma scraper execution completed successfully.")
        else:
            logger.error("Metro Parks Tacoma scraper pipeline execution reported errors.")
    else:
        logger.warning("No events scraped. Exiting task.")