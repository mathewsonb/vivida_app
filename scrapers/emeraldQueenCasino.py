"""
emeraldQueenCasino.py
Autonomous Scraper for Emerald Queen Casino (Tacoma, WA).
Refactored for Xvfb headless execution and isolated Supabase ingestion.
"""
import logging
import os
import re
import sys
from typing import Optional

from bs4 import BeautifulSoup
import pandas as pd
import requests
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
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
logger = logging.getLogger("scrape_eqc")

BASE_URL = "https://emeraldqueen.com"
TICKETS_URL = "https://emeraldqueen.com/tickets/"
DEFAULT_VENUE = "Emerald Queen Casino"
DEFAULT_ADDRESS = "2920 E R St, Tacoma, WA 98404"

def fetch_rendered_detail(driver: webdriver.Chrome, url: str, wait_for_selector: str = "#section-content", timeout: int = 10) -> Optional[BeautifulSoup]:
    """Navigates driver to detail page and extracts cleaned BeautifulSoup DOM."""
    try:
        driver.get(url)
        if wait_for_selector:
            try:
                WebDriverWait(driver, timeout).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, wait_for_selector))
                )
            except Exception:
                pass
        
        cleaned_html = clean_html_source(driver.page_source)
        return BeautifulSoup(cleaned_html, "html.parser")
    except Exception as e:
        logger.warning(f"Failed to fetch detail page for '{url}': {e}")
        return None

def scraper() -> pd.DataFrame:
    """Scrapes Emerald Queen Casino events, enriches details via Selenium, and returns raw DataFrame."""
    logger.info(f"Starting EQC scrape from {TICKETS_URL}...")
    events_data = []

    # 1. Primary Page Request via requests
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    try:
        response = requests.get(TICKETS_URL, headers=headers, timeout=15)
        response.raise_for_status()
    except Exception as e:
        logger.error(f"HTTP GET failed for {TICKETS_URL}: {e}")
        return pd.DataFrame()

    cleaned_main_html = clean_html_source(response.text)
    soup = BeautifulSoup(cleaned_main_html, "html.parser")

    target_table = soup.find("section", class_=re.compile(r"content.*moto-section"))
    if not target_table:
        logger.warning("Could not locate target section container on EQC tickets page.")
        return pd.DataFrame()

    event_items = target_table.find_all(
        "div", 
        class_=re.compile(r"moto-widget-row.*moto-spacing-top-medium")
    )
    logger.info(f"Found {len(event_items)} candidate event items on main list.")

    # 2. Reuse a single Headless Chrome Driver instance for all detail page calls
    driver = None
    try:
        chrome_opts = get_headless_chrome_options()
        # Locate system Chrome binary if inside Devcontainer/Codespace
        if os.path.exists("/usr/bin/google-chrome"):
            chrome_opts.binary_location = "/usr/bin/google-chrome"

        driver = webdriver.Chrome(options=chrome_opts)
    except Exception as err:
        logger.warning(f"Headless Chrome driver creation failed ({err}). Proceeding with primary card metadata extraction.")

    try:
        for event in event_items:
            item_data = {
                "venue_name": DEFAULT_VENUE,
                "address": DEFAULT_ADDRESS,
                "title": None,
                "start_time": None,
                "description": None,
                "external_url": None,
                "image_url": None,
                "ticket_link": None,
                "big_desc": None
            }

            # Batch extract primary card text
            details = event.find_all("div", class_=re.compile(r"moto-widget-text-content"))
            if details and len(details) >= 1:
                item_data["title"] = details[0].get_text(strip=True)
            if details and len(details) >= 2:
                item_data["start_time"] = details[1].get_text(strip=True).replace("•", "").strip()
            if details and len(details) >= 3:
                item_data["description"] = details[2].get_text(separator=" ", strip=True)

            # Extract relative external URL
            anchor = event.find("a", href=True)
            if anchor:
                href = anchor["href"]
                item_data["external_url"] = href if href.startswith("http") else f"{BASE_URL}{href}"

            # Extract image source
            img = event.find("img")
            if img:
                src = img.get("data-src") or img.get("src")
                if src:
                    item_data["image_url"] = src if src.startswith("http") else f"{BASE_URL}{src}"

            # Fetch enriched details using headless driver if available
            if driver and item_data["external_url"]:
                det_soup = fetch_rendered_detail(driver, item_data["external_url"])
                if det_soup:
                    # Find Ticketmaster link if present
                    tm_link = det_soup.find("a", href=re.compile(r"ticketmaster", re.IGNORECASE))
                    if tm_link and tm_link.get("href"):
                        item_data["ticket_link"] = tm_link.get("href")

                    # Extract cleaned rich text description
                    rich_text = fetch_event_text(det_soup)
                    if rich_text:
                        item_data["big_desc"] = re.sub(r"\s+", " ", rich_text)

            events_data.append(item_data)

    except Exception as e:
        logger.error(f"Error during execution loop: {e}")
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass

    if not events_data:
        return pd.DataFrame()

    eqc_df = pd.DataFrame(events_data)

    # 3. Datetime formatting
    if "start_time" in eqc_df.columns:
        parsed_dates = pd.to_datetime(eqc_df["start_time"], format="mixed", errors="coerce")
        eqc_df["start_time"] = [
            [dt.strftime("%Y-%m-%d")] if pd.notnull(dt) else None 
            for dt in parsed_dates
        ]

    return eqc_df

if __name__ == "__main__":
    logger.info("Executing Emerald Queen Casino scraper task...")
    raw_df = scraper()
    
    if not raw_df.empty:
        # Run autonomous pipeline transformation & Supabase upsert
        pipeline = VividaIngestionPipeline(table_name="events")
        success = pipeline.run_standalone_pipeline(raw_df)
        if success:
            logger.info("EQC scraper execution completed successfully.")
        else:
            logger.error("EQC scraper pipeline execution reported errors.")
    else:
        logger.warning("No events scraped. Exiting task.")