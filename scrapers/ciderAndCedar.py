"""
ciderAndCedar.py
Autonomous Scraper for Cider & Cedar (Tacoma, WA).
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
    get_headless_chrome_options,
)

# Configure logging for VM execution
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("scrape_cider_and_cedar")

TARGET_URL = "https://www.ciderandcedar.com/events"
DEFAULT_VENUE = "Cider & Cedar"
DEFAULT_ADDRESS = "744 Market St UNIT 102B, Tacoma, WA 98402"
DEFAULT_IMAGE_URL = (
    "https://images.squarespace-cdn.com/content/v1/679bb91621194f0e31b3c085/"
    "54ecdaa4-d251-44d5-b175-1b48f87aae9d/peacesignc%26c.png?format=1500w"
)


def scraper() -> pd.DataFrame:
    """Scrapes Cider & Cedar events from Squarespace layout and returns raw DataFrame."""
    logger.info(f"Starting Cider & Cedar scrape from {TARGET_URL}...")
    events_data = []
    driver = None

    try:
        chrome_opts = get_headless_chrome_options()
        driver = webdriver.Chrome(options=chrome_opts)
        driver.get(TARGET_URL)

        # Wait for Squarespace user-items-list container to render
        try:
            WebDriverWait(driver, 12).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "div.user-items-list, ul.user-items-list-item-container"))
            )
        except Exception:
            logger.warning("Timed out waiting for Squarespace list container selector.")

        cleaned_html = clean_html_source(driver.page_source)
        soup = BeautifulSoup(cleaned_html, "html.parser")

    except Exception as e:
        logger.error(f"Headless browser execution failed for {TARGET_URL}: {e}")
        return pd.DataFrame()
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass

    # Locate container
    target_section = soup.find("div", class_=re.compile(r"user-items-list"))
    if not target_section:
        logger.warning("Could not find 'user-items-list' container in parsed HTML.")
        return pd.DataFrame()

    # Strip script/style nodes
    for script in target_section(["script", "style"]):
        script.decompose()

    event_items = target_section.find_all("li")
    logger.info(f"Found {len(event_items)} event cards.")

    now = pd.Timestamp.now()

    for event in event_items:
        item_data = {
            "venue_name": DEFAULT_VENUE,
            "address": DEFAULT_ADDRESS,
            "title": None,
            "start_time": None,
            "description": None,
            "big_desc": None,
            "external_url": TARGET_URL,
            "image_url": DEFAULT_IMAGE_URL,
            "ticket_link": None
        }

        # 1. Title
        h2_tag = event.find(["h2", "h3", "h4"])
        if h2_tag:
            item_data["title"] = h2_tag.get_text(strip=True)

        # 2. Extract Card Image if Available
        img_tag = event.find("img", src=True) or event.find("img", attrs={"data-src": True})
        if img_tag:
            src_val = img_tag.get("src") or img_tag.get("data-src")
            if src_val and str(src_val).startswith("http"):
                item_data["image_url"] = src_val.strip()

        # 3. Date & Description Paragraphs
        paragraphs = event.find_all("p")
        
        # Parse Date (Paragraph 0)
        if len(paragraphs) >= 1:
            raw_date_str = paragraphs[0].get_text(strip=True)
            try:
                # Strip out day names like Monday, Tue, etc.
                clean_date_str = re.sub(
                    r"^(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday|Mon|Tue|Wed|Thu|Fri|Sat|Sun)[s\,]?\s*",
                    "",
                    raw_date_str,
                    flags=re.IGNORECASE
                ).strip()

                full_date_str = f"{clean_date_str}, {now.year}" if str(now.year) not in clean_date_str else clean_date_str
                start_dt = pd.to_datetime(full_date_str, errors="coerce", format="mixed")
                
                if pd.notnull(start_dt):
                    if start_dt.month < now.month and str(now.year) not in clean_date_str:
                        start_dt = start_dt + pd.DateOffset(years=1)
                    item_data["start_time"] = [start_dt.strftime("%Y-%m-%d")]
            except Exception as e:
                logger.warning(f"Failed to parse date string '{raw_date_str}': {e}")

        # Parse Description (Paragraph 1+)
        if len(paragraphs) >= 2:
            desc_parts = [p.get_text(strip=True) for p in paragraphs[1:] if p.get_text(strip=True)]
            combined_desc = " ".join(desc_parts)
            cleaned_desc = re.sub(r"\s+", " ", combined_desc)
            item_data["description"] = cleaned_desc
            item_data["big_desc"] = cleaned_desc

        events_data.append(item_data)

    if not events_data:
        return pd.DataFrame()

    return pd.DataFrame(events_data)


if __name__ == "__main__":
    logger.info("Executing Cider & Cedar scraper task...")
    raw_df = scraper()

    if not raw_df.empty:
        pipeline = VividaIngestionPipeline(table_name="events")
        success = pipeline.run_standalone_pipeline(raw_df)
        if success:
            logger.info("Cider & Cedar scraper execution completed successfully.")
        else:
            logger.error("Cider & Cedar scraper pipeline execution reported errors.")
    else:
        logger.warning("No events scraped. Exiting task.")