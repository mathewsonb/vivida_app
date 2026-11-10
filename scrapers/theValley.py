"""
theValley.py
Autonomous Production Scraper for The Valley (Tacoma, WA - Squarespace Engine).
Fetches upcoming live events using undetected_chromedriver + Xvfb, extracts Squarespace event metadata,
steps into detail pages for rich descriptions, and routes records to VividaIngestionPipeline.
"""

import logging
import os
import re
import sys
import time
from typing import Dict, List, Optional, Set
from urllib.parse import unquote

from bs4 import BeautifulSoup
import pandas as pd
from pyvirtualdisplay import Display
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
import undetected_chromedriver as uc

from vivida_etl import (
    VividaIngestionPipeline,
    clean_html_source,
    fetch_event_text,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(filename)s:%(lineno)d] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("scrape_the_valley")

BASE_URL = "https://www.thevalleytacoma.com"
TARGET_URL = "https://www.thevalleytacoma.com/new-events-1"
DEFAULT_VENUE = "The Valley"
DEFAULT_ADDRESS = "1206 Puyallup Ave, Tacoma, WA 98421"


def create_stealth_driver() -> uc.Chrome:
    """Instantiates an undetected Chrome driver configured for Xvfb display rendering."""
    options = uc.ChromeOptions()
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-gpu")
    
    if os.path.exists("/usr/bin/google-chrome"):
        options.binary_location = "/usr/bin/google-chrome"
    elif os.path.exists("/usr/bin/chromium"):
        options.binary_location = "/usr/bin/chromium"

    return uc.Chrome(options=options, version_main=None)


def fetch_rendered_page(driver: uc.Chrome, url: str, wait_selector: Optional[str] = None, timeout: int = 10) -> Optional[BeautifulSoup]:
    """Navigates active driver to target URL and returns pre-treated BeautifulSoup DOM."""
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


def parse_squarespace_dates(event: BeautifulSoup) -> Optional[List[str]]:
    """Extracts date strings from Squarespace event markup and parses into ISO date sequences."""
    start_dt = None
    end_dt = None

    # Check structural data attributes first
    data_start = event.get("data-start-date") or event.get("data-start")
    data_end = event.get("data-end-date") or event.get("data-end")

    if data_start:
        start_dt = pd.to_datetime(data_start, errors="coerce")
    if data_end:
        end_dt = pd.to_datetime(data_end, errors="coerce")

    # Fallback to <time> tag parsing
    if pd.isnull(start_dt):
        time_tags = event.find_all("time", class_=re.compile(r"event-date|eventlist-meta-time|event-time", re.I))
        for tag in time_tags:
            raw_dt = tag.get("datetime") or tag.get_text(strip=True)
            if raw_dt:
                dt_parsed = pd.to_datetime(raw_dt, errors="coerce")
                if pd.notnull(dt_parsed):
                    if pd.isnull(start_dt):
                        start_dt = dt_parsed
                    else:
                        end_dt = dt_parsed

    if pd.notnull(start_dt):
        s_str = start_dt.strftime("%Y-%m-%d")
        if pd.notnull(end_dt) and end_dt.date() > start_dt.date():
            e_str = end_dt.strftime("%Y-%m-%d")
            return [s_str, e_str]
        return [s_str]

    return None


def scraper() -> pd.DataFrame:
    """Scrapes The Valley Tacoma events using undetected Chrome driver in Xvfb."""
    logger.info("==================================================================")
    logger.info(f"[START] Initializing PyVirtualDisplay server for The Valley: {TARGET_URL}")
    logger.info("==================================================================")

    display = Display(visible=0, size=(1920, 1080), backend="xvfb")
    display.start()

    driver = None
    raw_records: List[Dict[str, Optional[str]]] = []
    seen_urls: Set[str] = set()

    try:
        logger.info("[SELENIUM] Launching undetected Chrome driver session...")
        driver = create_stealth_driver()

        logger.info(f"[SELENIUM NAV] Fetching main events calendar: {TARGET_URL}")
        soup = fetch_rendered_page(
            driver, 
            TARGET_URL, 
            wait_selector="div.eventlist, article.eventlist-event"
        )
        if not soup:
            logger.error("[ERROR] Could not load main events page.")
            return pd.DataFrame()

        target_section = (
            soup.find("div", class_=re.compile(r"eventlist--upcoming|eventlist", re.I)) or 
            soup
        )

        for element in target_section(["script", "style"]):
            element.decompose()

        event_items = target_section.find_all("article", class_=re.compile(r"eventlist-event", re.I))
        logger.info(f"[PARSER SUCCESS] Found {len(event_items)} Squarespace event articles.")

        # Iterate event articles
        for idx, event in enumerate(event_items, start=1):
            try:
                # Title & External URL
                h1_tag = event.find(["h1", "h2", "h3"], class_=re.compile(r"eventlist-title|title", re.I)) or event.find(["h1", "h2", "h3"])
                card_title = None
                detail_url = None

                if h1_tag:
                    anchor = h1_tag.find("a", href=True)
                    if anchor:
                        card_title = anchor.get_text(strip=True)
                        href = anchor["href"].strip()
                        detail_url = href if href.startswith("http") else f"{BASE_URL}{href if href.startswith('/') else '/' + href}"
                    else:
                        card_title = h1_tag.get_text(strip=True)
                else:
                    anchor = event.find("a", href=True)
                    if anchor:
                        card_title = anchor.get_text(strip=True)
                        href = anchor["href"].strip()
                        detail_url = href if href.startswith("http") else f"{BASE_URL}{href if href.startswith('/') else '/' + href}"

                if not card_title:
                    continue

                dedup_key = detail_url or card_title
                if dedup_key in seen_urls:
                    continue
                seen_urls.add(dedup_key)

                logger.info(f"--- [EVENT {len(seen_urls)}/{len(event_items)}] Processing: {card_title} ---")

                # Date parsing
                start_time_seq = parse_squarespace_dates(event)

                # Short Description / Tagline
                desc_div = event.find("div", class_=re.compile(r"sqs-html-content|eventlist-excerpt|excerpt", re.I))
                description = re.sub(r"\s+", " ", desc_div.get_text(separator=" ", strip=True)) if desc_div else card_title

                # Image URL with CDN normalization
                img_tag = event.find("img", src=True) or event.find("img", attrs={"data-src": True})
                image_url = None
                if img_tag:
                    src = img_tag.get("data-src") or img_tag.get("src")
                    if src:
                        src = src.strip()
                        if src.startswith("//"):
                            src = f"https:{src}"
                        image_url = src if src.startswith("http") else f"{BASE_URL}{src if src.startswith('/') else '/' + src}"

                # Address decoding from maps link
                address = DEFAULT_ADDRESS
                addr_anchor = event.find("li", class_=re.compile(r"eventlist-meta-address", re.I))
                if addr_anchor and addr_anchor.find("a", href=True):
                    address = parse_address_url(addr_anchor.find("a")["href"])

                # Step into Detail Page for rich content
                big_desc = description
                ticket_link = None

                if detail_url:
                    logger.info(f"  [DETAIL FETCH] Navigating to detail page: {detail_url}")
                    det_soup = fetch_rendered_page(
                        driver, 
                        detail_url, 
                        wait_selector="article.eventitem, div.sqs-block-html, main"
                    )
                    
                    if det_soup:
                        if not start_time_seq:
                            start_time_seq = parse_squarespace_dates(det_soup)

                        container = (
                            det_soup.find("article", class_=re.compile(r"eventitem", re.I)) or 
                            det_soup.find("main") or 
                            det_soup
                        )

                        rich_text = fetch_event_text(container)
                        if rich_text:
                            big_desc = re.sub(r"\s+", " ", rich_text)

                        # External ticket button check
                        t_anchor = det_soup.find("a", href=re.compile(r"ticket|eventbrite|dice|buy", re.IGNORECASE))
                        if t_anchor and t_anchor.get("href"):
                            ticket_link = t_anchor["href"].strip()

                if not ticket_link:
                    ticket_link = detail_url

                item_data = {
                    "venue_name": DEFAULT_VENUE,
                    "address": address,
                    "title": card_title,
                    "start_time": start_time_seq,
                    "description": description,
                    "big_desc": big_desc,
                    "external_url": detail_url,
                    "image_url": image_url,
                    "ticket_link": ticket_link
                }

                raw_records.append(item_data)
                logger.info(f"  Title      : {item_data['title']}")
                logger.info(f"  Start Time : {item_data['start_time']}")

            except Exception as item_err:
                logger.error(f"[ITEM ERROR] Failed parsing The Valley event #{idx}: {item_err}", exc_info=True)
                continue

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

    if not raw_records:
        return pd.DataFrame()

    return pd.DataFrame(raw_records)


if __name__ == "__main__":
    logger.info("Executing The Valley Tacoma scraper task...")
    raw_df = scraper()

    if not raw_df.empty:
        logger.info("==================================================================")
        logger.info(f"[PIPELINE HANDOFF] Passing {len(raw_df)} event records to VividaIngestionPipeline...")
        logger.info("==================================================================")

        pipeline = VividaIngestionPipeline(table_name="events")
        success = pipeline.run_standalone_pipeline(raw_df)

        if success:
            logger.info("[PIPELINE SUCCESS] The Valley Tacoma scraper execution completed successfully.")
        else:
            logger.error("[PIPELINE ERROR] The Valley Tacoma scraper pipeline execution reported errors.")
    else:
        logger.warning("[EXIT] No events scraped. Pipeline handoff skipped.")