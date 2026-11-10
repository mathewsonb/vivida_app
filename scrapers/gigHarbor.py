"""
scrape_gig_harbor.py
Autonomous Scraper for Visit Gig Harbor Events (The Events Calendar / Tribe).
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
logger = logging.getLogger("scrape_gig_harbor")

BASE_LIST_URL = "https://visitgigharbor.com/events-festivals/list/page/"
DEFAULT_VENUE = "Gig Harbor, WA"

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

def scraper(max_pages: int = 5) -> pd.DataFrame:
    """Scrapes Visit Gig Harbor events using a single headless Chrome instance and returns raw DataFrame."""
    logger.info(f"Starting Visit Gig Harbor scrape (scanning up to {max_pages} pages)...")
    
    # Initialize container out of try block to avoid UnboundLocalError
    events_data = []
    detail_urls = []
    driver = None

    try:
        chrome_opts = get_headless_chrome_options()
        driver = webdriver.Chrome(options=chrome_opts)

        # 1. Collect detail page URLs across pagination loop
        for page in range(1, max_pages + 1):
            page_url = f"{BASE_LIST_URL}{page}/"
            logger.info(f"Scanning list page {page}: {page_url}")
            
            soup = fetch_rendered_page(
                driver, 
                page_url, 
                wait_selector="h4.tribe-events-calendar-list__event-title, .tribe-events-calendar-list"
            )
            
            if not soup:
                logger.warning(f"Failed to retrieve page {page}. Ending pagination scan.")
                break

            title_blocks = soup.find_all(
                "h4", 
                class_=re.compile(r"tribe-events-calendar-list__event-title")
            )
            
            if not title_blocks:
                logger.info(f"No further event items found on page {page}.")
                break

            for block in title_blocks:
                anchor = block.find("a", href=True)
                if anchor and anchor["href"]:
                    detail_urls.append(anchor["href"].strip())

        # Deduplicate and sort collected URLs
        detail_urls = sorted(set(detail_urls))
        logger.info(f"Extracted {len(detail_urls)} unique event detail URLs.")

        now = pd.Timestamp.now()

        # 2. Iterate through detail URLs using the same driver instance
        for url in detail_urls:
            det_soup = fetch_rendered_page(
                driver, 
                url, 
                wait_selector=".tribe-events-single, #tribe-events-content, div.entry-content"
            )
            
            if not det_soup:
                continue

            target_section = (
                det_soup.find("div", class_=re.compile(r"tribe-events-single|entry-content")) or 
                det_soup.find("div", id="tribe-events-content") or 
                det_soup
            )

            # Strip script/style nodes
            for element in target_section(["script", "style"]):
                element.decompose()

            item_data = {
                "venue_name": DEFAULT_VENUE,
                "address": None,
                "title": None,
                "start_time": None,
                "description": None,
                "big_desc": None,
                "external_url": url,
                "image_url": None,
                "ticket_link": None
            }

            # Title
            h1_tag = target_section.find("h1")
            if h1_tag:
                item_data["title"] = h1_tag.get_text(strip=True)

            # Venue Name (Tribe layout uses h3 or tribe-venue class)
            h3_tag = target_section.find("h3") or target_section.find("span", class_=re.compile(r"tribe-venue"))
            if h3_tag:
                item_data["venue_name"] = h3_tag.get_text(strip=True)

            # Start Time Parsing with Year Rollover
            date_span = target_section.find("span", class_=re.compile(r"tribe-events-schedule__date--start|tribe-event-date-start"))
            if date_span:
                raw_date_str = date_span.get_text(strip=True)
                try:
                    full_date_str = f"{raw_date_str}, {now.year}" if str(now.year) not in raw_date_str else raw_date_str
                    start_dt = pd.to_datetime(full_date_str, errors="coerce", format="mixed")
                    
                    if pd.notnull(start_dt):
                        if start_dt.month < now.month and str(now.year) not in raw_date_str:
                            start_dt = start_dt + pd.DateOffset(years=1)
                        item_data["start_time"] = [start_dt.strftime("%Y-%m-%d")]
                except Exception as e:
                    logger.warning(f"Failed to parse date '{raw_date_str}': {e}")

            # Primary Short Description (First paragraph)
            p_tag = target_section.find("p", class_=re.compile(r"wp-block-paragraph")) or target_section.find("p")
            if p_tag:
                item_data["description"] = p_tag.get_text(strip=True)

            # Expanded Description (Full clean text)
            rich_text = fetch_event_text(target_section)
            if rich_text:
                item_data["big_desc"] = re.sub(r"\s+", " ", rich_text)
            else:
                item_data["big_desc"] = item_data["description"]

            # Image URL
            img_tag = target_section.find("img", src=True)
            if img_tag and img_tag["src"]:
                item_data["image_url"] = img_tag["src"].strip()

            # Address Parsing (Tribe microdata format)
            address_sec = target_section.find("address", class_=re.compile(r"tribe-block__venue__address|tribe-events-address"))
            if address_sec:
                try:
                    street = address_sec.find("span", class_=re.compile(r"tribe-street-address"))
                    locality = address_sec.find("span", class_=re.compile(r"tribe-locality"))
                    region = address_sec.find(["abbr", "span"], class_=re.compile(r"tribe-region"))
                    postal = address_sec.find("span", class_=re.compile(r"tribe-postal-code"))

                    st_str = street.get_text(strip=True) if street else ""
                    loc_str = locality.get_text(strip=True) if locality else "Gig Harbor"
                    reg_str = region.get_text(strip=True) if region else "WA"
                    post_str = postal.get_text(strip=True) if postal else ""

                    addr_parts = [p for p in [st_str, loc_str, f"{reg_str} {post_str}".strip()] if p]
                    item_data["address"] = ", ".join(addr_parts)
                except Exception:
                    item_data["address"] = address_sec.get_text(separator=", ", strip=True)

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
    logger.info("Executing Visit Gig Harbor scraper task...")
    raw_df = scraper(max_pages=5)

    if not raw_df.empty:
        pipeline = VividaIngestionPipeline(table_name="events")
        success = pipeline.run_standalone_pipeline(raw_df)
        if success:
            logger.info("Visit Gig Harbor scraper execution completed successfully.")
        else:
            logger.error("Visit Gig Harbor scraper pipeline execution reported errors.")
    else:
        logger.warning("No events scraped. Exiting task.")