"""
tacomaPierceCountyEvents.py
Autonomous Scraper for Visit Tacoma / Pierce County Events Calendar (Simpleview Engine).
Fetches initial event queue via Simpleview REST API and parses detail pages via single-instance Chrome.
"""

import logging
import re
import sys
from typing import Optional

from bs4 import BeautifulSoup
import pandas as pd
import requests
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
logger = logging.getLogger("scrape_tacoma_pierce_county")

BASE_URL = "https://www.visitpiercecounty.com"
API_KEY = "45d9681f15067d6e73734e4e1f75d32d"
DEFAULT_VENUE = "Tacoma / Pierce County"
DEFAULT_ADDRESS = "Tacoma, WA"


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


def scraper(days_ahead: int = 30) -> pd.DataFrame:
    """Scrapes Visit Tacoma / Pierce County events using REST API queue and single headless Chrome instance."""
    logger.info("Starting Visit Tacoma / Pierce County events scrape...")

    now = pd.Timestamp.now()
    start_date = now.strftime("%Y-%m-%d")
    end_date = (now + pd.Timedelta(days=days_ahead)).strftime("%Y-%m-%d")
    limit = 500

    api_url = (
        f"{BASE_URL}/includes/rest_v2/plugins_events_events_by_date/find/"
        f"?json=%7B%22filter%22%3A%7B%22active%22%3Atrue%2C%22%24and%22%3A%5B%7B%22categories.catId%22%3A%7B%22%24in%22%3A%5B%228%22%2C%2210%22%2C%2214%22%2C%229%22%2C%2220%22%2C%2213%22%2C%2212%22%2C%2211%22%5D%7D%7D%5D%2C%22date_range%22%3A%7B%22start%22%3A%7B%22%24date%22%3A%22{start_date}T08%3A00%3A00.000Z%22%7D%2C%22end%22%3A%7B%22%24date%22%3A%22{end_date}T08%3A00%3A00.000Z%22%7D%7D%7D%2C%22options%22%3A%7B%22limit%22%3A{limit}%2C%22skip%22%3A0%2C%22count%22%3Atrue%2C%22castDocs%22%3Afalse%2C%22fields%22%3A%7B%22_id%22%3A1%2C%22location%22%3A1%2C%22date%22%3A1%2C%22startDate%22%3A1%2C%22endDate%22%3A1%2C%22recurrence%22%3A1%2C%22recurType%22%3A1%2C%22latitude%22%3A1%2C%22longitude%22%3A1%2C%22media_raw%22%3A1%2C%22recid%22%3A1%2C%22title%22%3A1%2C%22url%22%3A1%2C%22categories%22%3A1%2C%22accountId%22%3A1%2C%22city%22%3A1%2C%22region%22%3A1%2C%22listing.primary_category%22%3A1%2C%22listing.title%22%3A1%2C%22listing.url%22%3A1%2C%22listing.recid%22%3A1%2C%22listing.acctid%22%3A1%2C%22listing.region%22%3A1%2C%22listing.city%22%3A1%2C%22listing.rankname%22%3A1%7D%2C%22hooks%22%3A%5B%5D%2C%22sort%22%3A%7B%22date%22%3A1%2C%22rank%22%3A1%2C%22title_sort%22%3A1%7D%7D%7D&token={API_KEY}"
    )

    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    try:
        response = requests.get(api_url, headers=headers, timeout=15)
        response.raise_for_status()
        data = response.json()
    except Exception as e:
        logger.error(f"Failed to fetch Simpleview REST API event index: {e}")
        return pd.DataFrame()

    docs = data.get("docs", [])
    logger.info(f"Retrieved {len(docs)} event records from REST API index.")

    if not docs:
        return pd.DataFrame()

    detail_urls = []
    for doc in docs:
        rel_url = doc.get("url")
        if rel_url:
            full_url = rel_url if rel_url.startswith("http") else f"{BASE_URL}{rel_url if rel_url.startswith('/') else '/' + rel_url}"
            detail_urls.append(full_url)

    detail_urls = sorted(set(detail_urls))
    events_data = []

    driver = None
    try:
        chrome_opts = get_headless_chrome_options()
        driver = webdriver.Chrome(options=chrome_opts)

        for url in detail_urls:
            det_soup = fetch_rendered_page(driver, url, wait_selector="#main-content, div.info-section")
            if not det_soup:
                continue

            main_content = det_soup.find("div", id="main-content") or det_soup

            item_data = {
                "venue_name": DEFAULT_VENUE,
                "address": DEFAULT_ADDRESS,
                "title": None,
                "start_time": None,
                "description": None,
                "big_desc": None,
                "external_url": url,
                "image_url": None,
                "ticket_link": None
            }

            info_sec = main_content.find("div", class_=re.compile(r"info-section"))

            # Title
            if info_sec:
                h1_tag = info_sec.find("h1")
                if h1_tag:
                    item_data["title"] = h1_tag.get_text(strip=True)

            # Date Range parsing
            if info_sec:
                dd_list = info_sec.find_all("dd")
                if dd_list:
                    raw_dates_str = dd_list[1].get_text(strip=True) if len(dd_list) > 1 else dd_list[0].get_text(strip=True)
                    try:
                        if "-" in raw_dates_str:
                            parts = raw_dates_str.split("-")
                            parsed_dates = [pd.to_datetime(p.strip(), errors="coerce", format="mixed") for p in parts]
                            parsed_dates = [d.strftime("%Y-%m-%d") for d in parsed_dates if pd.notnull(d)]
                            if parsed_dates:
                                item_data["start_time"] = parsed_dates
                        elif "/" in raw_dates_str:
                            parts = raw_dates_str.split(",")
                            parsed_dates = [pd.to_datetime(p.strip(), errors="coerce", format="mixed") for p in parts]
                            parsed_dates = [d.strftime("%Y-%m-%d") for d in parsed_dates if pd.notnull(d)]
                            if parsed_dates:
                                item_data["start_time"] = parsed_dates
                        else:
                            dt = pd.to_datetime(raw_dates_str.strip(), errors="coerce", format="mixed")
                            if pd.notnull(dt):
                                item_data["start_time"] = [dt.strftime("%Y-%m-%d")]
                    except Exception as e:
                        logger.warning(f"Failed to parse dates '{raw_dates_str}': {e}")

            # Venue Name parsing
            two_line = main_content.find("div", class_=re.compile(r"two-line-wrap"))
            if two_line and two_line.find("a"):
                item_data["venue_name"] = two_line.find("a").get_text(strip=True)
            elif item_data["title"] and any(kw in item_data["title"] for kw in [" at ", " At ", " AT ", " @ "]):
                split_parts = re.split(r" at | At | AT | @ ", item_data["title"])
                if len(split_parts) > 1:
                    item_data["venue_name"] = split_parts[1].strip()

            # Address parsing
            if two_line:
                spans = two_line.find_all("span")
                if len(spans) >= 2:
                    st = spans[0].get_text(strip=True)
                    ct = spans[1].get_text(strip=True)
                    item_data["address"] = f"{st}, {ct}"

            # Short Description
            desc_tab = main_content.find("div", id="descriptionTab") or main_content.find("div", class_=re.compile(r"description"))
            if desc_tab:
                item_data["description"] = re.sub(r"\s+", " ", desc_tab.get_text(strip=True, separator=" "))

            # Rich Expanded Description
            rich_text = fetch_event_text(main_content)
            if rich_text:
                item_data["big_desc"] = re.sub(r"\s+", " ", rich_text)
            else:
                item_data["big_desc"] = item_data["description"]

            # Image URL
            img_tag = main_content.find("img", class_=re.compile(r"slide-img|loaded")) or main_content.find("img", src=True)
            if img_tag and img_tag.get("src"):
                src = img_tag["src"].strip()
                item_data["image_url"] = src if src.startswith("http") else f"{BASE_URL}{src if src.startswith('/') else '/' + src}"

            # Ticket Link
            actions_div = main_content.find("div", class_=re.compile(r"bottom-actions"))
            if actions_div and actions_div.find("a", href=True):
                item_data["ticket_link"] = actions_div.find("a")["href"].strip()

            if item_data["start_time"]:
                events_data.append(item_data)

    except Exception as e:
        logger.error(f"Error during headless browser detail navigation loop: {e}")
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
    logger.info("Executing Visit Tacoma / Pierce County scraper task...")
    raw_df = scraper(days_ahead=30)

    if not raw_df.empty:
        # Run autonomous pipeline transformation & Supabase upsert
        pipeline = VividaIngestionPipeline(table_name="events")
        success = pipeline.run_standalone_pipeline(raw_df)
        if success:
            logger.info("Visit Tacoma / Pierce County scraper execution completed successfully.")
        else:
            logger.error("Visit Tacoma / Pierce County scraper pipeline execution reported errors.")
    else:
        logger.warning("No events scraped. Exiting task.")