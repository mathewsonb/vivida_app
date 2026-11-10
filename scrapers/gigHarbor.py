"""
gigHarbor.py
Autonomous Scraper for Visit Gig Harbor Events (The Events Calendar / Tribe).
Refactored for Xvfb headless execution and isolated Supabase ingestion.
"""
import logging
import os
import re
import sys
from typing import Optional

from bs4 import BeautifulSoup
import pandas as pd
from pyvirtualdisplay import Display
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
    format="%(asctime)s [%(levelname)s] [%(filename)s:%(lineno)d] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("scrape_gig_harbor")

BASE_LIST_URL = "https://visitgigharbor.com/events-festivals/list/page/"
DEFAULT_VENUE = "Gig Harbor, WA"
DEFAULT_ADDRESS = "Gig Harbor, WA"


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
    logger.info("==================================================================")
    logger.info(f"[START] Initializing PyVirtualDisplay server for Visit Gig Harbor (scanning up to {max_pages} pages)...")
    logger.info("==================================================================")
    
    display = Display(visible=0, size=(1920, 1080))
    display.start()

    events_data = []
    detail_urls = []
    driver = None

    try:
        chrome_opts = get_headless_chrome_options()
        driver = webdriver.Chrome(options=chrome_opts)

        # 1. Collect detail page URLs across pagination loop
        for page in range(1, max_pages + 1):
            page_url = f"{BASE_LIST_URL}{page}/"
            logger.info(f"[PAGINATION] Scanning list page {page}: {page_url}")
            
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

            page_urls_count = 0
            for block in title_blocks:
                anchor = block.find("a", href=True)
                if anchor and anchor["href"]:
                    detail_urls.append(anchor["href"].strip())
                    page_urls_count += 1

            if page_urls_count == 0:
                break

        # Deduplicate and sort collected URLs
        detail_urls = sorted(set(detail_urls))
        logger.info(f"[PARSER SUCCESS] Extracted {len(detail_urls)} unique event detail URLs. Beginning detail traversal...")

        now = pd.Timestamp.now()

        # 2. Iterate through detail URLs using the same driver instance
        for idx, url in enumerate(detail_urls, start=1):
            logger.info(f"--- [EVENT {idx}/{len(detail_urls)}] Navigating to detail page: {url} ---")
            
            det_soup = fetch_rendered_page(
                driver, 
                url, 
                wait_selector=".tribe-events-single, #tribe-events-content, div.entry-content"
            )
            
            if not det_soup:
                logger.warning(f"[ITEM SKIP] Failed to parse detail DOM for {url}")
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
                "address": DEFAULT_ADDRESS,
                "title": None,
                "start_time": None,
                "description": None,
                "big_desc": None,
                "external_url": url,
                "image_url": None,
                "ticket_link": url
            }

            # Title
            h1_tag = target_section.find("h1")
            if h1_tag:
                item_data["title"] = h1_tag.get_text(strip=True)

            # Venue Name (Tribe layout uses h3 or tribe-venue class)
            h3_tag = target_section.find("span", class_=re.compile(r"tribe-venue")) or target_section.find("dd", class_=re.compile(r"tribe-venue"))
            if h3_tag:
                item_data["venue_name"] = h3_tag.get_text(strip=True)

            # Start Time Parsing with Year Rollover
            date_span = target_section.find("span", class_=re.compile(r"tribe-events-schedule__date--start|tribe-event-date-start")) or target_section.find("abbr", class_=re.compile(r"tribe-events-abbr"))
            if date_span:
                raw_date_str = date_span.get_text(strip=True)
                try:
                    # Strip day names (e.g. "Monday, ")
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
            logger.info(f"  Title     : {item_data['title']}")
            logger.info(f"  Start Date: {item_data['start_time']}")

    except Exception as e:
        logger.error(f"[FATAL ERROR] Exception encountered during scraping loop: {e}", exc_info=True)
    finally:
        if driver:
            logger.info("[CLEANUP] Closing Chrome driver instance...")
            try:
                driver.quit()
            except Exception:
                pass
        logger.info("[CLEANUP] Stopping PyVirtualDisplay server...")
        display.stop()

    if not events_data:
        return pd.DataFrame()

    return pd.DataFrame(events_data)


if __name__ == "__main__":
    logger.info("Executing Visit Gig Harbor autonomous scraper pipeline task...")
    raw_df = scraper(max_pages=5)

    if not raw_df.empty:
        logger.info("==================================================================")
        logger.info(f"[PIPELINE HANDOFF] Passing {len(raw_df)} event records to VividaIngestionPipeline...")
        logger.info("==================================================================")
        
        pipeline = VividaIngestionPipeline(table_name="events")
        success = pipeline.run_standalone_pipeline(raw_df)
        
        if success:
            logger.info("[PIPELINE SUCCESS] Visit Gig Harbor scraper pipeline execution completed successfully.")
        else:
            logger.error("[PIPELINE ERROR] Visit Gig Harbor scraper pipeline execution reported errors.")
    else:
        logger.warning("[EXIT] No events scraped. Pipeline handoff skipped.")