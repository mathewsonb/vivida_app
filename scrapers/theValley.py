"""
theValley.py
Autonomous Scraper for The Valley (Tacoma, WA - Squarespace Engine).
Refactored for Xvfb headless execution, clean address decoding, and Supabase ingestion.
"""

import logging
import re
import sys
from typing import Optional
from urllib.parse import unquote

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
logger = logging.getLogger("scrape_the_valley")

BASE_URL = "https://www.thevalleytacoma.com"
TARGET_URL = "https://www.thevalleytacoma.com/new-events-1"
DEFAULT_VENUE = "The Valley"
DEFAULT_ADDRESS = "1206 Puyallup Ave, Tacoma, WA 98421"


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


def parse_address_url(addr_href: Optional[str]) -> str:
    """Decodes address string from Google Maps query parameter in Squarespace event metadata."""
    if not addr_href or "q=" not in addr_href:
        return DEFAULT_ADDRESS
    
    try:
        raw_q = addr_href.split("q=")[1].split("&")[0]
        decoded = unquote(raw_q).replace("+", " ").strip()
        return decoded if len(decoded) > 5 else DEFAULT_ADDRESS
    except Exception:
        return DEFAULT_ADDRESS


def scraper() -> pd.DataFrame:
    """Scrapes The Valley events using a single headless Chrome instance and returns raw DataFrame."""
    logger.info(f"Starting The Valley Tacoma scrape from {TARGET_URL}...")
    events_data = []

    driver = None
    try:
        chrome_opts = get_headless_chrome_options()
        driver = webdriver.Chrome(options=chrome_opts)

        # 1. Fetch main event list page
        soup = fetch_rendered_page(
            driver, 
            TARGET_URL, 
            wait_selector="div.eventlist, article.eventlist-event"
        )
        if not soup:
            logger.error("Could not load main events page.")
            return pd.DataFrame()

        target_section = (
            soup.find("div", class_=re.compile(r"eventlist--upcoming|eventlist")) or 
            soup
        )

        for element in target_section(["script", "style"]):
            element.decompose()

        event_items = target_section.find_all("article", class_=re.compile(r"eventlist-event"))
        logger.info(f"Found {len(event_items)} Squarespace event articles.")

        # 2. Iterate event articles and step into detail pages
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
            h1_tag = event.find(["h1", "h2"], class_=re.compile(r"eventlist-title|title"))
            if h1_tag:
                anchor = h1_tag.find("a", href=True)
                if anchor:
                    item_data["title"] = anchor.get_text(strip=True)
                    href = anchor["href"].strip()
                    item_data["external_url"] = href if href.startswith("http") else f"{BASE_URL}{href if href.startswith('/') else '/' + href}"
                else:
                    item_data["title"] = h1_tag.get_text(strip=True)

            # Date Parsing from <time> tag or datetime attribute
            time_tag = event.find("time", class_=re.compile(r"event-date|eventlist-meta-time"))
            if time_tag:
                raw_dt = time_tag.get("datetime") or time_tag.get_text(strip=True)
                if raw_dt:
                    dt = pd.to_datetime(raw_dt, errors="coerce")
                    if pd.notnull(dt):
                        item_data["start_time"] = [dt.strftime("%Y-%m-%d")]

            # Short Description
            desc_div = event.find("div", class_=re.compile(r"sqs-html-content|eventlist-excerpt"))
            if desc_div:
                item_data["description"] = re.sub(r"\s+", " ", desc_div.get_text(strip=True, separator=" "))

            # Image URL with Squarespace CDN normalization
            img_tag = event.find("img", src=True) or event.find("img", attrs={"data-src": True})
            if img_tag:
                src = img_tag.get("data-src") or img_tag.get("src")
                if src:
                    src = src.strip()
                    if src.startswith("//"):
                        src = f"https:{src}"
                    item_data["image_url"] = src if src.startswith("http") else f"{BASE_URL}{src if src.startswith('/') else '/' + src}"

            # Address parsing from maps link
            addr_anchor = event.find("li", class_=re.compile(r"eventlist-meta-address"))
            if addr_anchor and addr_anchor.find("a", href=True):
                item_data["address"] = parse_address_url(addr_anchor.find("a")["href"])

            # 3. Step into Detail Page for rich content using the same driver
            if item_data["external_url"]:
                det_soup = fetch_rendered_page(
                    driver, 
                    item_data["external_url"], 
                    wait_selector="article.eventitem, div.sqs-block-html, main"
                )
                
                if det_soup:
                    container = (
                        det_soup.find("article", class_=re.compile(r"eventitem")) or 
                        det_soup.find("main") or 
                        det_soup
                    )

                    rich_text = fetch_event_text(container)
                    if rich_text:
                        item_data["big_desc"] = re.sub(r"\s+", " ", rich_text)
                    else:
                        item_data["big_desc"] = item_data["description"]

                    # Check for external ticket button
                    t_anchor = det_soup.find("a", href=re.compile(r"ticket|eventbrite|dice|buy", re.IGNORECASE))
                    if t_anchor and t_anchor.get("href"):
                        item_data["ticket_link"] = t_anchor["href"].strip()
            else:
                item_data["big_desc"] = item_data["description"]

            events_data.append(item_data)

    except Exception as e:
        logger.error(f"Error during The Valley Tacoma scraping loop: {e}")
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
    logger.info("Executing The Valley Tacoma scraper task...")
    raw_df = scraper()

    if not raw_df.empty:
        # Run autonomous pipeline transformation & Supabase upsert
        pipeline = VividaIngestionPipeline(table_name="events")
        success = pipeline.run_standalone_pipeline(raw_df)
        if success:
            logger.info("The Valley Tacoma scraper execution completed successfully.")
        else:
            logger.error("The Valley Tacoma scraper pipeline execution reported errors.")
    else:
        logger.warning("No events scraped. Exiting task.")