"""
theValley.py
Autonomous Scraper for The Valley (Tacoma, WA - Squarespace Engine).
Refactored for Xvfb headless execution, clean address decoding, and Supabase ingestion.
"""

import logging
import re
import sys
from typing import List, Optional
from urllib.parse import unquote

from bs4 import BeautifulSoup
import pandas as pd
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from vivida_etl import (
    VividaIngestionPipeline,
    clean_html_source,
    fetch_event_text,
    get_headless_chrome_options,
    parse_event_dates,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("scrape_the_valley")

BASE_URL = "https://www.thevalleytacoma.com"
TARGET_URL = "https://www.thevalleytacoma.com/new-events-1"
DEFAULT_VENUE = "The Valley"
DEFAULT_ADDRESS = "1206 Puyallup Ave, Tacoma, WA 98421"


def normalize_url(raw_url: Optional[str]) -> Optional[str]:
    """Resolves protocol-relative and site-relative Squarespace URLs."""
    if not raw_url:
        return None
    url = raw_url.strip()
    if not url or url.startswith("#") or url.startswith("javascript:"):
        return None
    if url.startswith("//"):
        return f"https:{url}"
    if url.startswith("http://") or url.startswith("https://"):
        return url
    return f"{BASE_URL}{url if url.startswith('/') else '/' + url}"


def fetch_rendered_page(
    driver: webdriver.Chrome,
    url: str,
    wait_selector: Optional[str] = None,
    timeout: int = 10,
) -> Optional[BeautifulSoup]:
    """Navigates active headless driver to target URL and returns pre-treated BeautifulSoup DOM."""
    try:
        driver.get(url)
        if wait_selector:
            try:
                WebDriverWait(driver, timeout).until(
                    EC.presence_of_element_located(
                        (By.CSS_SELECTOR, wait_selector))
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


def extract_squarespace_dates(container: BeautifulSoup) -> List[str]:
    """Extracts single or multi-day [start, end] ISO dates from Squarespace <time> or meta tags."""
    time_tags = container.find_all(
        "time", class_=re.compile(r"event-date|eventlist-meta-time")
    )
    iso_dates: List[str] = []
    for tag in time_tags:
        raw_dt = tag.get("datetime") or tag.get_text(strip=True)
        if raw_dt:
            iso_dates.extend(parse_event_dates(raw_dt))

    if iso_dates:
        unique_sorted = sorted(set(iso_dates))
        # Enforce Rule 1: continuous start/end endpoints if multiple <time> tags exist
        return (
            [unique_sorted[0], unique_sorted[-1]]
            if len(unique_sorted) > 1
            else unique_sorted
        )

    meta_date = container.find("li", class_=re.compile(
        r"eventlist-meta-date|event-meta-date"))
    if meta_date:
        return parse_event_dates(meta_date.get_text(" ", strip=True))

    return []


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
            wait_selector="div.eventlist, article.eventlist-event",
        )
        if not soup:
            logger.error("Could not load main events page.")
            return pd.DataFrame()

        target_section = (
            soup.find("div", class_=re.compile(
                r"eventlist--upcoming|eventlist"))
            or soup
        )

        for element in target_section(["script", "style"]):
            element.decompose()

        event_items = target_section.find_all(
            "article", class_=re.compile(r"eventlist-event")
        )
        logger.info(f"Found {len(event_items)} Squarespace event articles.")

        # 2. Iterate event articles and step into detail pages
        for event in event_items:
            title: Optional[str] = None
            external_url: Optional[str] = None

            h1_tag = event.find(
                ["h1", "h2"], class_=re.compile(r"eventlist-title|title")
            )
            if h1_tag:
                anchor = h1_tag.find("a", href=True)
                if anchor:
                    title = anchor.get_text(strip=True)
                    external_url = normalize_url(anchor["href"])
                else:
                    title = h1_tag.get_text(strip=True)

            if not title:
                continue

            start_times = extract_squarespace_dates(event)

            # Short Description
            desc_div = event.find(
                "div", class_=re.compile(r"sqs-html-content|eventlist-excerpt")
            )
            description = (
                re.sub(r"\s+", " ", desc_div.get_text(strip=True, separator=" "))
                if desc_div
                else None
            )

            # Image URL with Squarespace CDN normalization
            img_tag = event.find("img", src=True) or event.find(
                "img", attrs={"data-src": True}
            )
            image_url = None
            if img_tag:
                image_url = normalize_url(
                    img_tag.get("data-src") or img_tag.get("src")
                )

            # Address parsing from maps link
            address = DEFAULT_ADDRESS
            addr_anchor = event.find(
                "li", class_=re.compile(r"eventlist-meta-address")
            )
            if addr_anchor and addr_anchor.find("a", href=True):
                address = parse_address_url(addr_anchor.find("a")["href"])

            big_desc = description
            ticket_link: Optional[str] = None

            # 3. Step into Detail Page for rich content using the same driver
            if external_url:
                det_soup = fetch_rendered_page(
                    driver,
                    external_url,
                    wait_selector="article.eventitem, div.sqs-block-html, main",
                )

                if det_soup:
                    if not start_times:
                        start_times = extract_squarespace_dates(det_soup)

                    container = (
                        det_soup.find(
                            "article", class_=re.compile(r"eventitem"))
                        or det_soup.find("main")
                        or det_soup
                    )

                    rich_text = fetch_event_text(container)
                    if rich_text:
                        big_desc = re.sub(r"\s+", " ", rich_text)

                    # Check for external ticket button
                    t_anchor = det_soup.find(
                        "a",
                        href=re.compile(
                            r"ticket|eventbrite|dice|seetickets|venuepilot|tixr|prekindle|buy",
                            re.IGNORECASE,
                        ),
                    )
                    if t_anchor and t_anchor.get("href"):
                        ticket_link = normalize_url(t_anchor["href"])

            if not start_times:
                logger.warning(f"[SKIP] No valid dates parsed for '{title}'.")
                continue

            events_data.append(
                {
                    "venue_name": DEFAULT_VENUE,
                    "address": address,
                    "title": title,
                    "start_time": start_times,
                    "description": description or title,
                    "big_desc": big_desc or description or title,
                    "external_url": external_url,
                    "image_url": image_url,
                    "ticket_link": ticket_link or external_url,
                }
            )

    except Exception as e:
        logger.error(f"Error during The Valley Tacoma scraping loop: {e}")
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass

    return pd.DataFrame(events_data) if events_data else pd.DataFrame()


if __name__ == "__main__":
    logger.info("Executing The Valley Tacoma scraper task...")
    raw_df = scraper()

    if not raw_df.empty:
        pipeline = VividaIngestionPipeline(table_name="events")
        success = pipeline.run_standalone_pipeline(raw_df)
        if success:
            logger.info(
                "The Valley Tacoma scraper execution completed successfully.")
        else:
            logger.error(
                "The Valley Tacoma scraper pipeline execution reported errors.")
    else:
        logger.warning("No events scraped. Exiting task.")
