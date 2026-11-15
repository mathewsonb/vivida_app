"""
tacomaArtMuseum.py
Autonomous Scraper for Tacoma Art Museum (Tacoma, WA).
Refactored for Xvfb headless execution, multi-pattern date parsing, and isolated Supabase ingestion.
"""

import logging
import os
import re
import sys
from typing import Optional, List

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
logger = logging.getLogger("scrape_tam")

BASE_URL = "https://www.tacomaartmuseum.org"
TARGET_URL = "https://www.tacomaartmuseum.org/events/"
DEFAULT_VENUE = "Tacoma Art Museum"
DEFAULT_ADDRESS = "1701 Pacific Avenue, Tacoma, WA 98402"

MONTH_NAMES = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec|January|February|March|April|May|June|July|August|September|October|November|December)"


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


def parse_event_dates(date_text: str, now: pd.Timestamp) -> Optional[List[str]]:
    """Multi-pattern date parser supporting single dates, day ranges, and month rollovers."""
    if not date_text or pd.isna(date_text):
        return None

    clean_str = re.sub(r"\s+", " ", str(date_text).strip())

    # 1. Full date match with year (e.g., "December 5, 2026")
    full_date_match = re.search(rf"\b{MONTH_NAMES}\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s+\d{{4}}\b", clean_str, re.I)
    if full_date_match:
        dt = pd.to_datetime(full_date_match.group(0), errors="coerce")
        if pd.notnull(dt):
            return [dt.strftime("%Y-%m-%d")]

    # 2. Month and Day match without year (e.g., "Saturday, Dec 5" or "Dec 5")
    partial_date_match = re.search(rf"\b{MONTH_NAMES}\s+\d{{1,2}}(?:st|nd|rd|th)?\b", clean_str, re.I)
    if partial_date_match:
        full_str = f"{partial_date_match.group(0)}, {now.year}"
        dt = pd.to_datetime(full_str, errors="coerce")
        if pd.notnull(dt):
            if dt.month < now.month:
                dt = dt + pd.DateOffset(years=1)
            return [dt.strftime("%Y-%m-%d")]

    # 3. Handle Range Strings (e.g., "Dec 5 - 12" or "Nov 20 - Dec 2")
    range_parts = re.split(r"\s*[-–—\bto\b]\s*", clean_str, flags=re.IGNORECASE)
    try:
        if len(range_parts) >= 2:
            start_raw, end_raw = range_parts[0].strip(), range_parts[1].strip()
            
            # Carry month forward if end_raw is just a day number (e.g. "Dec 5-8")
            if re.match(r"^\d{1,2}$", end_raw):
                month_match = re.search(rf"\b{MONTH_NAMES}\b", start_raw, re.I)
                if month_match:
                    end_raw = f"{month_match.group(0)} {end_raw}"

            start_dt = pd.to_datetime(start_raw, errors="coerce", format="mixed")
            if pd.isnull(start_dt):
                start_dt = pd.to_datetime(f"{start_raw}, {now.year}", errors="coerce", format="mixed")

            end_dt = pd.to_datetime(end_raw, errors="coerce", format="mixed")
            if pd.isnull(end_dt):
                end_dt = pd.to_datetime(f"{end_raw}, {now.year}", errors="coerce", format="mixed")

            if pd.notnull(start_dt) and pd.notnull(end_dt):
                if start_dt.month < now.month:
                    start_dt = start_dt + pd.DateOffset(years=1)
                    end_dt = end_dt + pd.DateOffset(years=1)

                if end_dt >= start_dt:
                    diff_days = (end_dt.date() - start_dt.date()).days
                    if diff_days <= 60:
                        date_range = pd.date_range(start=start_dt.date(), end=end_dt.date(), freq="D")
                        return [d.strftime("%Y-%m-%d") for d in date_range]
                return [start_dt.strftime("%Y-%m-%d")]
    except Exception as e:
        logger.warning(f"Failed to parse date range text '{date_text}': {e}")

    return None


def scraper() -> pd.DataFrame:
    """Scrapes Tacoma Art Museum events using a single headless Chrome instance and returns raw DataFrame."""
    logger.info("==================================================================")
    logger.info("[START] Initializing PyVirtualDisplay server for Tacoma Art Museum scrape...")
    logger.info("==================================================================")

    display = Display(visible=0, size=(1920, 1080))
    display.start()

    events_data = []
    driver = None

    try:
        chrome_opts = get_headless_chrome_options()
        driver = webdriver.Chrome(options=chrome_opts)

        logger.info(f"[SELENIUM NAV] Fetching main calendar page: {TARGET_URL}")
        soup = fetch_rendered_page(driver, TARGET_URL, wait_selector="div.main_column, ul.events-list, li, .tribe-events-list")
        
        if not soup:
            logger.error("Could not load main TAM events page.")
            return pd.DataFrame()

        target_section = soup.find("div", class_="main_column") or soup.find("main") or soup
        
        # Strip script and style tags
        for element in target_section(["script", "style"]):
            element.decompose()

        event_items = target_section.find_all(["li", "article", "div"], class_=re.compile(r"event|tribe-events-item|type-tribe_events", re.I))
        
        # Fallback to general list items if no specific event containers found
        if not event_items:
            event_items = target_section.find_all("li")

        logger.info(f"[PARSER SUCCESS] Found {len(event_items)} TAM candidate event list items.")

        now = pd.Timestamp.now()

        for idx, event in enumerate(event_items, start=1):
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

            # 1. Title & External URL Extraction
            anchor = event.find("a", href=True)
            if anchor:
                href = anchor["href"].strip()
                item_data["external_url"] = href if href.startswith("http") else f"{BASE_URL}{href if href.startswith('/') else '/' + href}"
                
                deets_div = anchor.find("div", class_=re.compile(r"deets|title|heading", re.I)) or event.find(["h3", "h2", "h4"])
                if deets_div:
                    item_data["title"] = re.sub(r"\s+", " ", deets_div.get_text(strip=True))
                else:
                    item_data["title"] = re.sub(r"\s+", " ", anchor.get_text(strip=True))

            if not item_data["title"]:
                continue

            # 2. Date Parsing
            date_container = event.find("div", class_=re.compile(r"num|date|time|tribe-event-schedule", re.I)) or event.find("span", class_=re.compile(r"date", re.I))
            if date_container:
                raw_date_str = date_container.get_text(strip=True)
                item_data["start_time"] = parse_event_dates(raw_date_str, now)

            # 3. Thumbnail Image from Listing Card
            img_tag = event.find("img", src=True)
            if img_tag and img_tag["src"]:
                src = img_tag["src"].strip()
                item_data["image_url"] = src if src.startswith("http") else f"{BASE_URL}{src if src.startswith('/') else '/' + src}"

            # 4. Detail Page Fetch for Full Description & Metadata Fallback
            if item_data["external_url"]:
                logger.info(f"--- [EVENT {idx}] Detail Fetch: {item_data['external_url']} ---")
                det_soup = fetch_rendered_page(
                    driver, 
                    item_data["external_url"], 
                    wait_selector="div.rtecontent, div.tribe-events-content, main, article"
                )
                
                if det_soup:
                    container = (
                        det_soup.find("div", class_=re.compile(r"rtecontent|tribe-events-content|entry-content")) or 
                        det_soup.find("main") or 
                        det_soup
                    )
                    
                    # Date fallback from detail page if listing card lacked explicit dates
                    if not item_data["start_time"]:
                        det_date = det_soup.find("div", class_=re.compile(r"date|schedule|time", re.I)) or det_soup.find("span", class_=re.compile(r"date", re.I))
                        if det_date:
                            item_data["start_time"] = parse_event_dates(det_date.get_text(strip=True), now)

                    # Rich text extraction for full description
                    rich_text = fetch_event_text(container)
                    if rich_text:
                        item_data["big_desc"] = re.sub(r"\s+", " ", rich_text)
                    else:
                        item_data["big_desc"] = item_data["title"]

                    # Short description summary
                    h3_tag = container.find("h3")
                    if h3_tag:
                        item_data["description"] = h3_tag.get_text(separator=" ", strip=True)
                    else:
                        p_tag = container.find("p")
                        if p_tag:
                            item_data["description"] = p_tag.get_text(separator=" ", strip=True)
                        else:
                            item_data["description"] = item_data["big_desc"][:250] if item_data["big_desc"] else item_data["title"]

                    # High-res Detail Image Fallback
                    if not item_data["image_url"]:
                        det_img = container.find("img", src=True) or det_soup.find("meta", property="og:image")
                        if det_img:
                            src = det_img.get("src") or det_img.get("content")
                            if src:
                                src = src.strip()
                                item_data["image_url"] = src if src.startswith("http") else f"{BASE_URL}{src if src.startswith('/') else '/' + src}"

                    # Ticket / Registration Link
                    ticket_anchor = det_soup.find("a", href=re.compile(r"ticket|register|rsvp|buy", re.I))
                    if ticket_anchor and ticket_anchor.get("href"):
                        item_data["ticket_link"] = ticket_anchor["href"].strip()
            else:
                item_data["big_desc"] = item_data["title"]

            events_data.append(item_data)
            logger.info(f"  Title     : {item_data['title']}")
            logger.info(f"  Start Date: {item_data['start_time']}")

    except Exception as e:
        logger.error(f"[FATAL ERROR] Error during browser execution loop: {e}", exc_info=True)
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
    logger.info("Executing Tacoma Art Museum scraper task...")
    raw_df = scraper()

    if not raw_df.empty:
        logger.info("==================================================================")
        logger.info(f"[PIPELINE HANDOFF] Passing {len(raw_df)} event records to VividaIngestionPipeline...")
        logger.info("==================================================================")
        
        pipeline = VividaIngestionPipeline(table_name="events")
        success = pipeline.run_standalone_pipeline(raw_df)
        
        if success:
            logger.info("[PIPELINE SUCCESS] TAM scraper execution completed successfully.")
        else:
            logger.error("[PIPELINE ERROR] TAM scraper pipeline execution reported errors.")
    else:
        logger.warning("[EXIT] No events scraped. Pipeline handoff skipped.")