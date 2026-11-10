"""
pointDefianceZoo.py
Autonomous Scraper for Point Defiance Zoo & Aquarium (Tacoma, WA).
Refactored for Xvfb headless execution, hero container image extraction, and isolated Supabase ingestion.
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
logger = logging.getLogger("scrape_pdza")

BASE_URL = "https://www.pdza.org"
TARGET_URL = "https://www.pdza.org/events/"
DEFAULT_VENUE = "Point Defiance Zoo & Aquarium"
DEFAULT_ADDRESS = "5400 N Pearl St, Tacoma, WA 98407"


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


def parse_pdza_date(date_text: str, now: pd.Timestamp) -> Optional[list]:
    """Robust parser for PDZA date strings including ranges and single dates."""
    if not date_text:
        return None
        
    # Clean day names (e.g. "Wed, Nov 11" -> "Nov 11")
    clean_str = re.sub(
        r"^(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday|Mon|Tue|Wed|Thu|Fri|Sat|Sun)[s\,]?\s*",
        "",
        date_text.strip(),
        flags=re.IGNORECASE
    )
    
    # Split date ranges (e.g., "Nov 10 - Nov 12" or "Nov 10 – 12")
    range_parts = re.split(r"\s*[-–—]\s*", clean_str)
    
    try:
        first_date_raw = range_parts[0].strip()
        full_date_str = f"{first_date_raw}, {now.year}" if str(now.year) not in first_date_raw else first_date_raw
        start_dt = pd.to_datetime(full_date_str, errors="coerce", format="mixed")
        
        if pd.notnull(start_dt):
            if start_dt.month < now.month and str(now.year) not in first_date_raw:
                start_dt = start_dt + pd.DateOffset(years=1)
            return [start_dt.strftime("%Y-%m-%d")]
    except Exception:
        pass
        
    return None


def extract_pdza_image(soup: BeautifulSoup) -> Optional[str]:
    """Multi-tiered image extractor targeting hero divs, OpenGraph tags, and WP uploads."""
    if not soup:
        return None

    # Tier 0: Direct PDZA Hero Container Inspection (<div class="hero" style="background-image: ...">)
    hero_div = soup.find("div", class_=re.compile(r"hero", re.I)) or soup.find(lambda tag: tag.has_attr("style") and "background-image" in tag["style"])
    if hero_div and hero_div.get("style"):
        match = re.search(r'url\((?:["\']?)(.*?)(?:["\']?)\)', hero_div["style"], re.IGNORECASE)
        if match:
            url_str = match.group(1).strip()
            return url_str if url_str.startswith("http") else f"{BASE_URL}{url_str if url_str.startswith('/') else '/' + url_str}"

    # Tier 1: OpenGraph Meta Tag (Fallback)
    og_img = soup.find("meta", property=re.compile(r"og:image", re.I))
    if og_img and og_img.get("content"):
        url = og_img["content"].strip()
        if url.startswith("http"):
            return url

    # Tier 2: Dedicated Event Banner / Featured Image Container
    featured_container = (
        soup.find("div", class_=re.compile(r"single-event-image|tribe-events-event-image|featured-image", re.I)) or
        soup.find("section", class_=re.compile(r"single-event", re.I))
    )
    search_target = featured_container if featured_container else soup

    # Tier 3: Lazy-loaded or standard img tags inside the main container
    img_tag = search_target.find("img", attrs={"data-src": True}) or \
              search_target.find("img", attrs={"data-lazy-src": True}) or \
              search_target.find("img", src=re.compile(r"wp-content/uploads", re.I))

    if img_tag:
        src = img_tag.get("data-src") or img_tag.get("data-lazy-src") or img_tag.get("src")
        if src:
            src_str = src.strip()
            return src_str if src_str.startswith("http") else f"{BASE_URL}{src_str if src_str.startswith('/') else '/' + src_str}"

    return None


def scraper() -> pd.DataFrame:
    """Scrapes PDZA events using a single headless Chrome instance and returns raw DataFrame."""
    logger.info("==================================================================")
    logger.info("[START] Initializing PyVirtualDisplay server for Point Defiance Zoo scrape...")
    logger.info("==================================================================")
    
    display = Display(visible=0, size=(1920, 1080))
    display.start()

    events_data = []
    driver = None

    try:
        chrome_opts = get_headless_chrome_options()
        driver = webdriver.Chrome(options=chrome_opts)

        # 1. Fetch main event grid page
        logger.info(f"[SELENIUM NAV] Fetching main calendar grid: {TARGET_URL}")
        soup = fetch_rendered_page(driver, TARGET_URL, wait_selector="article.event, div.event-card, .tribe-events-list")
        if not soup:
            logger.error("Could not load main events list page.")
            return pd.DataFrame()

        # Clean script and style tags from top-level soup
        for element in soup(["script", "style"]):
            element.decompose()

        # Find top-level event cards/articles
        event_items = soup.find_all(["article", "div"], class_=re.compile(r"event-card|tribe-events-single|type-tribe_events"))
        if not event_items:
            # Fallback to links pointing directly to detail pages
            event_items = soup.find_all("a", href=re.compile(r"/event/"))

        logger.info("[PARSER SUCCESS] Located candidate event elements. Deduplicating target URLs...")

        # Deduplicate detail URLs first
        unique_events = {}
        for item in event_items:
            link = item.get("href") if item.name == "a" else None
            if not link:
                a_tag = item.find("a", href=re.compile(r"/event/"))
                if a_tag:
                    link = a_tag.get("href")

            if link:
                full_url = link.strip() if link.startswith("http") else f"{BASE_URL}{link.strip() if link.startswith('/') else '/' + link.strip()}"
                if full_url not in unique_events:
                    unique_events[full_url] = item

        logger.info(f"[PARSER DEDUP] Found {len(unique_events)} unique event detail pages to scrape.")
        now = pd.Timestamp.now()

        # 2. Iterate through unique event pages
        for idx, (detail_url, card_elem) in enumerate(unique_events.items(), start=1):
            logger.info(f"--- [EVENT {idx}/{len(unique_events)}] Parsing detail page: {detail_url} ---")
            
            item_data = {
                "venue_name": DEFAULT_VENUE,
                "address": DEFAULT_ADDRESS,
                "title": None,
                "start_time": None,
                "description": None,
                "big_desc": None,
                "external_url": detail_url,
                "image_url": None,
                "ticket_link": detail_url
            }

            # Pre-extract card-level title and date if present
            if card_elem and card_elem.name != "a":
                title_tag = card_elem.find(["h2", "h3", "h4", "a"], class_=re.compile(r"title", re.I))
                if title_tag:
                    item_data["title"] = title_tag.get_text(strip=True)

                date_tag = card_elem.find(["div", "span", "p"], class_=re.compile(r"date|time", re.I))
                if date_tag:
                    item_data["start_time"] = parse_pdza_date(date_tag.get_text(strip=True), now)

            # 3. Step into Detail Page for full content & image fallback
            det_soup = fetch_rendered_page(
                driver, 
                detail_url, 
                wait_selector="h1, section.single-event, main, .hero"
            )
            
            if det_soup:
                # Extract Title from Detail Page
                h1_tag = det_soup.find("h1")
                if h1_tag and not item_data["title"]:
                    item_data["title"] = h1_tag.get_text(strip=True)

                # Extract Date from Detail Page if missing from card
                if not item_data["start_time"]:
                    detail_date_el = (
                        det_soup.find("span", class_=re.compile(r"tribe-event-date-start|date-start|event-date", re.I)) or
                        det_soup.find("div", class_=re.compile(r"date|schedule", re.I))
                    )
                    if detail_date_el:
                        item_data["start_time"] = parse_pdza_date(detail_date_el.get_text(strip=True), now)

                # Fallback: parse detail body for month/day patterns
                if not item_data["start_time"]:
                    body_text = det_soup.get_text()
                    match = re.search(r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2}(?:st|nd|rd|th)?\b", body_text, re.I)
                    if match:
                        item_data["start_time"] = parse_pdza_date(match.group(0), now)

                # Description
                section = (
                    det_soup.find("section", class_=re.compile(r"single-event|content", re.I)) or 
                    det_soup.find("main") or 
                    det_soup
                )

                # Ticket Link
                t_btn = section.find(["a", "button"], href=re.compile(r"ticket|buy|admission|register|etix", re.I))
                if t_btn and t_btn.get("href"):
                    item_data["ticket_link"] = t_btn.get("href").strip()

                # Rich Text / Big Description
                rich_text = fetch_event_text(section)
                if rich_text:
                    item_data["big_desc"] = re.sub(r"\s+", " ", rich_text)
                    item_data["description"] = item_data["big_desc"][:250]
                else:
                    item_data["big_desc"] = item_data["title"]
                    item_data["description"] = item_data["title"]

                # Extract Image URL via multi-tiered extractor (Hero check enabled)
                item_data["image_url"] = extract_pdza_image(det_soup)

            events_data.append(item_data)
            logger.info(f"  Title     : {item_data['title']}")
            logger.info(f"  Start Date: {item_data['start_time']}")
            logger.info(f"  Image URL : {item_data['image_url']}")

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
    logger.info("Executing Point Defiance Zoo & Aquarium scraper task...")
    raw_df = scraper()

    if not raw_df.empty:
        logger.info("==================================================================")
        logger.info(f"[PIPELINE HANDOFF] Passing {len(raw_df)} event records to VividaIngestionPipeline...")
        logger.info("==================================================================")
        
        pipeline = VividaIngestionPipeline(table_name="events")
        success = pipeline.run_standalone_pipeline(raw_df)
        
        if success:
            logger.info("[PIPELINE SUCCESS] PDZA scraper pipeline execution completed successfully.")
        else:
            logger.error("[PIPELINE ERROR] PDZA scraper pipeline execution reported errors.")
    else:
        logger.warning("[EXIT] No events scraped. Exiting task.")