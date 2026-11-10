"""
tacomaArtsLive.py
Autonomous Production Scraper for Tacoma Arts Live (Tacoma, WA).
Includes targeted Elementor text-editor container parsing for rich big_desc extraction.
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
import undetected_chromedriver as uc

from vivida_etl import (
    VividaIngestionPipeline,
    fetch_event_text,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(filename)s:%(lineno)d] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("scrape_tacoma_arts_live")

BASE_URL = "https://www.tacomaartslive.org"
TARGET_URL = "https://www.tacomaartslive.org/upcoming-events/"
DEFAULT_VENUE = "Tacoma Arts Live"
DEFAULT_ADDRESS = "901 Broadway, Tacoma, WA 98402"

MONTH_NAMES = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec|January|February|March|April|May|June|July|August|September|October|November|December)"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SANDBOX_PATH = (
    os.path.join(os.path.dirname(BASE_DIR), "dev", "sandbox.html")
    if os.path.basename(BASE_DIR) == "scrapers"
    else os.path.join(BASE_DIR, "dev", "sandbox.html")
)


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


def scroll_page_to_bottom(driver: uc.Chrome, max_scrolls: int = 10, pause_time: float = 2.5):
    """Iteratively scrolls down the page to trigger dynamic AJAX loads for all upcoming events."""
    logger.info("[SELENIUM SCROLL] Executing dynamic scroll loop...")
    last_height = driver.execute_script("return document.body.scrollHeight")
    
    for i in range(max_scrolls):
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(pause_time)
        
        try:
            load_more_btns = driver.find_elements("css selector", "a.mec-load-more-button, .mec-load-more-wrap a, a.mec-button")
            for btn in load_more_btns:
                if btn.is_displayed():
                    driver.execute_script("arguments[0].click();", btn)
                    time.sleep(pause_time)
                    break
        except Exception:
            pass

        new_height = driver.execute_script("return document.body.scrollHeight")
        if new_height == last_height:
            break
        last_height = new_height


def parse_event_dates(date_text: str, now: pd.Timestamp) -> Optional[List[str]]:
    """Multi-pattern date parser supporting single dates, day ranges, and month rollovers."""
    if not date_text or pd.isna(date_text):
        return None

    clean_str = re.sub(r"\s+", " ", str(date_text).strip())

    full_date_match = re.search(rf"\b{MONTH_NAMES}\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s+\d{{4}}\b", clean_str, re.I)
    if full_date_match:
        dt = pd.to_datetime(full_date_match.group(0), errors="coerce")
        if pd.notnull(dt):
            return [dt.strftime("%Y-%m-%d")]

    partial_date_match = re.search(rf"\b{MONTH_NAMES}\s+\d{{1,2}}(?:st|nd|rd|th)?\b", clean_str, re.I)
    if partial_date_match:
        full_str = f"{partial_date_match.group(0)}, {now.year}"
        dt = pd.to_datetime(full_str, errors="coerce")
        if pd.notnull(dt):
            if dt.month < now.month:
                dt = dt + pd.DateOffset(years=1)
            return [dt.strftime("%Y-%m-%d")]

    range_parts = re.split(r"\s*[-–—\bto\b]\s*", clean_str, flags=re.IGNORECASE)
    try:
        if len(range_parts) >= 2:
            start_raw, end_raw = range_parts[0].strip(), range_parts[1].strip()
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
    """Executes Stage 1 listing fetch and Stage 2 detail page traversals."""
    logger.info("==================================================================")
    logger.info("[START] Initializing PyVirtualDisplay server for Tacoma Arts Live scrape...")
    logger.info("==================================================================")
    
    display = Display(visible=0, size=(1920, 1080), backend="xvfb")
    display.start()

    driver = None
    raw_records: List[Dict[str, Optional[str]]] = []
    seen_urls: Set[str] = set()

    try:
        logger.info("[SELENIUM] Launching undetected Chrome driver session...")
        driver = create_stealth_driver()

        logger.info(f"[SELENIUM NAV] Fetching main calendar grid: {TARGET_URL}")
        driver.get(TARGET_URL)
        time.sleep(8)

        scroll_page_to_bottom(driver)

        main_html = driver.page_source
        
        os.makedirs(os.path.dirname(SANDBOX_PATH), exist_ok=True)
        with open(SANDBOX_PATH, "w", encoding="utf-8") as f:
            f.write(main_html)
            f.flush()

        soup = BeautifulSoup(main_html, "html.parser")
        event_cards = soup.select("article.mec-event-article")
        if not event_cards:
            event_cards = soup.select("div.col-md-4")

        if not event_cards:
            logger.warning("[PARSER WARNING] Found 0 event cards matching selector grid.")
            return pd.DataFrame()

        total_cards = len(event_cards)
        logger.info(f"[PARSER SUCCESS] Found {total_cards} candidate MEC event cards.")

        now = pd.Timestamp.now()

        for idx, card in enumerate(event_cards, start=1):
            try:
                title_el = card.find("h4", class_=re.compile(r"mec-event-title|title", re.I)) or card.find(["h4", "h3"])
                link_tag = title_el.find("a", href=True) if title_el else card.find("a", href=True)
                
                card_title = link_tag.get_text(strip=True) if link_tag else None
                detail_url = link_tag.get("href") if link_tag else None
                if detail_url and not detail_url.startswith("http"):
                    detail_url = f"{BASE_URL}{detail_url if detail_url.startswith('/') else '/' + detail_url}"

                if not card_title:
                    continue

                dedup_key = detail_url or card_title
                if dedup_key in seen_urls:
                    continue
                seen_urls.add(dedup_key)

                logger.info(f"--- [EVENT {len(seen_urls)}/{total_cards}] Processing: {card_title} ---")

                date_span = card.find("span", class_=re.compile(r"mec-start-date-label|date", re.I))
                raw_date_str = date_span.get_text(strip=True) if date_span else None
                start_time_list = parse_event_dates(raw_date_str, now)

                loc_p = card.find("p", class_=re.compile(r"mec-grid-event-location|location", re.I))
                venue_name = DEFAULT_VENUE
                address = DEFAULT_ADDRESS
                if loc_p:
                    loc_parts = [p.strip() for p in loc_p.get_text(strip=True).split(",") if p.strip()]
                    if len(loc_parts) >= 1:
                        venue_name = loc_parts[0]
                    if len(loc_parts) >= 2:
                        address = ", ".join(loc_parts[1:])

                img_tag = card.find("img", src=True)
                image_url = None
                if img_tag and img_tag["src"]:
                    src = img_tag["src"].strip()
                    image_url = src if src.startswith("http") else f"{BASE_URL}{src if src.startswith('/') else '/' + src}"

                big_desc = card_title
                description = card_title
                ticket_link = detail_url

                if detail_url:
                    logger.info(f"  [DETAIL FETCH] Navigating to detail page: {detail_url}")
                    driver.get(detail_url)
                    time.sleep(3)
                    
                    det_soup = BeautifulSoup(driver.page_source, "html.parser")
                    
                    # Targeted Elementor text-editor container locator
                    det_container = (
                        det_soup.find("div", class_=re.compile(r"elementor-widget-text-editor", re.I)) or
                        det_soup.find("div", class_="elementor-widget-container") or
                        det_soup.find("div", class_=re.compile(r"entry-content|mec-single-event-description", re.I)) or
                        det_soup.find("main") or
                        det_soup
                    )

                    if not start_time_list:
                        det_date = det_soup.find("span", class_=re.compile(r"mec-start-date-label|date", re.I))
                        if det_date:
                            start_time_list = parse_event_dates(det_date.get_text(strip=True), now)

                    rich_text = fetch_event_text(det_container)
                    if rich_text:
                        big_desc = re.sub(r"\s+", " ", rich_text)

                    p_tags = det_container.find_all("p")
                    p_text_blocks = [re.sub(r"\s+", " ", p.get_text(strip=True)) for p in p_tags if p.get_text(strip=True)]
                    if p_text_blocks:
                        description = " ".join(p_text_blocks[:2])
                    else:
                        description = big_desc[:250] if big_desc else card_title

                    t_anchor = det_soup.find("a", href=re.compile(r"ticket|buy|tessitura|ordertickets", re.IGNORECASE))
                    if t_anchor and t_anchor.get("href"):
                        ticket_link = t_anchor["href"].strip()

                item_data = {
                    "venue_name": venue_name,
                    "address": address,
                    "title": card_title,
                    "start_time": start_time_list,
                    "description": description,
                    "big_desc": big_desc,
                    "external_url": detail_url,
                    "image_url": image_url,
                    "ticket_link": ticket_link,
                }

                raw_records.append(item_data)
                logger.info(f"  Title      : {item_data['title']}")
                logger.info(f"  Start Time : {item_data['start_time']}")

            except Exception as item_err:
                logger.error(f"[ITEM ERROR] Failed parsing event #{idx}: {item_err}", exc_info=True)
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
    logger.info("Executing Tacoma Arts Live scraper task...")
    raw_df = scraper()

    if not raw_df.empty:
        pipeline = VividaIngestionPipeline(table_name="events")
        success = pipeline.run_standalone_pipeline(raw_df)
        if success:
            logger.info("[PIPELINE SUCCESS] Tacoma Arts Live scraper completed successfully.")
        else:
            logger.error("[PIPELINE ERROR] Tacoma Arts Live scraper pipeline execution reported errors.")
    else:
        logger.warning("[EXIT] No events scraped. Pipeline handoff skipped.")