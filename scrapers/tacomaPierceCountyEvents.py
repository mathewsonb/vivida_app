"""
tacomaPierceCountyEvents.py
Autonomous Production Scraper for Visit Tacoma / Pierce County (Simpleview Engine).
Fetches initial event queue via in-browser JavaScript fetch() execution,
expands recurring date patterns (daily/weekly/monthly), preserves discrete start/end dates,
and routes records to VividaIngestionPipeline.
"""

import json
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
    clean_html_source,
    fetch_event_text,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(filename)s:%(lineno)d] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("scrape_tacoma_pierce_county")

BASE_URL = "https://www.visitpiercecounty.com"
API_KEY = "8ebe197e5ce45b9490c9c4c5a24b2e01"
DEFAULT_VENUE = "Tacoma / Pierce County"
DEFAULT_ADDRESS = "Tacoma, WA"


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


def parse_simpleview_api_dates(doc: dict, max_days_window: int = 60) -> Optional[List[str]]:
    """Generates a date sequence by interpreting the 'recurrence' string and 'recurType' enum.
    Discretely preserves [startDate, endDate] when no recurrence rule is specified.
    """
    if not isinstance(doc, dict):
        return None

    raw_start = doc.get("startDate") or doc.get("date")
    raw_end = doc.get("endDate")

    from_dt = pd.to_datetime(raw_start, errors="coerce") if raw_start else None
    to_dt = pd.to_datetime(raw_end, errors="coerce") if raw_end else None

    if not from_dt or pd.isnull(from_dt):
        return None

    start_str = from_dt.strftime("%Y-%m-%d")

    if not to_dt or pd.isnull(to_dt) or to_dt.date() <= from_dt.date():
        return [start_str]

    end_str = to_dt.strftime("%Y-%m-%d")
    recurrence_str = str(doc.get("recurrence", "")).lower()
    recur_type = doc.get("recurType")

    cutoff_dt = min(to_dt.date(), (from_dt + pd.Timedelta(days=max_days_window)).date())

    # 1. Weekly Recurrence (7-day step)
    if "weekly" in recurrence_str or recur_type == 3:
        date_range = pd.date_range(start=from_dt.date(), end=cutoff_dt, freq="7D")
        return [d.strftime("%Y-%m-%d") for d in date_range]

    # 2. Monthly Recurrence (Monthly step)
    elif "monthly" in recurrence_str or recur_type == 5:
        dates = []
        curr = from_dt.date()
        while curr <= cutoff_dt:
            dates.append(curr.strftime("%Y-%m-%d"))
            curr = (pd.Timestamp(curr) + pd.DateOffset(months=1)).date()
        return dates

    # 3. Daily Recurrence (Continuous 1-day step)
    elif "daily" in recurrence_str or recur_type == 1:
        date_range = pd.date_range(start=from_dt.date(), end=cutoff_dt, freq="D")
        return [d.strftime("%Y-%m-%d") for d in date_range]

    # 4. Discrete Non-Recurring Span (No interpolation)
    else:
        return [start_str, end_str] if start_str != end_str else [start_str]


def fetch_simpleview_events_via_js(driver: uc.Chrome, api_url: str) -> List[dict]:
    """Navigates to the main events page to pass Cloudflare/WAF checks, 
    then executes an in-browser JS fetch() call to retrieve the API payload using active session tokens.
    """
    try:
        landing_url = "https://www.visitpiercecounty.com/events/"
        logger.info(f"[SELENIUM NAV] Navigating to landing page to establish session: {landing_url}")
        driver.get(landing_url)
        time.sleep(5)

        logger.info(f"[JS FETCH] Executing API fetch in browser context for: {api_url}")
        js_script = """
        const callback = arguments[arguments.length - 1];
        fetch(arguments[0], {
            headers: {
                'Accept': 'application/json, text/plain, */*'
            }
        })
        .then(response => response.json())
        .then(data => callback(data))
        .catch(err => callback({ error: err.toString() }));
        """
        
        driver.set_script_timeout(20)
        result = driver.execute_async_script(js_script, api_url)

        if isinstance(result, dict) and "error" in result:
            logger.error(f"[JS FETCH ERROR] Browser fetch failed: {result['error']}")
            return []

        if isinstance(result, dict):
            docs_obj = result.get("docs")
            if isinstance(docs_obj, dict):
                docs = docs_obj.get("docs", [])
            elif isinstance(docs_obj, list):
                docs = docs_obj
            else:
                docs = []
        else:
            docs = []

        logger.info(f"[JS FETCH SUCCESS] Extracted {len(docs)} event records from browser response.")
        return docs

    except Exception as e:
        logger.error(f"[JS FETCH FATAL ERROR] Failed executing in-browser API fetch: {e}", exc_info=True)
        return []


def scraper(days_ahead: int = 30) -> pd.DataFrame:
    """Scrapes Visit Tacoma / Pierce County events using REST API queue and stealth Chrome driver."""
    logger.info("==================================================================")
    logger.info("[START] Initializing PyVirtualDisplay server for Visit Tacoma / Pierce County...")
    logger.info("==================================================================")

    display = Display(visible=0, size=(1920, 1080), backend="xvfb")
    display.start()

    driver = None
    events_data = []
    seen_urls: Set[str] = set()

    try:
        logger.info("[SELENIUM] Launching undetected Chrome driver instance...")
        driver = create_stealth_driver()

        now = pd.Timestamp.now(tz="UTC")
        start_date = now.strftime("%Y-%m-%d")
        end_date = (now + pd.Timedelta(days=days_ahead)).strftime("%Y-%m-%d")
        limit = 500

        api_url = (
            f"{BASE_URL}/includes/rest_v2/plugins_events_events_by_date/find/"
            f"?json=%7B%22filter%22%3A%7B%22active%22%3Atrue%2C%22%24and%22%3A%5B%7B%22categories.catId%22%3A%7B%22%24in%22%3A%5B%228%22%2C%2210%22%2C%2214%22%2C%229%22%2C%2220%22%2C%2213%22%2C%2212%22%2C%2211%22%5D%7D%7D%5D%2C%22date_range%22%3A%7B%22start%22%3A%7B%22%24date%22%3A%22{start_date}T08%3A00%3A00.000Z%22%7D%2C%22end%22%3A%7B%22%24date%22%3A%22{end_date}T08%3A00%3A00.000Z%22%7D%7D%7D%2C%22options%22%3A%7B%22limit%22%3A{limit}%2C%22skip%22%3A0%2C%22count%22%3Atrue%2C%22castDocs%22%3Afalse%2C%22fields%22%3A%7B%22_id%22%3A1%2C%22location%22%3A1%2C%22date%22%3A1%2C%22startDate%22%3A1%2C%22endDate%22%3A1%2C%22recurrence%22%3A1%2C%22recurType%22%3A1%2C%22latitude%22%3A1%2C%22longitude%22%3A1%2C%22media_raw%22%3A1%2C%22recid%22%3A1%2C%22title%22%3A1%2C%22url%22%3A1%2C%22categories%22%3A1%2C%22accountId%22%3A1%2C%22city%22%3A1%2C%22region%22%3A1%2C%22listing.primary_category%22%3A1%2C%22listing.title%22%3A1%2C%22listing.url%22%3A1%2C%22listing.recid%22%3A1%2C%22listing.acctid%22%3A1%2C%22listing.region%22%3A1%2C%22listing.city%22%3A1%2C%22listing.rankname%22%3A1%7D%2C%22hooks%22%3A%5B%5D%2C%22sort%22%3A%7B%22date%22%3A1%2C%22rank%22%3A1%2C%22title_sort%22%3A1%7D%7D%7D&token={API_KEY}"
        )

        docs = fetch_simpleview_events_via_js(driver, api_url)
        logger.info(f"[REST API SUCCESS] Processing {len(docs)} event records from API index.")

        if not docs:
            return pd.DataFrame()

        for idx, doc in enumerate(docs, start=1):
            try:
                if not isinstance(doc, dict):
                    continue

                rel_url = doc.get("url")
                if not rel_url:
                    continue

                full_url = rel_url if rel_url.startswith("http") else f"{BASE_URL}{rel_url if rel_url.startswith('/') else '/' + rel_url}"
                
                if full_url in seen_urls:
                    continue
                seen_urls.add(full_url)

                api_title = doc.get("title") or doc.get("listing", {}).get("title")
                card_title = re.sub(r"\s+", " ", str(api_title).strip()) if api_title else None
                start_time_seq = parse_simpleview_api_dates(doc)

                logger.info(f"--- [EVENT {len(seen_urls)}/{len(docs)}] Processing: {card_title or full_url} ---")

                api_location = doc.get("location")
                api_venue = (api_location.strip() if api_location and api_location.strip() else None) or doc.get("listing", {}).get("title") or DEFAULT_VENUE
                api_city = doc.get("city") or doc.get("listing", {}).get("city") or "Tacoma"
                api_address = f"{api_city}, WA"

                item_data = {
                    "venue_name": api_venue,
                    "address": api_address,
                    "title": card_title,
                    "start_time": start_time_seq,
                    "description": None,
                    "big_desc": None,
                    "external_url": full_url,
                    "image_url": None,
                    "ticket_link": None
                }

                # Step into detail page for rich description
                driver.get(full_url)
                time.sleep(3)

                det_soup = BeautifulSoup(clean_html_source(driver.page_source), "html.parser")
                main_content = det_soup.find("div", id="main-content") or det_soup
                info_sec = main_content.find("div", class_=re.compile(r"info-section", re.I))

                if not item_data["title"] and info_sec:
                    h1_tag = info_sec.find("h1")
                    if h1_tag:
                        item_data["title"] = h1_tag.get_text(strip=True)

                if not item_data["title"]:
                    continue

                # DOM Date Fallback
                if not item_data["start_time"] and info_sec:
                    dd_list = info_sec.find_all("dd")
                    if dd_list:
                        raw_dates_str = dd_list[1].get_text(strip=True) if len(dd_list) > 1 else dd_list[0].get_text(strip=True)
                        if "-" in raw_dates_str:
                            parts = raw_dates_str.split("-")
                            p_start = pd.to_datetime(parts[0].strip(), errors="coerce")
                            p_end = pd.to_datetime(parts[1].strip(), errors="coerce")
                            if pd.notnull(p_start) and pd.notnull(p_end):
                                s_str = p_start.strftime("%Y-%m-%d")
                                e_str = p_end.strftime("%Y-%m-%d")
                                item_data["start_time"] = [s_str, e_str] if s_str != e_str else [s_str]
                        else:
                            dt_single = pd.to_datetime(raw_dates_str.strip(), errors="coerce")
                            if pd.notnull(dt_single):
                                item_data["start_time"] = [dt_single.strftime("%Y-%m-%d")]

                # Description parsing
                desc_tab = main_content.find("div", id="descriptionTab") or main_content.find("div", class_=re.compile(r"description", re.I))
                if desc_tab:
                    item_data["description"] = re.sub(r"\s+", " ", desc_tab.get_text(strip=True, separator=" "))

                # Rich Expanded Description
                rich_text = fetch_event_text(main_content)
                if rich_text:
                    item_data["big_desc"] = re.sub(r"\s+", " ", rich_text)
                else:
                    item_data["big_desc"] = item_data["description"] or item_data["title"]

                if not item_data["description"]:
                    item_data["description"] = item_data["big_desc"][:250] if item_data["big_desc"] else item_data["title"]

                # Image URL
                img_media = doc.get("media_raw", [])
                if img_media and isinstance(img_media, list) and len(img_media) > 0:
                    item_data["image_url"] = img_media[0].get("mediaurl")
                else:
                    img_tag = main_content.find("img", class_=re.compile(r"slide-img|loaded", re.I)) or main_content.find("img", src=True)
                    if img_tag and img_tag.get("src"):
                        src = img_tag["src"].strip()
                        item_data["image_url"] = src if src.startswith("http") else f"{BASE_URL}{src if src.startswith('/') else '/' + src}"

                # Ticket Link
                actions_div = main_content.find("div", class_=re.compile(r"bottom-actions", re.I))
                if actions_div and actions_div.find("a", href=True):
                    item_data["ticket_link"] = actions_div.find("a")["href"].strip()
                else:
                    item_data["ticket_link"] = full_url

                if item_data["start_time"]:
                    events_data.append(item_data)
                    logger.info(f"  Title      : {item_data['title']}")
                    logger.info(f"  Start Time : {item_data['start_time']}")

            except Exception as item_err:
                logger.error(f"[ITEM ERROR] Failed parsing Simpleview event #{idx}: {item_err}", exc_info=True)
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

    if not events_data:
        return pd.DataFrame()

    return pd.DataFrame(events_data)


if __name__ == "__main__":
    logger.info("Executing Visit Tacoma / Pierce County scraper task...")
    raw_df = scraper(days_ahead=30)

    if not raw_df.empty:
        logger.info("==================================================================")
        logger.info(f"[PIPELINE HANDOFF] Passing {len(raw_df)} event records to VividaIngestionPipeline...")
        logger.info("==================================================================")
        
        pipeline = VividaIngestionPipeline(table_name="events")
        success = pipeline.run_standalone_pipeline(raw_df)
        
        if success:
            logger.info("[PIPELINE SUCCESS] Visit Tacoma / Pierce County scraper execution completed successfully.")
        else:
            logger.error("[PIPELINE ERROR] Visit Tacoma / Pierce County scraper pipeline execution reported errors.")
    else:
        logger.warning("[EXIT] No events scraped. Pipeline handoff skipped.")