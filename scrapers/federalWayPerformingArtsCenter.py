"""
scrape_fwpaec.py
Autonomous Scraper for Federal Way Performing Arts & Event Center (FWPAEC).
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
logger = logging.getLogger("scrape_fwpaec")

TARGET_URL = "https://fwpaec.org/event-calendar/"
DEFAULT_VENUE = "Federal Way Performing Arts & Event Center"
DEFAULT_ADDRESS = "31510 Pete von Reichbauer Way S, Federal Way, WA 98003"


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
    """Scrapes FWPAEC events using a single headless Chrome instance and returns raw DataFrame."""
    logger.info(f"Starting FWPAEC scrape from {TARGET_URL}...")
    events_data = []

    driver = None
    try:
        chrome_opts = get_headless_chrome_options()
        driver = webdriver.Chrome(options=chrome_opts)

        # 1. Fetch main calendar page
        soup = fetch_rendered_page(
            driver, 
            TARGET_URL, 
            wait_selector="div.eael-tabs-content, div.mec-wrap"
        )
        if not soup:
            logger.error("Could not load main events page.")
            return pd.DataFrame()

        target_section = soup.find("div", class_="eael-tabs-content") or soup.find("div", class_=re.compile(r"mec-wrap"))
        if not target_section:
            logger.warning("Could not locate calendar tabs container.")
            return pd.DataFrame()

        # Clean script/style elements
        for element in target_section(["script", "style"]):
            element.decompose()

        calendar_tab = target_section.find("div", id="full-event-calendar-tab") or target_section
        event_items = calendar_tab.find_all("div", class_=re.compile(r"col-md-3|col-sm-3|mec-grid-item"))
        logger.info(f"Found {len(event_items)} MEC event grid items.")

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

            # Extract Title & External URL
            h4_tag = event.find("h4")
            if h4_tag:
                item_data["title"] = h4_tag.get_text(strip=True, separator=" ")
                anchor = h4_tag.find("a", href=True)
                if anchor:
                    item_data["external_url"] = anchor["href"].strip()

            # Extract Venue Location Override if present
            loc_div = event.find("div", class_=re.compile(r"mec-event-loc-place"))
            if loc_div:
                item_data["venue_name"] = loc_div.get_text(strip=True, separator=" ")

            # Address text override if present
            loc_p = event.find("p", class_=re.compile(r"mec-grid-event-location"))
            if loc_p:
                item_data["address"] = loc_p.get_text(strip=True)

            # Extract Date & Apply Year Rollover Logic
            date_label = event.find("span", class_=re.compile(r"mec-start-date-label"))
            if date_label:
                raw_date_str = date_label.get_text(strip=True)
                try:
                    full_date_str = f"{raw_date_str} {now.year}"
                    parsed_dt = pd.to_datetime(full_date_str, errors="coerce", format="mixed")
                    
                    if pd.notnull(parsed_dt):
                        if parsed_dt.month < now.month:
                            parsed_dt = parsed_dt + pd.DateOffset(years=1)
                        item_data["start_time"] = [parsed_dt.strftime("%Y-%m-%d")]
                except Exception as e:
                    logger.warning(f"Failed to parse date string '{raw_date_str}': {e}")

            # Extract Primary Summary
            desc_elem = event.find("div", class_=re.compile(r"mec-event-description|excerpt|content"))
            if desc_elem:
                item_data["description"] = desc_elem.get_text(strip=True, separator=" ")

            # Extract Image URL (checking data-src first, falling back to src)
            image_sec = event.find("div", class_=re.compile(r"mec-event-image"))
            if image_sec:
                img_tag = image_sec.find("img")
                if img_tag:
                    src = img_tag.get("data-src") or img_tag.get("src")
                    if src:
                        item_data["image_url"] = src.strip()

            # Extract Ticket Link
            t_btn = event.find("a", class_=re.compile(r"ticket|booking|buy", re.I))
            if t_btn and t_btn.get("href"):
                item_data["ticket_link"] = t_btn.get("href").strip()

            # 3. Step into Detail Page for expanded text
            if item_data["external_url"]:
                det_soup = fetch_rendered_page(
                    driver, 
                    item_data["external_url"], 
                    wait_selector="div.mec-single-event-description, div.entry-content, main"
                )
                
                if det_soup:
                    container = (
                        det_soup.find("div", class_=re.compile(r"mec-single-event-description|mec-events-content")) or
                        det_soup.find("div", class_="entry-content") or
                        det_soup.find("main")
                    )

                    if container:
                        rich_text = fetch_event_text(container)
                        if rich_text:
                            item_data["big_desc"] = re.sub(r"\s+", " ", rich_text)
                        else:
                            item_data["big_desc"] = item_data["description"]
                    else:
                        item_data["big_desc"] = item_data["description"]
            else:
                item_data["big_desc"] = item_data["description"]

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
    logger.info("Executing FWPAEC scraper task...")
    raw_df = scraper()

    if not raw_df.empty:
        # Run autonomous pipeline transformation & Supabase upsert
        pipeline = VividaIngestionPipeline(table_name="events")
        success = pipeline.run_standalone_pipeline(raw_df)
        if success:
            logger.info("FWPAEC scraper execution completed successfully.")
        else:
            logger.error("FWPAEC scraper pipeline execution reported errors.")
    else:
        logger.warning("No events scraped. Exiting task.")