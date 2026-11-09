"""
tacomaArtsLive.py
Autonomous Scraper for Tacoma Arts Live (Tacoma, WA).
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
logger = logging.getLogger("scrape_tacoma_arts_live")

BASE_URL = "https://www.tacomaartslive.org"
TARGET_URL = "https://www.tacomaartslive.org/upcoming-events/"
DEFAULT_VENUE = "Tacoma Arts Live"
DEFAULT_ADDRESS = "901 Broadway, Tacoma, WA 98402"


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
    """Scrapes Tacoma Arts Live events using a single headless Chrome instance and returns raw DataFrame."""
    logger.info(f"Starting Tacoma Arts Live scrape from {TARGET_URL}...")
    events_data = []

    driver = None
    try:
        chrome_opts = get_headless_chrome_options()
        driver = webdriver.Chrome(options=chrome_opts)

        # 1. Fetch main event grid page
        soup = fetch_rendered_page(
            driver, 
            TARGET_URL, 
            wait_selector="div.mec-skin-grid-events-container, div.mec-wrap"
        )
        if not soup:
            logger.error("Could not load main events page.")
            return pd.DataFrame()

        target_section = (
            soup.find("div", class_="mec-skin-grid-events-container") or 
            soup.find("div", class_=re.compile(r"mec-wrap")) or 
            soup
        )
        
        # Clean script/style tags
        for element in target_section(["script", "style"]):
            element.decompose()

        event_items = target_section.find_all("div", class_=re.compile(r"col-md-4|col-sm-4|mec-grid-item"))
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

            # Title & External URL
            h4_tag = event.find("h4")
            if h4_tag:
                item_data["title"] = h4_tag.get_text(strip=True)
                anchor = h4_tag.find("a", href=True)
                if anchor:
                    href = anchor["href"].strip()
                    item_data["external_url"] = href if href.startswith("http") else f"{BASE_URL}{href if href.startswith('/') else '/' + href}"

            # Venue Name & Address extraction from location paragraph
            loc_p = event.find("p", class_=re.compile(r"mec-grid-event-location"))
            if loc_p:
                loc_text = loc_p.get_text(strip=True)
                parts = loc_text.split(",")
                if len(parts) > 0 and parts[0].strip():
                    item_data["venue_name"] = parts[0].strip()
                if len(parts) > 1:
                    item_data["address"] = ", ".join(parts[1:]).strip()

            # Date parsing with dynamic year rollover check
            date_span = event.find("span", class_=re.compile(r"mec-start-date-label"))
            if date_span:
                raw_date_str = date_span.get_text(strip=True)
                try:
                    full_date_str = f"{raw_date_str} {now.year}"
                    start_dt = pd.to_datetime(full_date_str, errors="coerce", format="mixed")
                    
                    if pd.notnull(start_dt):
                        if start_dt.month < now.month:
                            start_dt = start_dt + pd.DateOffset(years=1)
                        item_data["start_time"] = [start_dt.strftime("%Y-%m-%d")]
                except Exception as e:
                    logger.warning(f"Failed to parse date string '{raw_date_str}': {e}")

            # 3. Step into Detail Page for rich descriptions and image/ticket links
            if item_data["external_url"]:
                det_soup = fetch_rendered_page(
                    driver, 
                    item_data["external_url"], 
                    wait_selector="div.elementor, div.entry-content, main"
                )
                
                if det_soup:
                    container = (
                        det_soup.find("div", class_=re.compile(r"elementor-widget-text-editor")) or 
                        det_soup.find("div", class_="elementor") or 
                        det_soup.find("div", class_="entry-content") or 
                        det_soup
                    )

                    # Extract rich big description
                    rich_text = fetch_event_text(container)
                    if rich_text:
                        item_data["big_desc"] = re.sub(r"\s+", " ", rich_text)

                    # Extract primary short description
                    p_tag = container.find("p")
                    if p_tag:
                        item_data["description"] = re.sub(r"\s+", " ", p_tag.get_text(strip=True, separator=" "))
                    else:
                        item_data["description"] = item_data["big_desc"][:250] if item_data["big_desc"] else None

                    # Extract image URL safely
                    img_tag = det_soup.find("img", src=True)
                    if img_tag and img_tag["src"]:
                        src = img_tag["src"].strip()
                        item_data["image_url"] = src if src.startswith("http") else f"{BASE_URL}{src if src.startswith('/') else '/' + src}"

                    # Extract ticket link (looking for buy/ticket links or noscript elements)
                    t_anchor = det_soup.find("a", href=re.compile(r"ticket|buy|tessitura", re.IGNORECASE))
                    if t_anchor and t_anchor.get("href"):
                        item_data["ticket_link"] = t_anchor["href"].strip()

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
    logger.info("Executing Tacoma Arts Live scraper task...")
    raw_df = scraper()

    if not raw_df.empty:
        # Run autonomous pipeline transformation & Supabase upsert
        pipeline = VividaIngestionPipeline(table_name="events")
        success = pipeline.run_standalone_pipeline(raw_df)
        if success:
            logger.info("Tacoma Arts Live scraper execution completed successfully.")
        else:
            logger.error("Tacoma Arts Live scraper pipeline execution reported errors.")
    else:
        logger.warning("No events scraped. Exiting task.")