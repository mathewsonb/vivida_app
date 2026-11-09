"""
scrape_mcmenamins.py
Autonomous Scraper for McMenamins Elks Temple / Spanish Ballroom (Tacoma, WA).
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
logger = logging.getLogger("scrape_mcmenamins")

BASE_URL = "https://www.mcmenamins.com"
TARGET_URL = "https://www.mcmenamins.com/elks-temple/spanish-ballroom"
DEFAULT_VENUE = "McMenamins Elks Temple"
DEFAULT_ADDRESS = "565 Broadway, Tacoma, WA 98402"


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
        return url if url.startswith("http") else f"{BASE_URL}{url}"
    return None


def extract_ticket_url(element) -> Optional[str]:
    """Extracts external ticket link from buttons or anchors via href/onclick regex."""
    if not element:
        return None
    
    href = element.get("href")
    if href and href.startswith("http"):
        return href.strip()

    onclick = element.get("onclick")
    if onclick:
        match = re.search(r"https?://[^\s'\"]+", onclick)
        if match:
            return match.group(0).strip()
    return None


def scraper() -> pd.DataFrame:
    """Scrapes McMenamins events using a single headless Chrome instance and returns raw DataFrame."""
    logger.info(f"Starting McMenamins Elks Temple scrape from {TARGET_URL}...")
    events_data = []

    driver = None
    try:
        chrome_opts = get_headless_chrome_options()
        driver = webdriver.Chrome(options=chrome_opts)

        # 1. Fetch main calendar container
        soup = fetch_rendered_page(driver, TARGET_URL, wait_selector="#full_calendar, div.calendar-item")
        if not soup:
            logger.error("Could not load main events calendar.")
            return pd.DataFrame()

        target_section = soup.find("div", id="full_calendar") or soup
        
        # Strip script and style tags from primary section
        for element in target_section(["script", "style"]):
            element.decompose()

        event_items = target_section.find_all("div", class_=re.compile(r"^calendar-item"))
        logger.info(f"Found {len(event_items)} calendar event cards.")

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
            h3_tag = event.find("h3")
            if h3_tag:
                item_data["title"] = h3_tag.get_text(strip=True, separator=" ")

            # Date parsing with year rollover support
            time_p = event.find("p", class_=re.compile(r"uk-panel-time"))
            if time_p:
                raw_time_str = time_p.get_text(strip=True)
                try:
                    # Formats usually like "Friday, Nov 15"
                    parts = raw_time_str.split(",")
                    date_body = parts[1].strip() if len(parts) > 1 else raw_time_str
                    
                    full_date_str = f"{date_body}, {now.year}"
                    start_dt = pd.to_datetime(full_date_str, errors="coerce", format="mixed")

                    if pd.notnull(start_dt):
                        if start_dt.month < now.month:
                            start_dt = start_dt + pd.DateOffset(years=1)
                        item_data["start_time"] = [start_dt.strftime("%Y-%m-%d")]
                except Exception as e:
                    logger.warning(f"Failed to parse date '{raw_time_str}': {e}")

            # Short Description (from details button text or card body)
            details_btn = event.find(["button", "a"], class_=re.compile(r"uk-button-details"))
            if details_btn:
                item_data["description"] = re.sub(r"\s+", " ", details_btn.get_text(strip=True, separator=" "))

            # External URL
            anchor = event.find("a", href=True)
            if anchor:
                href = anchor["href"].strip()
                item_data["external_url"] = href if href.startswith("http") else f"{BASE_URL}{href}"

            # Image URL from background style
            panel_div = event.find("div", class_=re.compile(r"uk-panel"))
            if panel_div and panel_div.get("style"):
                item_data["image_url"] = extract_css_bg_image(panel_div["style"])

            # Ticket Link
            ticket_btn = event.find(["button", "a"], class_=re.compile(r"uk-button-primary"))
            if ticket_btn:
                item_data["ticket_link"] = extract_ticket_url(ticket_btn)

            # 3. Step into Detail Page for rich content
            if item_data["external_url"]:
                det_soup = fetch_rendered_page(
                    driver, 
                    item_data["external_url"], 
                    wait_selector="div.uk-width-1-1, main"
                )
                
                if det_soup:
                    container = (
                        det_soup.find("div", class_=re.compile(r"uk-width-xlarge-7-10")) or 
                        det_soup.find("main") or 
                        det_soup
                    )
                    rich_text = fetch_event_text(container)
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
    logger.info("Executing McMenamins Elks Temple scraper task...")
    raw_df = scraper()

    if not raw_df.empty:
        # Run autonomous pipeline transformation & Supabase upsert
        pipeline = VividaIngestionPipeline(table_name="events")
        success = pipeline.run_standalone_pipeline(raw_df)
        if success:
            logger.info("McMenamins scraper execution completed successfully.")
        else:
            logger.error("McMenamins scraper pipeline execution reported errors.")
    else:
        logger.warning("No events scraped. Exiting task.")