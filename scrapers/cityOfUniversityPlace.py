"""
cityOfUniversityPlace.py
Autonomous Scraper for City of University Place (WA) Events.
Refactored for Xvfb headless execution and isolated Supabase ingestion.
"""
import logging
import re
import sys
from typing import Optional
from urllib.parse import urljoin

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
logger = logging.getLogger("scrape_city_of_up")

BASE_URL = "https://www.cityofup.com"
TARGET_URL = "https://www.cityofup.com/132/Events"
DEFAULT_VENUE = "University Place, WA"

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
    """Scrapes City of UP events using single headless Chrome instance and returns raw DataFrame."""
    logger.info(f"Starting City of University Place scrape from {TARGET_URL}...")
    
    # Outer scope initialization to prevent UnboundLocalError on driver failure
    events_data = []
    driver = None

    try:
        chrome_opts = get_headless_chrome_options()
        driver = webdriver.Chrome(options=chrome_opts)

        # 1. Fetch main calendar page
        soup = fetch_rendered_page(driver, TARGET_URL, wait_selector="div.calendarInnerWrapper, .cpGrid, table.calendarTable")
        if not soup:
            logger.error("Could not load main events page.")
            return pd.DataFrame()

        target_section = soup.find("div", class_="calendarInnerWrapper") or soup.find("div", class_=re.compile(r"cpGrid")) or soup
        if not target_section:
            logger.warning("Could not locate calendar container on page.")
            return pd.DataFrame()

        # Clean script/style nodes
        for element in target_section(["script", "style"]):
            element.decompose()

        event_items = target_section.find_all(["li", "tr", "div"], class_=re.compile(r"calendarItem|cpGridRow|eventItem"))
        if not event_items:
            # Fallback to generic li search if specific CivicPlus grid classes aren't matched
            event_items = target_section.find_all("li")

        logger.info(f"Found {len(event_items)} candidate event items in calendar view.")

        now = pd.Timestamp.now()

        # 2. Iterate event list and step into detail pages via same driver
        for event in event_items:
            item_data = {
                "venue_name": DEFAULT_VENUE,
                "address": None,
                "title": None,
                "start_time": None,
                "description": None,
                "big_desc": None,
                "external_url": None,
                "image_url": None,
                "ticket_link": None
            }

            # Extract Title & Relative Link
            anchor = event.find("a", href=True)
            if anchor:
                title_text = anchor.get_text(strip=True)
                if not title_text or len(title_text) < 3:
                    continue
                item_data["title"] = title_text
                href = anchor["href"].strip()
                item_data["external_url"] = urljoin(BASE_URL, href)
            else:
                continue

            # Extract Raw Date Header
            date_tag = event.find(["h4", "h3", "span", "td"], class_=re.compile(r"date|calendarDate|dateHeader", re.I)) or event.find("h4")
            if date_tag:
                raw_date_str = date_tag.get_text(strip=True)
                try:
                    # Strip day names (e.g., "Tuesday, ")
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

            # 3. Step into Detail Page if URL exists
            if item_data["external_url"]:
                det_soup = fetch_rendered_page(
                    driver, 
                    item_data["external_url"], 
                    wait_selector="div.eventDetails, div[itemprop='description'], .cpContent"
                )
                
                if det_soup:
                    # Extract Venue & Address (itemprop="location")
                    addr_soup = det_soup.find("div", itemprop="location") or det_soup.find("div", class_=re.compile(r"location|address", re.I))
                    if addr_soup:
                        venue_name_tag = addr_soup.find("div", itemprop="name") or addr_soup.find("span", itemprop="name")
                        if venue_name_tag:
                            item_data["venue_name"] = venue_name_tag.get_text(strip=True)

                        try:
                            street = addr_soup.find("span", itemprop="streetAddress").get_text(strip=True)
                            city = addr_soup.find("span", itemprop="addressLocality").get_text(strip=True)
                            zip_code = addr_soup.find("span", itemprop="postalCode").get_text(strip=True)
                            item_data["address"] = f"{street}, {city}, WA {zip_code}"
                        except Exception:
                            item_data["address"] = addr_soup.get_text(separator=", ", strip=True)

                    # Extract Description
                    desc_container = det_soup.find("div", itemprop="description") or det_soup.find("div", class_=re.compile(r"eventDescription|cpContent"))
                    if desc_container:
                        raw_big_desc = desc_container.get_text(separator=" ", strip=True)
                        item_data["big_desc"] = re.sub(r"\s+", " ", raw_big_desc)

                        p_tags = desc_container.find_all("p")
                        if p_tags:
                            item_data["description"] = p_tags[0].get_text(separator=" ", strip=True)
                        else:
                            item_data["description"] = item_data["big_desc"][:250] if item_data["big_desc"] else None

                    # Extract Detail Image
                    img_tag = det_soup.find("img", src=True)
                    if img_tag and img_tag["src"]:
                        src = img_tag["src"].strip()
                        item_data["image_url"] = urljoin(BASE_URL, src)

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
    logger.info("Executing City of University Place scraper task...")
    raw_df = scraper()

    if not raw_df.empty:
        pipeline = VividaIngestionPipeline(table_name="events")
        success = pipeline.run_standalone_pipeline(raw_df)
        if success:
            logger.info("City of UP scraper execution completed successfully.")
        else:
            logger.error("City of UP scraper pipeline execution reported errors.")
    else:
        logger.warning("No events scraped. Exiting task.")