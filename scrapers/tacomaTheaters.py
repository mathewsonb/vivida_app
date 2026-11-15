"""
tacomaTheaters.py
Autonomous Production Scraper for Tacoma City Theaters (Pantages, Rialto, Theatre on the Square).
Expands "Load More" pagination, extracts event details using precise class selectors,
steps into detail pages for rich descriptions, and routes records to VividaIngestionPipeline.
"""
import glob
import logging
import re
import shutil
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
    parse_event_dates,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(filename)s:%(lineno)d] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("scrape_tacoma_city_theaters")

BASE_URL = "https://www.tacomacitytheaters.org"
TARGET_URL = "https://www.tacomacitytheaters.org/events"
DEFAULT_VENUE = "Tacoma City Theaters"
DEFAULT_ADDRESS = "901 Broadway, Tacoma, WA 98402"

VENUE_ADDRESS_MAP: Dict[str, str] = {
    "pantages": "901 Broadway, Tacoma, WA 98402",
    "rialto": "310 S 9th St, Tacoma, WA 98402",
    "theatre on the square": "915 Broadway, Tacoma, WA 98402",
    "theater on the square": "915 Broadway, Tacoma, WA 98402",
}


def resolve_venue_address(venue_name: str) -> str:
    """Maps specific Tacoma City Theaters stages to their exact street addresses."""
    norm = (venue_name or "").strip().lower()
    for key, addr in VENUE_ADDRESS_MAP.items():
        if key in norm:
            return addr
    return DEFAULT_ADDRESS


def create_stealth_driver() -> uc.Chrome:
    """Instantiates an undetected Chrome driver configured for Xvfb display rendering."""
    options = uc.ChromeOptions()
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-gpu")

    cached_chrome = sorted(
        glob.glob("/home/vscode/.cache/selenium/chrome/linux64/*/chrome")
    )
    chrome_bin = (
        shutil.which("google-chrome")
        or shutil.which("google-chrome-stable")
        or shutil.which("chromium")
        or shutil.which("chromium-browser")
        or (cached_chrome[-1] if cached_chrome else None)
    )

    if not chrome_bin:
        raise RuntimeError(
            "No Chrome or Chromium executable found. Install google-chrome-stable first."
        )

    options.binary_location = chrome_bin
    return uc.Chrome(options=options, version_main=None)


def scraper() -> pd.DataFrame:
    """Scrapes Tacoma City Theaters by fully expanding event pagination using undetected Chrome in Xvfb."""
    logger.info(
        f"[START] Initializing PyVirtualDisplay server for Tacoma City Theaters: {TARGET_URL}"
    )

    display = Display(visible=0, size=(1920, 1080), backend="xvfb")
    display.start()

    driver = None
    raw_records: List[Dict[str, Optional[str]]] = []
    seen_urls: Set[str] = set()

    try:
        driver = create_stealth_driver()
        driver.get(TARGET_URL)
        time.sleep(6)

        load_clicks = 0
        while True:
            try:
                buttons = driver.find_elements(By.ID, "loadMoreEvents")
                if not buttons:
                    buttons = driver.find_elements(
                        By.CSS_SELECTOR, "a.loadMore, button.loadMore, a.btn-load-more"
                    )

                if buttons and buttons[0].is_displayed() and buttons[0].is_enabled():
                    btn = buttons[0]
                    driver.execute_script(
                        "arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});",
                        btn,
                    )
                    time.sleep(1)
                    driver.execute_script("arguments[0].click();", btn)
                    load_clicks += 1
                    logger.info(
                        f"[PAGINATION] Clicked 'Load More' button (click #{load_clicks})..."
                    )
                    time.sleep(3)
                else:
                    logger.info(
                        f"[PAGINATION COMPLETE] All events unfolded. Total clicks: {load_clicks}."
                    )
                    break
            except Exception as nav_err:
                logger.info(
                    f"[PAGINATION END] Pagination stopped after {load_clicks} clicks: {nav_err}"
                )
                break

        main_html = clean_html_source(driver.page_source)
        soup = BeautifulSoup(main_html, "html.parser")

        target_section = (
            soup.find(
                "div", class_="eventList event_list_grid event_list is-loaded")
            or soup
        )
        for element in target_section(["script", "style"]):
            element.decompose()

        event_items = target_section.select("div.eventItem")
        if not event_items:
            event_items = target_section.find_all(
                "div", class_=re.compile(r"^eventItem entry", re.I)
            )

        logger.info(
            f"[PARSER SUCCESS] Found {len(event_items)} unfolded Tacoma City Theaters event cards."
        )

        for idx, event in enumerate(event_items, start=1):
            try:
                title_el = (
                    event.select_one("h3.title a")
                    or event.select_one("h3 a")
                    or event.find("h3")
                )
                if not title_el:
                    continue

                card_title = title_el.get_text(strip=True)
                href = (
                    title_el.get("href", "").strip()
                    if title_el.name == "a"
                    else ""
                )
                if not href:
                    anchor = event.find("a", href=True)
                    href = anchor["href"].strip() if anchor else ""

                detail_url = None
                if href:
                    detail_url = (
                        href
                        if href.startswith("http")
                        else f"{BASE_URL}{href if href.startswith('/') else '/' + href}"
                    )

                dedup_key = detail_url or card_title
                if dedup_key in seen_urls:
                    continue
                seen_urls.add(dedup_key)

                loc_el = event.select_one("div.location")
                venue_name = loc_el.get_text(strip=True) if loc_el else None
                if not venue_name:
                    logo_el = event.select_one("a.venue_logo")
                    venue_name = (
                        logo_el.get("title", "").strip()
                        if logo_el and logo_el.get("title")
                        else DEFAULT_VENUE
                    )

                date_el = event.select_one("div.date")
                raw_date_str = date_el.get_text(
                    strip=True) if date_el else None
                start_time_seq = parse_event_dates(
                    raw_date_str) if raw_date_str else []

                tagline_el = event.select_one("h4.tagline")
                description = (
                    tagline_el.get_text(
                        strip=True) if tagline_el else card_title
                )

                img_el = event.select_one(
                    "div.thumb img") or event.find("img", src=True)
                image_url = None
                if img_el and img_el.get("src"):
                    src = img_el["src"].strip()
                    image_url = (
                        src
                        if src.startswith("http")
                        else f"{BASE_URL}{src if src.startswith('/') else '/' + src}"
                    )

                ticket_el = event.select_one("a.tickets") or event.find(
                    "a", href=re.compile(r"ticket|buy|bit\.ly", re.IGNORECASE)
                )
                ticket_link = (
                    ticket_el["href"].strip()
                    if ticket_el and ticket_el.get("href")
                    else detail_url
                )

                big_desc = description
                if detail_url:
                    driver.get(detail_url)
                    time.sleep(3)

                    det_soup = BeautifulSoup(
                        clean_html_source(driver.page_source), "html.parser"
                    )
                    if det_soup:
                        if not start_time_seq:
                            det_date_el = (
                                det_soup.select_one("div.date")
                                or det_soup.find("time")
                            )
                            if det_date_el:
                                start_time_seq = parse_event_dates(
                                    det_date_el.get_text(strip=True)
                                )

                        container = (
                            det_soup.select_one("div.leftColumn")
                            or det_soup.select_one("div.event-description")
                            or det_soup.find("main")
                            or det_soup
                        )

                        rich_text = fetch_event_text(container)
                        if rich_text:
                            big_desc = re.sub(r"\s+", " ", rich_text)

                        if not ticket_link or ticket_link == detail_url:
                            t_btn = det_soup.select_one("a.tickets") or det_soup.find(
                                "a",
                                href=re.compile(
                                    r"ticket|buy|tessitura|bit\.ly", re.IGNORECASE
                                ),
                            )
                            if t_btn and t_btn.get("href"):
                                ticket_link = t_btn["href"].strip()

                if not start_time_seq:
                    logger.warning(
                        f"[SKIP] No valid dates parsed for '{card_title}' (raw='{raw_date_str}')"
                    )
                    continue

                raw_records.append(
                    {
                        "venue_name": venue_name,
                        "address": resolve_venue_address(venue_name),
                        "title": card_title,
                        "start_time": start_time_seq,
                        "description": description,
                        "big_desc": big_desc,
                        "external_url": detail_url,
                        "image_url": image_url,
                        "ticket_link": ticket_link,
                    }
                )

            except Exception as item_err:
                logger.error(
                    f"[ITEM ERROR] Failed parsing event #{idx}: {item_err}",
                    exc_info=True,
                )
                continue

    except Exception as e:
        logger.error(
            f"[FATAL ERROR] Exception during scraping loop: {e}", exc_info=True
        )
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass
        display.stop()

    return pd.DataFrame(raw_records) if raw_records else pd.DataFrame()


if __name__ == "__main__":
    logger.info("Executing Tacoma City Theaters scraper task...")
    raw_df = scraper()

    if not raw_df.empty:
        pipeline = VividaIngestionPipeline(table_name="events")
        success = pipeline.run_standalone_pipeline(raw_df)
        if success:
            logger.info(
                "[PIPELINE SUCCESS] Tacoma City Theaters scraper execution completed successfully."
            )
        else:
            logger.error(
                "[PIPELINE ERROR] Tacoma City Theaters scraper pipeline execution reported errors."
            )
    else:
        logger.warning("[EXIT] No events scraped. Pipeline handoff skipped.")
