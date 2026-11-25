"""
ciderAndCedar.py
Autonomous Scraper for Cider & Cedar (Tacoma, WA).
Uses HTTP-first Squarespace list parsing with lazy cross-platform browser hydration fallback
and centralized parse_event_dates normalization.
"""

import logging
import re
import sys
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup
import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from vivida_etl import (
    VividaIngestionPipeline,
    clean_html_source,
    create_stealth_driver,
    parse_event_dates,
    virtual_display,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(filename)s:%(lineno)d] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("scrape_cider_and_cedar")

BASE_URL = "https://www.ciderandcedar.com"
TARGET_URL = "https://www.ciderandcedar.com/events"
DEFAULT_VENUE = "Cider & Cedar"
DEFAULT_ADDRESS = "744 Market St UNIT 102B, Tacoma, WA 98402"
DEFAULT_IMAGE_URL = (
    "https://images.squarespace-cdn.com/content/v1/679bb91621194f0e31b3c085/"
    "54ecdaa4-d251-44d5-b175-1b48f87aae9d/peacesignc%26c.png?format=1500w"
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def normalize_ws(text: Optional[str]) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("\xa0", " ")).strip()


def resolve_url(raw_url: Optional[str]) -> Optional[str]:
    if not raw_url:
        return None
    clean = str(raw_url).strip()
    if (
        not clean
        or clean.startswith("#")
        or clean.lower().startswith("javascript:")
        or clean.lower().startswith("data:")
    ):
        return None
    return urljoin(BASE_URL, clean)


def build_http_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(HEADERS)
    retries = Retry(
        total=2,
        backoff_factor=0.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    adapter = HTTPAdapter(max_retries=retries)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def parse_squarespace_items(soup: BeautifulSoup) -> List[Dict[str, Any]]:
    """Extracts Cider & Cedar event cards from Squarespace user-items-list markup."""
    target_section = soup.find(
        "div", class_=re.compile(r"user-items-list", re.I)
    )
    if not target_section:
        return []

    for script in target_section(["script", "style"]):
        script.decompose()

    event_items = target_section.find_all(
        "li", class_=re.compile(r"list-item|user-items-list", re.I)
    ) or target_section.find_all("li")

    logger.info(f"[PARSER] Found {len(event_items)} Squarespace list items.")

    grouped: Dict[str, Dict[str, Any]] = {}
    for event in event_items:
        heading = event.find(["h2", "h3", "h4"])
        title = normalize_ws(heading.get_text(" ", strip=True)) if heading else ""
        if not title:
            continue

        # Image extraction (Squarespace uses data-src / data-image for lazy CDN assets)
        image_url = DEFAULT_IMAGE_URL
        img_tag = event.find("img")
        if img_tag:
            raw_src = (
                img_tag.get("data-src")
                or img_tag.get("data-image")
                or img_tag.get("src")
            )
            resolved_img = resolve_url(raw_src)
            if resolved_img:
                image_url = resolved_img

        # Optional CTA button / link on the card
        cta_anchor = event.find("a", href=True)
        cta_url = resolve_url(cta_anchor["href"]) if cta_anchor else None
        external_url = cta_url or TARGET_URL
        ticket_link = cta_url or TARGET_URL

        # Parse Date & Description paragraphs
        paragraphs = [
            normalize_ws(p.get_text(" ", strip=True))
            for p in event.find_all("p")
            if normalize_ws(p.get_text(" ", strip=True))
        ]

        start_dates: List[str] = []
        desc_parts: List[str] = []
        for p_text in paragraphs:
            if not start_dates:
                parsed = parse_event_dates(p_text)
                if parsed:
                    start_dates = parsed
                    continue
            desc_parts.append(p_text)

        if not start_dates:
            logger.warning(f"[SKIP] No valid date parsed for '{title}'.")
            continue

        combined_desc = " ".join(desc_parts).strip() or title
        key = title.lower()

        if key in grouped:
            grouped[key]["dates_set"].update(start_dates)
            if len(combined_desc) > len(grouped[key]["big_desc"]):
                grouped[key]["description"] = combined_desc[:250]
                grouped[key]["big_desc"] = combined_desc
            continue

        grouped[key] = {
            "venue_name": DEFAULT_VENUE,
            "address": DEFAULT_ADDRESS,
            "title": title,
            "dates_set": set(start_dates),
            "description": combined_desc[:250],
            "big_desc": combined_desc,
            "external_url": external_url,
            "image_url": image_url,
            "ticket_link": ticket_link,
        }

    records: List[Dict[str, Any]] = []
    for entry in grouped.values():
        entry["start_time"] = sorted(entry.pop("dates_set"))
        records.append(entry)

    return records


def scraper() -> pd.DataFrame:
    """Scrapes Cider & Cedar events via HTTP-first Squarespace fetch + browser fallback."""
    logger.info(f"[START] Scraping Cider & Cedar: {TARGET_URL}")
    session = build_http_session()

    # 1. HTTP-first fetch
    try:
        resp = session.get(TARGET_URL, timeout=15)
        resp.raise_for_status()
        soup_http = BeautifulSoup(clean_html_source(resp.text), "html.parser")
        records = parse_squarespace_items(soup_http)
        if records:
            logger.info(
                f"[HTTP FAST-PATH] Extracted {len(records)} Cider & Cedar events via HTTP."
            )
            return pd.DataFrame(records)
        logger.info(
            "[HTTP LISTING] 0 items parsed statically; launching browser fallback..."
        )
    except Exception as http_err:
        logger.warning(
            f"[HTTP LISTING] Failed ({http_err}); launching browser fallback..."
        )

    # 2. Cross-platform virtual display + Chrome hydration fallback
    driver = None
    try:
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait

        with virtual_display(size=(1920, 1080)) as disp:
            driver = create_stealth_driver(headless=(disp is None))
            driver.get(TARGET_URL)

            try:
                WebDriverWait(driver, 12).until(
                    EC.presence_of_element_located(
                        (
                            By.CSS_SELECTOR,
                            "div.user-items-list, ul.user-items-list-item-container",
                        )
                    )
                )
            except Exception:
                logger.warning(
                    "Timed out waiting for Squarespace list container selector."
                )
            time.sleep(1.5)

            soup_browser = BeautifulSoup(
                clean_html_source(driver.page_source), "html.parser"
            )
            records = parse_squarespace_items(soup_browser)
            return pd.DataFrame(records) if records else pd.DataFrame()

    except Exception as e:
        logger.error(
            f"[FATAL ERROR] Headless browser execution failed for {TARGET_URL}: {e}",
            exc_info=True,
        )
        return pd.DataFrame()
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass


if __name__ == "__main__":
    logger.info("Executing Cider & Cedar scraper task...")
    raw_df = scraper()

    if not raw_df.empty:
        pipeline = VividaIngestionPipeline(table_name="events")
        success = pipeline.run_standalone_pipeline(raw_df)
        if success:
            logger.info(
                "[PIPELINE SUCCESS] Cider & Cedar scraper execution completed successfully."
            )
        else:
            logger.error(
                "[PIPELINE ERROR] Cider & Cedar scraper pipeline execution reported errors."
            )
    else:
        logger.warning("[EXIT] No events scraped. Exiting task.")