"""
tacomaTheaters.py
Autonomous Scraper for Tacoma City Theaters (Pantages, Rialto, Theatre on the Square).
Refactored for Xvfb headless execution, infinite load-more pagination, and Supabase ingestion.
"""

import logging
import re
import sys
import time
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
    format_raw_date,
    get_headless_chrome_options,
)

# Configure logging for VM execution
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("scrape_tacoma_city_theaters")

BASE_URL = "https://www.tacomacitytheaters.org"
TARGET_URL = "https://www.tacomacitytheaters.org/events"
DEFAULT_VENUE = "Tacoma City Theaters"
DEFAULT_ADDRESS = "901 Broadway, Tacoma, WA 98402"


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
    """Scrapes Tacoma City Theaters by fully expanding event pagination using a single headless Chrome instance."""
    logger.info(f"Starting Tacoma City Theaters scrape from {TARGET_URL}...")
    events_data = []

    driver = None
    try:
        chrome_opts = get_headless_chrome_options()
        driver = webdriver.Chrome(options=chrome_opts)

        logger.info("Navigating to events calendar page...")
        driver.get(TARGET_URL)

        # 1. Wait for event container
        try:
            WebDriverWait(driver, 12).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "div.eventList, div.eventItem"))
            )
        except Exception:
            logger.warning("Timed out waiting for initial event container to load.")

        # 2. Iterate "Load More" pagination button until all events unfold
        load_clicks = 0
        while True:
            try:
                button = driver.find_element(By.ID, "loadMoreEvents")
                if button.is_displayed() and button.is_enabled():
                    driver.execute_script("arguments[0].scrollIntoView(true);", button)
                    driver.execute_script("arguments[0].click();", button)
                    load_clicks += 1
                    time.sleep(2.5)
                else:
                    logger.info("All events unfolded. 'Load More' button disabled or hidden.")
                    break
            except Exception:
                logger.info(f"No further 'Load More' button found. Total pagination clicks: {load_clicks}.")
                break

        # Parse fully unfolded DOM
        cleaned_html = clean_html_source(driver.page_source)
        soup = BeautifulSoup(cleaned_html, "html.parser")

        target_section = (
            soup.find("div", class_=re.compile(r"eventList")) or 
            soup.find("div", class_=re.compile(r"event_listing_page")) or 
            soup
        )

        for element in target_section(["script", "style"]):
            element.decompose()

        event_items = target_section.find_all("div", class_=re.compile(r"^eventItem"))
        logger.info(f"Found {len(event_items)} unfolded Tacoma City Theaters event cards.")

        # 3. Process event items and navigate to detail pages
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
            h3_tag = event.find("h3")
            if h3_tag:
                item_data["title"] = h3_tag.get_text(strip=True)
                anchor = h3_tag.find("a", href=True)
                if anchor:
                    href = anchor["href"].strip()
                    item_data["external_url"] = href if href.startswith("http") else f"{BASE_URL}{href if href.startswith('/') else '/' + href}"

            # Venue Branch Name (Pantages, Rialto, etc.)
            loc_div = event.find("div", class_=re.compile(r"location"))
            if loc_div:
                item_data["venue_name"] = loc_div.get_text(strip=True)

            # Date parsing
            date_div = event.find("div", class_=re.compile(r"date"))
            if date_div:
                raw_date_str = date_div.get_text(strip=True)
                try:
                    formatted_dt = format_raw_date(raw_date_str)
                    if formatted_dt:
                        item_data["start_time"] = [formatted_dt]
                except Exception as e:
                    logger.warning(f"Failed to format raw date '{raw_date_str}': {e}")

            # Short Description / Tagline
            h4_tag = event.find("h4", class_=re.compile(r"tagline"))
            if h4_tag:
                item_data["description"] = h4_tag.get_text(strip=True)

            # Image URL
            img_tag = event.find("img", src=True)
            if img_tag and img_tag["src"]:
                src = img_tag["src"].strip()
                item_data["image_url"] = src if src.startswith("http") else f"{BASE_URL}{src if src.startswith('/') else '/' + src}"

            # Ticket Link
            t_anchor = event.find("a", href=re.compile(r"ticket|buy|tessitura", re.IGNORECASE))
            if t_anchor and t_anchor.get("href"):
                item_data["ticket_link"] = t_anchor["href"].strip()

            # 4. Step into Detail Page for rich content using the same driver
            if item_data["external_url"]:
                det_soup = fetch_rendered_page(
                    driver, 
                    item_data["external_url"], 
                    wait_selector="div.leftColumn, div.event-description, main"
                )
                
                if det_soup:
                    container = (
                        det_soup.find("div", class_=re.compile(r"leftColumn")) or 
                        det_soup.find("div", class_=re.compile(r"event-description")) or 
                        det_soup.find("main") or 
                        det_soup
                    )
                    
                    rich_text = fetch_event_text(container)
                    if rich_text:
                        item_data["big_desc"] = re.sub(r"\s+", " ", rich_text)
                    else:
                        item_data["big_desc"] = item_data["description"]

                    if not item_data["ticket_link"]:
                        t_btn = det_soup.find("a", href=re.compile(r"ticket|buy|tessitura", re.IGNORECASE))
                        if t_btn and t_btn.get("href"):
                            item_data["ticket_link"] = t_btn["href"].strip()
            else:
                item_data["big_desc"] = item_data["description"]

            events_data.append(item_data)

    except Exception as e:
        logger.error(f"Error during Tacoma City Theaters scraping loop: {e}")
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
    logger.info("Executing Tacoma City Theaters scraper task...")
    raw_df = scraper()

    if not raw_df.empty:
        # Run autonomous pipeline transformation & Supabase upsert
        pipeline = VividaIngestionPipeline(table_name="events")
        success = pipeline.run_standalone_pipeline(raw_df)
        if success:
            logger.info("Tacoma City Theaters scraper execution completed successfully.")
        else:
            logger.error("Tacoma City Theaters scraper pipeline execution reported errors.")
    else:
        logger.warning("No events scraped. Exiting task.")