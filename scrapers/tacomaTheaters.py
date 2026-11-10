"""
tacomaTheaters.py
Autonomous Production Scraper for Tacoma City Theaters (Pantages, Rialto, Theatre on the Square).
Expands "Load More" pagination, extracts event details using precise class selectors,
steps into detail pages for rich descriptions, and routes records to VividaIngestionPipeline.
"""

import logging
import os
import re
import sys
import time
from typing import Dict, List, Optional, Set

from bs4 import BeautifulSoup
import pandas as pd
from pyvirtualdisplay import Display
from selenium.webdriver.common.by import By
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
logger = logging.getLogger("scrape_tacoma_city_theaters")

BASE_URL = "https://www.tacomacitytheaters.org"
TARGET_URL = "https://www.tacomacitytheaters.org/events"
DEFAULT_VENUE = "Tacoma City Theaters"
DEFAULT_ADDRESS = "901 Broadway, Tacoma, WA 98402"


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


def clean_and_parse_single_date(raw_str: str) -> Optional[pd.Timestamp]:
    """Cleans weekday names and whitespace before converting to Timestamp."""
    if not raw_str:
        return None
    clean_str = re.sub(r"\s+", " ", str(raw_str).replace("\xa0", " ")).strip()
    clean_str = re.sub(r"^(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*,?\s*", "", clean_str, flags=re.IGNORECASE)

    dt = pd.to_datetime(clean_str, errors="coerce", format="mixed")
    return dt if pd.notnull(dt) else None


def parse_date_string(raw_date_str: str) -> Optional[List[str]]:
    """Parses Tacoma City Theaters date strings, handling single dates, intra-month ranges,
    cross-month ranges, and cross-year ranges. Preserves discrete [start, end] pairs.
    """
    if not raw_date_str:
        return None

    # 1. Normalize non-breaking spaces, multi-spaces, and leading weekday names
    clean_str = re.sub(r"\s+", " ", str(raw_date_str).replace("\xa0", " ")).strip()
    clean_str = re.sub(r"^(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*,?\s*", "", clean_str, flags=re.IGNORECASE)

    # 2. Case A: Intra-Month Range -> "Dec 4 - 6, 2026" or "Dec 4-6, 2026"
    intra_match = re.search(r"([A-Za-z]+)\s+(\d{1,2})\s*[-–—]\s*(\d{1,2}),?\s*(\d{4})", clean_str)
    if intra_match:
        month_str, day1_str, day2_str, year_str = intra_match.groups()
        dt_start = pd.to_datetime(f"{month_str} {day1_str}, {year_str}", errors="coerce")
        dt_end = pd.to_datetime(f"{month_str} {day2_str}, {year_str}", errors="coerce")
        if pd.notnull(dt_start) and pd.notnull(dt_end):
            s_str = dt_start.strftime("%Y-%m-%d")
            e_str = dt_end.strftime("%Y-%m-%d")
            return [s_str, e_str] if s_str != e_str else [s_str]

    # 3. Case B: Cross-Month Range -> "Nov 28 - Dec 2, 2026"
    cross_month_match = re.search(r"([A-Za-z]+)\s+(\d{1,2})\s*[-–—]\s*([A-Za-z]+)\s+(\d{1,2}),?\s*(\d{4})", clean_str)
    if cross_month_match:
        m1, d1, m2, d2, y = cross_month_match.groups()
        dt_start = pd.to_datetime(f"{m1} {d1}, {y}", errors="coerce")
        dt_end = pd.to_datetime(f"{m2} {d2}, {y}", errors="coerce")
        if pd.notnull(dt_start) and pd.notnull(dt_end):
            return [dt_start.strftime("%Y-%m-%d"), dt_end.strftime("%Y-%m-%d")]

    # 4. Case C: Cross-Year Range -> "Dec 28, 2026 - Jan 3, 2027"
    cross_year_match = re.search(r"([A-Za-z]+\s+\d{1,2},?\s*\d{4})\s*[-–—]\s*([A-Za-z]+\s+\d{1,2},?\s*\d{4})", clean_str)
    if cross_year_match:
        part1, part2 = cross_year_match.groups()
        dt_start = pd.to_datetime(part1, errors="coerce")
        dt_end = pd.to_datetime(part2, errors="coerce")
        if pd.notnull(dt_start) and pd.notnull(dt_end):
            return [dt_start.strftime("%Y-%m-%d"), dt_end.strftime("%Y-%m-%d")]

    # 5. Case D: Single Date Fallback
    dt_single = clean_and_parse_single_date(clean_str)
    if dt_single:
        return [dt_single.strftime("%Y-%m-%d")]

    # 6. Fallback string splitting
    parts = re.split(r"\s*[-–—\bto\b]\s*", clean_str, flags=re.IGNORECASE)
    if len(parts) >= 2:
        part_a = parts[0].strip()
        part_b = parts[1].strip()
        
        year_match = re.search(r"\b(\d{4})\b", part_b)
        year_str = year_match.group(1) if year_match else None

        dt_start = pd.to_datetime(f"{part_a}, {year_str}" if year_str and not re.search(r"\d{4}", part_a) else part_a, errors="coerce")
        dt_end = pd.to_datetime(part_b, errors="coerce")

        if pd.notnull(dt_start) and pd.notnull(dt_end):
            s_str = dt_start.strftime("%Y-%m-%d")
            e_str = dt_end.strftime("%Y-%m-%d")
            return [s_str, e_str] if s_str != e_str else [s_str]

    return None


def scraper() -> pd.DataFrame:
    """Scrapes Tacoma City Theaters by fully expanding event pagination using undetected Chrome in Xvfb."""
    logger.info("==================================================================")
    logger.info(f"[START] Initializing PyVirtualDisplay server for Tacoma City Theaters: {TARGET_URL}")
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
        driver.get(TARGET_URL)
        time.sleep(6)

        # 1. Expand "Load More" pagination until all event cards unfold
        load_clicks = 0
        while True:
            try:
                buttons = driver.find_elements(By.ID, "loadMoreEvents")
                if not buttons:
                    buttons = driver.find_elements(By.CSS_SELECTOR, "a.loadMore, button.loadMore, a.btn-load-more")

                if buttons and buttons[0].is_displayed() and buttons[0].is_enabled():
                    btn = buttons[0]
                    driver.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", btn)
                    time.sleep(1)
                    driver.execute_script("arguments[0].click();", btn)
                    load_clicks += 1
                    logger.info(f"[PAGINATION] Clicked 'Load More' button (click #{load_clicks})...")
                    time.sleep(3)
                else:
                    logger.info(f"[PAGINATION COMPLETE] All events unfolded. Total clicks: {load_clicks}.")
                    break
            except Exception as nav_err:
                logger.info(f"[PAGINATION END] No further pagination button interactable after {load_clicks} clicks: {nav_err}")
                break

        # 2. Parse fully unfolded DOM using exact CSS selectors
        main_html = clean_html_source(driver.page_source)
        soup = BeautifulSoup(main_html, "html.parser")

        target_section = soup.find("div", class_="eventList event_list_grid event_list is-loaded") or soup

        for element in target_section(["script", "style"]):
            element.decompose()

        # Select all cards containing the eventItem class
        event_items = target_section.select("div.eventItem")
        if not event_items:
            event_items = target_section.find_all("div", class_=re.compile(r"^eventItem entry", re.I))

        logger.info(f"[PARSER SUCCESS] Found {len(event_items)} unfolded Tacoma City Theaters event cards.")

        # 3. Iterate card elements
        for idx, event in enumerate(event_items, start=1):
            try:
                # Title & External URL
                title_el = event.select_one("h3.title a") or event.select_one("h3 a") or event.find("h3")
                if not title_el:
                    continue

                card_title = title_el.get_text(strip=True)
                href = title_el.get("href", "").strip() if title_el.name == "a" else ""
                
                if not href:
                    anchor = event.find("a", href=True)
                    href = anchor["href"].strip() if anchor else ""

                detail_url = None
                if href:
                    detail_url = href if href.startswith("http") else f"{BASE_URL}{href if href.startswith('/') else '/' + href}"

                dedup_key = detail_url or card_title
                if dedup_key in seen_urls:
                    continue
                seen_urls.add(dedup_key)

                logger.info(f"--- [EVENT {len(seen_urls)}/{len(event_items)}] Processing: {card_title} ---")

                # Venue Hall Name (Rialto Theater, Pantages Theater, Theatre on the Square)
                loc_el = event.select_one("div.location")
                venue_name = loc_el.get_text(strip=True) if loc_el else None

                if not venue_name:
                    logo_el = event.select_one("a.venue_logo")
                    venue_name = logo_el.get("title", "").strip() if logo_el and logo_el.get("title") else DEFAULT_VENUE

                # Date parsing - get_text(strip=True) preserves clean string format
                date_el = event.select_one("div.date")
                raw_date_str = date_el.get_text(strip=True) if date_el else None
                start_time_seq = parse_date_string(raw_date_str) if raw_date_str else None

                # Tagline / Short Description
                tagline_el = event.select_one("h4.tagline")
                description = tagline_el.get_text(strip=True) if tagline_el else card_title

                # Image URL
                img_el = event.select_one("div.thumb img") or event.find("img", src=True)
                image_url = None
                if img_el and img_el.get("src"):
                    src = img_el["src"].strip()
                    image_url = src if src.startswith("http") else f"{BASE_URL}{src if src.startswith('/') else '/' + src}"

                # Ticket Link
                ticket_el = event.select_one("a.tickets") or event.find("a", href=re.compile(r"ticket|buy|bit\.ly", re.IGNORECASE))
                ticket_link = ticket_el["href"].strip() if ticket_el and ticket_el.get("href") else detail_url

                # Step into Detail Page for rich content
                big_desc = description
                if detail_url:
                    logger.info(f"  [DETAIL FETCH] Navigating to detail page: {detail_url}")
                    driver.get(detail_url)
                    time.sleep(3)

                    det_soup = BeautifulSoup(clean_html_source(driver.page_source), "html.parser")
                    if det_soup:
                        if not start_time_seq:
                            det_date_el = det_soup.select_one("div.date") or det_soup.find("time")
                            if det_date_el:
                                start_time_seq = parse_date_string(det_date_el.get_text(strip=True))

                        container = (
                            det_soup.select_one("div.leftColumn") or 
                            det_soup.select_one("div.event-description") or 
                            det_soup.find("main") or 
                            det_soup
                        )

                        rich_text = fetch_event_text(container)
                        if rich_text:
                            big_desc = re.sub(r"\s+", " ", rich_text)

                        if not ticket_link or ticket_link == detail_url:
                            t_btn = det_soup.select_one("a.tickets") or det_soup.find("a", href=re.compile(r"ticket|buy|tessitura|bit\.ly", re.IGNORECASE))
                            if t_btn and t_btn.get("href"):
                                ticket_link = t_btn["href"].strip()

                item_data = {
                    "venue_name": venue_name,
                    "address": DEFAULT_ADDRESS,
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
                logger.info(f"  Venue      : {item_data['venue_name']}")
                logger.info(f"  Start Time : {item_data['start_time']}")

            except Exception as item_err:
                logger.error(f"[ITEM ERROR] Failed parsing Tacoma City Theaters event #{idx}: {item_err}", exc_info=True)
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
    logger.info("Executing Tacoma City Theaters scraper task...")
    raw_df = scraper()

    if not raw_df.empty:
        logger.info("==================================================================")
        logger.info(f"[PIPELINE HANDOFF] Passing {len(raw_df)} event records to VividaIngestionPipeline...")
        logger.info("==================================================================")

        pipeline = VividaIngestionPipeline(table_name="events")
        success = pipeline.run_standalone_pipeline(raw_df)

        if success:
            logger.info("[PIPELINE SUCCESS] Tacoma City Theaters scraper execution completed successfully.")
        else:
            logger.error("[PIPELINE ERROR] Tacoma City Theaters scraper pipeline execution reported errors.")
    else:
        logger.warning("[EXIT] No events scraped. Pipeline handoff skipped.")