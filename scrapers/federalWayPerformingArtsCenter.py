"""
federalWayPerformingArtsCenter.py
Raw Ingestion Script: Full Top-of-Funnel + Detail Traversal.
Loops through all events, extracts raw listing and detail page fields,
maps output to standard schema keys without custom processing,
and exports the raw DataFrame to scraper_cache/ for manual inspection.
"""
import logging
import os
import re
import sys
import time
from typing import Dict, List, Optional

from bs4 import BeautifulSoup
import pandas as pd
from pyvirtualdisplay import Display
from selenium import webdriver
from selenium.webdriver.chrome.options import Options

# Configure stdout logging with explicit timestamps and line numbers
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(filename)s:%(lineno)d] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("scrape_fwpaec_raw")

TARGET_URL = "https://fwpaec.org/event-calendar/"
CACHE_DIR = "scraper_cache"
CSV_OUTPUT_PATH = os.path.join(CACHE_DIR, "raw_events_dataframe.csv")
JSON_OUTPUT_PATH = os.path.join(CACHE_DIR, "raw_events_dataframe.json")

DEFAULT_VENUE = "Federal Way Performing Arts and Event Center"
DEFAULT_ADDRESS = "31510 Pete von Reichbauer Way S Federal Way, WA 98003"


def create_driver(options: Options) -> webdriver.Chrome:
    """Instantiates a Selenium Chrome driver with explicitly verified binary paths."""
    if os.path.exists("/usr/bin/google-chrome"):
        options.binary_location = "/usr/bin/google-chrome"
    elif os.path.exists("/usr/bin/chromium"):
        options.binary_location = "/usr/bin/chromium"
    return webdriver.Chrome(options=options)


def parse_detail_page_html(html_content: str) -> Dict[str, Optional[str]]:
    """Extracts raw detail parameters from an MEC single event page using verified DOM locators."""
    soup = BeautifulSoup(html_content, "html.parser")
    container = soup.find("section", class_="mec-container", id="main-content")
    root = container if container else soup

    detail_data = {
        "detail_title": None,
        "detail_full_date": None,
        "detail_ticket_link": None,
        "description": None,
        "big_desc": None,
    }

    # 1. Title Locator: <h1 class="mec-single-title">
    title_el = root.find("h1", class_="mec-single-title")
    if title_el:
        detail_data["detail_title"] = title_el.get_text(strip=True)

    # 2. Date Locator: <span class="mec-start-date-label">
    date_el = root.find("span", class_="mec-start-date-label")
    if date_el:
        detail_data["detail_full_date"] = date_el.get_text(strip=True)

    # 3. Registration/Ticket Link: <div class="mec-reg-btn mec-frontbox"> -> <a class="mec-booking-button...">
    reg_btn = root.find("a", class_=re.compile(r"mec-booking-button"))
    if reg_btn and reg_btn.get("href"):
        detail_data["detail_ticket_link"] = reg_btn.get("href")

    # 4. Description Locators: <div class="mec-single-event-description mec-events-content">
    desc_el = root.find("div", class_=re.compile(r"mec-single-event-description"))
    if desc_el:
        raw_big_desc = desc_el.get_text(" ", strip=True)
        detail_data["big_desc"] = re.sub(r"\s+", " ", raw_big_desc)

        p_tags = desc_el.find_all("p")
        if p_tags:
            detail_data["description"] = re.sub(r"\s+", " ", p_tags[0].get_text(" ", strip=True))
        else:
            detail_data["description"] = detail_data["big_desc"][:250] if detail_data["big_desc"] else None

    return detail_data


def compile_all_raw_events() -> pd.DataFrame:
    """Executes Stage 1 (listing parse) and Stage 2 (all detail page traversals)

    using a single virtual display and driver session, returning the target schema DataFrame.
    """
    logger.info("==================================================================")
    logger.info("[WAYPOINT 1] Initializing PyVirtualDisplay server...")
    logger.info("==================================================================")
    
    display = Display(visible=0, size=(1920, 1080))
    display.start()

    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-gpu")

    driver = None
    raw_records: List[Dict[str, Optional[str]]] = []

    try:
        logger.info("[WAYPOINT 2] Launching Chrome driver session...")
        driver = create_driver(options)

        # ------------------------------------------------------------------
        # STAGE 1: Main Calendar Listing Fetch & Parse
        # ------------------------------------------------------------------
        logger.info(f"[WAYPOINT 3] Fetching main calendar grid: {TARGET_URL}")
        driver.get(TARGET_URL)
        time.sleep(5)
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight / 2);")
        time.sleep(2)

        main_html = driver.page_source
        soup = BeautifulSoup(main_html, "html.parser")
        event_articles = soup.find_all("article", class_=re.compile(r"mec-event-article"))

        if not event_articles:
            logger.warning("[PARSER WARNING] Found 0 articles matching class 'mec-event-article'.")
            return pd.DataFrame()

        logger.info("==================================================================")
        logger.info(f"[PARSER SUCCESS] Found {len(event_articles)} MEC event cards. Beginning full traversal...")
        logger.info("==================================================================")

        now = pd.Timestamp.now()

        # ------------------------------------------------------------------
        # STAGE 2: Detail Page Traversal Loop Across ALL Events
        # ------------------------------------------------------------------
        for idx, article in enumerate(event_articles, start=1):
            logger.info(f"--- [EVENT {idx}/{len(event_articles)}] Parsing listing card & detail page ---")

            # Extract card-level attributes
            title_el = article.find("h4", class_="mec-event-title")
            link_tag = title_el.find("a") if title_el else None
            
            card_title = link_tag.get_text(strip=True) if link_tag else None
            detail_url = link_tag.get("href") if link_tag else None

            if not detail_url:
                booking_btn = article.find("a", class_="mec-booking-button")
                if booking_btn:
                    detail_url = booking_btn.get("href")

            month_el = article.find("div", class_="mec-event-month")
            card_date_str = month_el.get_text(strip=True) if month_el else None

            time_el = article.find("span", class_="mec-start-time")
            card_time_str = time_el.get_text(strip=True) if time_el else None

            venue_el = article.find("div", class_="mec-event-loc-place")
            card_venue = venue_el.get_text(strip=True) if venue_el else DEFAULT_VENUE

            addr_el = article.find("p", class_="mec-grid-event-location")
            card_address = addr_el.get_text(strip=True) if addr_el else DEFAULT_ADDRESS

            img_tag = article.find("img")
            image_url = None
            if img_tag:
                image_url = img_tag.get("data-src") or img_tag.get("src")

            if not detail_url:
                logger.warning(f"[ITEM SKIP] Event {idx}: No detail URL found. Skipping detail scrape.")
                continue

            # Fetch detail page
            logger.info(f"[DETAIL FETCH] Navigating to detail page: {detail_url}")
            driver.get(detail_url)
            time.sleep(4)

            detail_html = driver.page_source
            detail_fields = parse_detail_page_html(detail_html)

            # Date construction into list format
            raw_date = f"{detail_fields['detail_full_date'] or card_date_str} {now.year}".strip()
            start_time_list = None
            try:
                start_dt = pd.to_datetime(raw_date, errors="coerce", format="mixed")
                if pd.notnull(start_dt):
                    if start_dt.month < now.month and str(now.year) not in raw_date:
                        start_dt = start_dt + pd.DateOffset(years=1)
                    start_time_list = [start_dt.strftime("%Y-%m-%d")]
            except Exception as e:
                logger.warning(f"Could not format date '{raw_date}': {e}")

            # Target Schema Mapping
            item_data = {
                "venue_name": card_venue,
                "address": card_address,
                "title": detail_fields["detail_title"] or card_title,
                "start_time": start_time_list,
                "description": detail_fields["description"],
                "big_desc": detail_fields["big_desc"],
                "external_url": detail_url,
                "image_url": image_url,
                "ticket_link": detail_fields["detail_ticket_link"] or detail_url,
            }

            raw_records.append(item_data)

            logger.info(f"  Title       : {item_data['title']}")
            logger.info(f"  Start Time  : {item_data['start_time']}")
            logger.info(f"  Detail URL  : {item_data['external_url']}")
            logger.info(f"  Ticket Link : {item_data['ticket_link']}")
            logger.info(f"  Desc Length : {len(item_data['big_desc']) if item_data['big_desc'] else 0} chars")

    except Exception as e:
        logger.error(f"[FATAL ERROR] Exception encountered during scraping loop: {e}", exc_info=True)
    finally:
        if driver:
            logger.info("[CLEANUP] Closing Chrome driver instance...")
            driver.quit()
        logger.info("[CLEANUP] Stopping PyVirtualDisplay server...")
        display.stop()

    return pd.DataFrame(raw_records)


if __name__ == "__main__":
    os.makedirs(CACHE_DIR, exist_ok=True)
    logger.info("Executing raw FWPAEC full-event compilation...")

    df_raw = compile_all_raw_events()

    if not df_raw.empty:
        logger.info("==================================================================")
        logger.info(f"[RAW EXPORT] Captured {len(df_raw)} raw event records across all pages.")
        
        # Save raw outputs to cache directory for manual verification
        df_raw.to_csv(CSV_OUTPUT_PATH, index=False, encoding="utf-8")
        df_raw.to_json(JSON_OUTPUT_PATH, orient="records", indent=2)
        
        logger.info(f" -> Saved raw CSV to : {os.path.abspath(CSV_OUTPUT_PATH)}")
        logger.info(f" -> Saved raw JSON to: {os.path.abspath(JSON_OUTPUT_PATH)}")
        logger.info("==================================================================")
    else:
        logger.error("[EXPORT FAILED] 0 records parsed. Output files were not updated.")