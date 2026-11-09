"""
tacomaDome.py
Autonomous Scraper for Tacoma Dome (Tacoma, WA).
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
logger = logging.getLogger("scrape_tacoma_dome")

BASE_URL = "https://www.tacomadome.org"
TARGET_URL = "https://www.tacomadome.org/events"
DEFAULT_VENUE = "Tacoma Dome"
DEFAULT_ADDRESS = "2727 E D St, Tacoma, WA 98421"


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
    """Scrapes Tacoma Dome events using a single headless Chrome instance and returns raw DataFrame."""
    logger.info(f"Starting Tacoma Dome scrape from {TARGET_URL}...")
    events_data = []

    driver = None
    try:
        chrome_opts = get_headless_chrome_options()
        driver = webdriver.Chrome(options=chrome_opts)

        # 1. Fetch main event list container
        soup = fetch_rendered_page(
            driver, 
            TARGET_URL, 
            wait_selector="div.full_column, div.eventItem"
        )
        if not soup:
            logger.error("Could not load main events page.")
            return pd.DataFrame()

        target_section = (
            soup.find("div", class_=re.compile(r"full_column|non-widget-area")) or 
            soup
        )
        
        # Strip script and style elements
        for element in target_section(["script", "style"]):
            element.decompose()

        event_items = target_section.find_all("div", class_=re.compile(r"^eventItem.*clearfix$"))
        logger.info(f"Found {len(event_items)} Tacoma Dome event blocks.")

        # 2. Iterate event blocks and step into detail pages
        for event in event_items:
            item_data = {
                "venue_name": DEFAULT_VENUE,
                "address": DEFAULT_ADDRESS,
                "title": None,
                "start_time": None,
                "raw_date_str": None,
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

            # External URL
            anchor = event.find("a", href=True)
            if anchor:
                href = anchor["href"].strip()
                item_data["external_url"] = href if href.startswith("http") else f"{BASE_URL}{href if href.startswith('/') else '/' + href}"

            # Raw Date string from aria-label or text
            date_div = event.find("div", class_=re.compile(r"date"))
            if date_div:
                item_data["raw_date_str"] = date_div.get("aria-label") or date_div.get_text(strip=True)

            # Short Description
            h4_tag = event.find("h4")
            if h4_tag:
                item_data["description"] = h4_tag.get_text(strip=True, separator=" ")

            # Background Image URL
            bg_div = event.find("div", class_=re.compile(r"background-img"))
            if bg_div and bg_div.get("style"):
                item_data["image_url"] = extract_css_bg_image(bg_div["style"])

            # Ticket Link
            t_anchor = event.find("a", class_=re.compile(r"tickets|onsalenow", re.IGNORECASE))
            if t_anchor and t_anchor.get("href"):
                item_data["ticket_link"] = t_anchor["href"].strip()

            # 3. Step into Detail Page for rich text
            if item_data["external_url"]:
                det_soup = fetch_rendered_page(
                    driver, 
                    item_data["external_url"], 
                    wait_selector="main, div.detail-content, div.full_column"
                )
                
                if det_soup:
                    rich_text = fetch_event_text(det_soup)
                    if rich_text:
                        item_data["big_desc"] = re.sub(r"\s+", " ", rich_text)
                    else:
                        item_data["big_desc"] = item_data["description"]
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

    td_df = pd.DataFrame(events_data)

    # 4. Multi-day Date Range Extraction & Formatting
    if "raw_date_str" in td_df.columns:
        td_df[["first_day", "last_day"]] = (
            td_df["raw_date_str"].astype(str).str.split(" to ", expand=True).apply(lambda col: col.str.strip())
        )

        extracted_year = td_df["last_day"].str.extract(r"(\b\d{4}\b)", expand=False)
        needs_year = extracted_year.notna()

        td_df.loc[needs_year, "first_day"] = (
            td_df.loc[needs_year, "first_day"].str.strip() + " " + extracted_year[needs_year]
        )

        td_df["first_day"] = pd.to_datetime(td_df["first_day"], errors="coerce")
        td_df["last_day"] = pd.to_datetime(td_df["last_day"], errors="coerce")

        def format_dt(val):
            return val.strftime("%Y-%m-%d") if pd.notna(val) else None

        td_df["start_time"] = [
            [format_dt(x), format_dt(y)] if pd.notna(y) else [format_dt(x)]
            for x, y in zip(td_df["first_day"], td_df["last_day"])
        ]

        td_df = td_df.drop(columns=["first_day", "last_day", "raw_date_str"], errors="ignore")

    return td_df


if __name__ == "__main__":
    logger.info("Executing Tacoma Dome scraper task...")
    raw_df = scraper()

    if not raw_df.empty:
        # Run autonomous pipeline transformation & Supabase upsert
        pipeline = VividaIngestionPipeline(table_name="events")
        success = pipeline.run_standalone_pipeline(raw_df)
        if success:
            logger.info("Tacoma Dome scraper execution completed successfully.")
        else:
            logger.error("Tacoma Dome scraper pipeline execution reported errors.")
    else:
        logger.warning("No events scraped. Exiting task.")