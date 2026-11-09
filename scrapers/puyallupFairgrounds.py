"""
puyallupFairgrounds.py
Autonomous Scraper for Puyallup Fairgrounds / Washington State Fair (Puyallup, WA).
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
logger = logging.getLogger("scrape_puyallup_fair")

BASE_URL = "https://www.thefair.com"
TARGET_URL = "https://www.thefair.com/events-calendar/"
DEFAULT_VENUE = "Puyallup Fairgrounds"
DEFAULT_ADDRESS = "110 9th Ave SW, Puyallup, WA 98371"


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


def format_date_range(x: pd.Series) -> Optional[list]:
    """Safely formats grouped date series into single date or date range list."""
    valid = x.dropna()
    if valid.empty:
        return None
    min_d = pd.to_datetime(valid.min(), errors="coerce", format="mixed")
    max_d = pd.to_datetime(valid.max(), errors="coerce", format="mixed")
    
    if pd.isna(min_d):
        return None
        
    if min_d == max_d or pd.isna(max_d):
        return [min_d.strftime("%Y-%m-%d")]
    return [min_d.strftime("%Y-%m-%d"), max_d.strftime("%Y-%m-%d")]


def scraper() -> pd.DataFrame:
    """Scrapes Puyallup Fairgrounds events across dynamic pagination using a single headless Chrome instance."""
    logger.info(f"Starting Puyallup Fairgrounds scrape from {TARGET_URL}...")
    all_events = []

    driver = None
    try:
        chrome_opts = get_headless_chrome_options()
        driver = webdriver.Chrome(options=chrome_opts)

        page = 1
        while True:
            page_url = f"{TARGET_URL}?page={page}"
            logger.info(f"Scanning calendar page {page}: {page_url}")

            soup = fetch_rendered_page(
                driver, 
                page_url, 
                wait_selector="div.container--md2, div.event-card-large"
            )

            if not soup:
                logger.warning(f"Failed to fetch page {page}. Terminating pagination scan.")
                break

            target_container = soup.find("div", class_=re.compile(r"container--md2")) or soup
            event_items = target_container.find_all("div", class_=re.compile(r"event-card-large"))

            if not event_items:
                logger.info(f"No further event cards found on page {page}. Ending pagination.")
                break

            logger.info(f"Found {len(event_items)} event items on page {page}.")

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

                # 1. Event Title & External URL
                h3_tag = event.find("h3")
                if h3_tag:
                    item_data["title"] = h3_tag.get_text(strip=True, separator=" ")
                    anchor = h3_tag.find("a", href=True)
                    if anchor:
                        href = anchor["href"].strip()
                        item_data["external_url"] = href if href.startswith("http") else f"{BASE_URL}{href}"

                # 2. Date Text
                date_icon = event.find("div", class_=re.compile(r"icon-text"))
                if date_icon:
                    item_data["start_time"] = date_icon.get_text(strip=True)

                # 3. Image URL
                img_tag = event.find("img", src=True)
                if img_tag and img_tag["src"]:
                    src = img_tag["src"].strip()
                    item_data["image_url"] = src if src.startswith("http") else f"{BASE_URL}{src}"

                # 4. Description Summary
                span_desc = event.find("span")
                if span_desc:
                    item_data["description"] = span_desc.get_text(strip=True, separator=" ")

                # 5. Ticket Link from Card
                buttons_div = event.find("div", class_=re.compile(r"event-card-large__buttons"))
                if buttons_div:
                    t_anchor = buttons_div.find("a", href=re.compile(r"ticket", re.IGNORECASE))
                    if t_anchor and t_anchor.get("href"):
                        item_data["ticket_link"] = t_anchor["href"].strip()

                # 6. Deep Detail Render
                if item_data["external_url"]:
                    det_soup = fetch_rendered_page(
                        driver, 
                        item_data["external_url"], 
                        wait_selector="main, div.page-builder"
                    )
                    
                    if det_soup:
                        rich_text = fetch_event_text(det_soup)
                        if rich_text:
                            item_data["big_desc"] = re.sub(r"\s+", " ", rich_text)
                        else:
                            item_data["big_desc"] = item_data["description"]

                        # Fallback ticket link from detail page
                        if not item_data["ticket_link"]:
                            t_btn = det_soup.find("a", href=re.compile(r"ticket", re.IGNORECASE))
                            if t_btn and t_btn.get("href"):
                                item_data["ticket_link"] = t_btn["href"].strip()
                else:
                    item_data["big_desc"] = item_data["description"]

                all_events.append(item_data)

            page += 1

    except Exception as e:
        logger.error(f"Error during headless browser execution loop: {e}")
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass

    if not all_events:
        return pd.DataFrame()

    pfg_df = pd.DataFrame(all_events)

    # Date range cleaning & Groupby Aggregation
    if "start_time" in pfg_df.columns:
        pfg_df["clean_date"] = pd.to_datetime(
            pfg_df["start_time"].astype(str).str.split(r"-|to").str[0], 
            errors="coerce"
        )
        pfg_df = pfg_df.sort_values(by=["title", "clean_date"])

        pfg_df = pfg_df.groupby("title", as_index=False).agg({
            "clean_date": format_date_range,
            "external_url": "first",
            "image_url": "first",
            "ticket_link": "first",
            "description": "first",
            "address": "first",
            "big_desc": "first",
            "venue_name": "first"
        }).rename(columns={"clean_date": "start_time"})

    return pfg_df


if __name__ == "__main__":
    logger.info("Executing Puyallup Fairgrounds scraper task...")
    raw_df = scraper()

    if not raw_df.empty:
        # Run autonomous pipeline transformation & Supabase upsert
        pipeline = VividaIngestionPipeline(table_name="events")
        success = pipeline.run_standalone_pipeline(raw_df)
        if success:
            logger.info("Puyallup Fairgrounds scraper execution completed successfully.")
        else:
            logger.error("Puyallup Fairgrounds scraper pipeline execution reported errors.")
    else:
        logger.warning("No events scraped. Exiting task.")