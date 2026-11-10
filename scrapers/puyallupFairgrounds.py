"""
puyallupFairgrounds.py
Autonomous Scraper for Puyallup Fairgrounds / Washington State Fair (Puyallup, WA).
Refactored for Xvfb headless execution, SVG-targeted metadata extraction, and isolated Supabase ingestion.
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
logger = logging.getLogger("scrape_puyallup_fair")

BASE_URL = "https://www.thefair.com"
TARGET_URL = "https://www.thefair.com/events-calendar/"
DEFAULT_VENUE = "Puyallup Fairgrounds"
DEFAULT_ADDRESS = "110 9th Ave SW, Puyallup, WA 98371"

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
    """Multi-pattern date parser with explicit regex extraction."""
    if not date_text or pd.isna(date_text):
        return None

    # Standardize string whitespace
    clean_str = re.sub(r"\s+", " ", str(date_text).strip())

    # Direct match for full dates like "Thursday, November 12, 2026" or "November 12, 2026"
    full_date_match = re.search(rf"\b{MONTH_NAMES}\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s+\d{{4}}\b", clean_str, re.I)
    if full_date_match:
        dt = pd.to_datetime(full_date_match.group(0), errors="coerce")
        if pd.notnull(dt):
            return [dt.strftime("%Y-%m-%d")]

    # Match month and day without year (e.g., "November 12" or "Nov 12")
    partial_date_match = re.search(rf"\b{MONTH_NAMES}\s+\d{{1,2}}(?:st|nd|rd|th)?\b", clean_str, re.I)
    if partial_date_match:
        full_str = f"{partial_date_match.group(0)}, {now.year}"
        dt = pd.to_datetime(full_str, errors="coerce")
        if pd.notnull(dt):
            if dt.month < now.month:
                dt = dt + pd.DateOffset(years=1)
            return [dt.strftime("%Y-%m-%d")]

    # Date ranges (e.g., "Nov 11 - Dec 4" or "Nov 20-21")
    range_parts = re.split(r"\s*[-–—\bto\b]\s*", clean_str, flags=re.IGNORECASE)
    try:
        if len(range_parts) >= 2:
            start_raw, end_raw = range_parts[0].strip(), range_parts[1].strip()
            
            # Carry month forward if end_raw is just a day number (e.g. "Nov 20-21")
            if re.match(r"^\d{1,2}$", end_raw):
                month_match = re.search(rf"\b{MONTH_NAMES}\b", start_raw, re.I)
                if month_match:
                    end_raw = f"{month_match.group(1)} {end_raw}"

            start_dt = pd.to_datetime(start_raw, errors="coerce", format="mixed")
            if pd.isnull(start_dt):
                start_dt = pd.to_datetime(f"{start_raw}, {now.year}", errors="coerce", format="mixed")

            end_dt = pd.to_datetime(end_raw, errors="coerce", format="mixed")
            if pd.isnull(end_dt):
                end_dt = pd.to_datetime(f"{end_raw}, {now.year}", errors="coerce", format="mixed")

            if pd.notnull(start_dt) and pd.notnull(end_dt):
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
    """Scrapes Puyallup Fairgrounds events across dynamic pagination using a single headless Chrome instance."""
    logger.info("==================================================================")
    logger.info("[START] Initializing PyVirtualDisplay server for Puyallup Fairgrounds scrape...")
    logger.info("==================================================================")

    display = Display(visible=0, size=(1920, 1080))
    display.start()

    all_events = []
    driver = None

    try:
        chrome_opts = get_headless_chrome_options()
        driver = webdriver.Chrome(options=chrome_opts)

        page = 1
        now = pd.Timestamp.now()

        while True:
            page_url = f"{TARGET_URL}?page={page}"
            logger.info(f"[SELENIUM NAV] Scanning calendar page {page}: {page_url}")

            soup = fetch_rendered_page(
                driver, 
                page_url, 
                wait_selector="div.container--md2, div.event-card-large"
            )

            if not soup:
                logger.warning(f"Failed to fetch page {page}. Terminating pagination scan.")
                break

            target_container = soup.find("div", class_=re.compile(r"container--md2")) or soup
            event_items = target_container.find_all("div", class_="event-card-large")

            if not event_items:
                logger.info(f"[PARSER END] No further event cards found on page {page}. Ending pagination.")
                break

            # Filter out top-level wrapper divs by requiring a direct .event-card-large__info child
            valid_cards = [card for card in event_items if card.find("div", class_="event-card-large__info")]
            logger.info(f"[PARSER SUCCESS] Found {len(valid_cards)} valid event cards on page {page}.")

            for idx, event in enumerate(valid_cards, start=1):
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

                info_container = event.find("div", class_="event-card-large__info")

                # 1. Event Title & External Link
                if info_container:
                    h3_tag = info_container.find("h3", class_="title") or info_container.find(["h3", "h2", "h4"])
                    if h3_tag:
                        item_data["title"] = re.sub(r"\s+", " ", h3_tag.get_text(strip=True))
                        anchor = h3_tag.find("a", href=True) or info_container.find("a", href=True)
                        if anchor:
                            href = anchor["href"].strip()
                            item_data["external_url"] = href if href.startswith("http") else f"{BASE_URL}{href}"

                if not item_data["title"]:
                    continue

                # 2. Date & Description Extraction
                if info_container:
                    icon_texts = info_container.find_all("div", class_="icon-text")
                    for div in icon_texts:
                        # Strip SVG element out to isolate clean text node
                        div_copy = BeautifulSoup(str(div), "html.parser")
                        for svg in div_copy.find_all("svg"):
                            svg.decompose()
                        
                        raw_text = div_copy.get_text(strip=True)

                        # Check for description span
                        span_tag = div.find("span")
                        if span_tag:
                            item_data["description"] = re.sub(r"\s+", " ", span_tag.get_text(strip=True))
                        # Check for month pattern in text node for start_time
                        elif re.search(rf"\b{MONTH_NAMES}\b", raw_text, re.I) and not item_data["start_time"]:
                            item_data["start_time"] = parse_event_dates(raw_text, now)

                # 3. Image URL
                img_tag = event.find("img", src=True)
                if img_tag and img_tag["src"]:
                    src = img_tag["src"].strip()
                    item_data["image_url"] = src if src.startswith("http") else f"{BASE_URL}{src}"

                # 4. Ticket Link from Buttons
                buttons_div = event.find("div", class_=re.compile(r"event-card-large__buttons|buttons"))
                if buttons_div:
                    t_anchor = buttons_div.find("a", href=re.compile(r"ticket", re.IGNORECASE))
                    if t_anchor and t_anchor.get("href"):
                        item_data["ticket_link"] = t_anchor["href"].strip()

                # 5. Deep Detail Render & Date Fallback
                if item_data["external_url"]:
                    logger.info(f"--- [P{page} EVENT {idx}] Detail Fetch: {item_data['external_url']} ---")
                    det_soup = fetch_rendered_page(
                        driver, 
                        item_data["external_url"], 
                        wait_selector="main, div.page-builder, section.content, .hero"
                    )
                    
                    if det_soup:
                        if not item_data["start_time"]:
                            det_date = det_soup.find("div", class_=re.compile(r"date|event-date|schedule", re.I)) or det_soup.find("span", class_=re.compile(r"date", re.I))
                            if det_date:
                                item_data["start_time"] = parse_event_dates(det_date.get_text(strip=True), now)

                        rich_text = fetch_event_text(det_soup)
                        if rich_text:
                            item_data["big_desc"] = re.sub(r"\s+", " ", rich_text)
                        else:
                            item_data["big_desc"] = item_data["description"] or item_data["title"]

                        if not item_data["ticket_link"]:
                            t_btn = det_soup.find("a", href=re.compile(r"ticket|buy|admission", re.IGNORECASE))
                            if t_btn and t_btn.get("href"):
                                item_data["ticket_link"] = t_btn["href"].strip()
                else:
                    item_data["big_desc"] = item_data["description"] or item_data["title"]

                all_events.append(item_data)
                logger.info(f"  Title     : {item_data['title']}")
                logger.info(f"  Start Date: {item_data['start_time']}")

            page += 1

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

    if not all_events:
        return pd.DataFrame()

    return pd.DataFrame(all_events)


if __name__ == "__main__":
    logger.info("Executing Puyallup Fairgrounds scraper task...")
    raw_df = scraper()

    if not raw_df.empty:
        logger.info("==================================================================")
        logger.info(f"[PIPELINE HANDOFF] Passing {len(raw_df)} event records to VividaIngestionPipeline...")
        logger.info("==================================================================")
        
        pipeline = VividaIngestionPipeline(table_name="events")
        success = pipeline.run_standalone_pipeline(raw_df)
        
        if success:
            logger.info("[PIPELINE SUCCESS] Puyallup Fairgrounds scraper pipeline execution completed successfully.")
        else:
            logger.error("[PIPELINE ERROR] Puyallup Fairgrounds scraper pipeline execution reported errors.")
    else:
        logger.warning("[EXIT] No events scraped. Pipeline handoff skipped.")