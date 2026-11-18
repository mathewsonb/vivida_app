"""
doylesPublicHouse.py
Autonomous Scraper for Doyle's Public House (Tacoma, WA).
Uses HTTP-first / JSON-LD / Popmenu extraction with lazy Xvfb/Chrome hydration fallback,
concurrent HTTP detail enrichment (workers=8), and centralized parse_event_dates normalization.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
import glob
import json
import logging
import re
import shutil
import sys
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin

from bs4 import BeautifulSoup
import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from vivida_etl import (
    VividaIngestionPipeline,
    clean_html_source,
    fetch_event_text,
    parse_event_dates,
    create_stealth_driver
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(filename)s:%(lineno)d] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("scrape_doyles")

BASE_URL = "https://www.doylespublichouse.com"
TARGET_URL = "https://www.doylespublichouse.com/events"
DEFAULT_VENUE = "Doyle's Public House"
DEFAULT_ADDRESS = "208 St. Helens Ave South, Tacoma, WA 98402"

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


def build_http_session(pool_size: int = 10) -> requests.Session:
    session = requests.Session()
    session.headers.update(HEADERS)
    retries = Retry(
        total=2,
        backoff_factor=0.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    adapter = HTTPAdapter(
        max_retries=retries,
        pool_connections=pool_size,
        pool_maxsize=pool_size,
    )
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def extract_jsonld_events(soup: BeautifulSoup) -> List[Dict[str, Any]]:
    """Extracts schema.org Event objects if embedded by Popmenu."""
    records: List[Dict[str, Any]] = []
    for script in soup.find_all("script", type="application/ld+json"):
        raw = script.string or script.get_text()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except Exception:
            continue

        items = data if isinstance(data, list) else data.get("@graph", [data])
        for item in items:
            if not isinstance(item, dict) or "Event" not in str(item.get("@type") or ""):
                continue
            title = normalize_ws(str(item.get("name") or ""))
            raw_start = str(item.get("startDate") or "")
            if not title or not raw_start:
                continue
            dates = parse_event_dates(raw_start[:10])
            if not dates:
                continue
            desc = normalize_ws(str(item.get("description") or ""))
            url = resolve_url(item.get("url")) or TARGET_URL
            img = item.get("image")
            if isinstance(img, list):
                img = img[0] if img else None
            elif isinstance(img, dict):
                img = img.get("url")
            offers = item.get("offers")
            tix = None
            if isinstance(offers, dict):
                tix = resolve_url(offers.get("url"))
            elif isinstance(offers, list) and offers and isinstance(offers[0], dict):
                tix = resolve_url(offers[0].get("url"))

            records.append(
                {
                    "venue_name": DEFAULT_VENUE,
                    "address": DEFAULT_ADDRESS,
                    "title": title,
                    "start_time": dates,
                    "description": desc[:250] if desc else title,
                    "big_desc": desc or title,
                    "external_url": url,
                    "image_url": resolve_url(img),
                    "ticket_link": tix or url,
                }
            )
    return records


def extract_popmenu_cards(soup: BeautifulSoup) -> List[Dict[str, Any]]:
    """Extracts Popmenu event cards from rendered or static HTML."""
    for element in soup(["script", "style"]):
        element.decompose()

    event_items = soup.find_all(
        "div", class_=re.compile(r"pm-calendar-event-card-wrapper", re.I)
    )
    if not event_items:
        anchors = soup.find_all("a", href=re.compile(r"/events/[^/?#]+", re.I))
        seen_parents = set()
        for a in anchors:
            parent = a.find_parent(["div", "article", "li"]) or a
            if id(parent) not in seen_parents:
                seen_parents.add(id(parent))
                event_items.append(parent)

    records: List[Dict[str, Any]] = []
    for event in event_items:
        anchor = event.find("a", href=True) if event.name != "a" else event
        heading = event.find(
            ["h1", "h2", "h3", "h4", "h5", "div", "span"],
            class_=re.compile(r"title|header|name", re.I),
        )

        title = ""
        if heading and heading.get_text(strip=True):
            title = normalize_ws(heading.get_text(" ", strip=True))
        elif anchor and anchor.get_text(strip=True):
            title = normalize_ws(anchor.get_text(" ", strip=True))

        if not title or len(title) < 3:
            continue

        external_url = resolve_url(anchor.get(
            "href")) if anchor and anchor.get("href") else TARGET_URL

        start_dates: List[str] = []
        month_tag = event.find(
            ["span", "div"], class_=re.compile(r"month", re.I))
        day_tag = event.find(
            ["span", "div"], class_=re.compile(r"date|day", re.I))
        if month_tag and day_tag:
            month_str = normalize_ws(month_tag.get_text(" ", strip=True))
            day_str = normalize_ws(day_tag.get_text(" ", strip=True))
            start_dates = parse_event_dates(f"{month_str} {day_str}")
        if not start_dates:
            time_el = event.find(["time", "div", "span"],
                                 class_=re.compile(r"date|time|when", re.I))
            if time_el:
                raw_dt = time_el.get(
                    "datetime") or time_el.get_text(" ", strip=True)
                start_dates = parse_event_dates(normalize_ws(raw_dt))

        desc_div = event.find(
            ["div", "p", "span"], class_=re.compile(r"description|content|summary", re.I)
        )
        card_desc = (
            normalize_ws(desc_div.get_text(" ", strip=True)
                         ) if desc_div else None
        )

        img_tag = event.find("img")
        image_url = (
            resolve_url(img_tag.get("data-src") or img_tag.get("src"))
            if img_tag
            else None
        )

        records.append(
            {
                "venue_name": DEFAULT_VENUE,
                "address": DEFAULT_ADDRESS,
                "title": title,
                "start_time": start_dates,
                "description": card_desc or title,
                "big_desc": card_desc or title,
                "external_url": external_url,
                "image_url": image_url,
                "ticket_link": external_url,
            }
        )
    return records


def enrich_detail_via_http(
    session: requests.Session, item: Dict[str, Any]
) -> Dict[str, Any]:
    detail_url = item.get("external_url")
    if not detail_url or detail_url.rstrip("/") == TARGET_URL.rstrip("/"):
        return item

    try:
        resp = session.get(detail_url, timeout=12)
        resp.raise_for_status()
        if len(resp.text) < 500:
            return item

        det_soup = BeautifulSoup(clean_html_source(resp.text), "html.parser")
        main_el = det_soup.find("main") or det_soup

        if not item.get("start_time"):
            date_el = main_el.find(
                ["time", "span", "div"], class_=re.compile(r"date|when", re.I))
            if date_el:
                raw_dt = date_el.get(
                    "datetime") or date_el.get_text(" ", strip=True)
                item["start_time"] = parse_event_dates(normalize_ws(raw_dt))

        t_btn = main_el.find(
            "a",
            href=re.compile(
                r"ticket|rsvp|register|eventbrite|brownpapertickets|seetickets", re.I),
        )
        if t_btn and t_btn.get("href"):
            item["ticket_link"] = resolve_url(t_btn["href"]) or detail_url

        if not item.get("image_url"):
            og_img = det_soup.find("meta", property="og:image")
            if og_img and og_img.get("content"):
                item["image_url"] = resolve_url(og_img["content"])

        rich_text = fetch_event_text(main_el)
        if rich_text:
            clean_rich = normalize_ws(rich_text)
            if len(clean_rich) > len(item.get("big_desc") or ""):
                item["big_desc"] = clean_rich
                if item.get("description") == item["title"]:
                    item["description"] = clean_rich[:250]
    except Exception:
        pass
    return item


def consolidate_records(records: List[Dict[str, Any]]) -> pd.DataFrame:
    """Consolidates recurring pub nights by (external_url or title) into sorted start_time arrays."""
    grouped: Dict[str, Dict[str, Any]] = {}
    for rec in records:
        title = normalize_ws(rec.get("title"))
        dates = rec.get("start_time") or []
        if not title or not dates:
            continue

        ext_url = rec.get("external_url") or TARGET_URL
        key = (
            ext_url.lower()
            if ext_url.rstrip("/") != TARGET_URL.rstrip("/")
            else title.lower()
        )
        if key in grouped:
            grouped[key]["dates_set"].update(dates)
            if len(rec.get("big_desc") or "") > len(grouped[key].get("big_desc") or ""):
                grouped[key]["big_desc"] = rec["big_desc"]
                grouped[key]["description"] = rec["description"]
            continue

        entry = dict(rec)
        entry["title"] = title
        entry["dates_set"] = set(dates)
        grouped[key] = entry

    final_rows: List[Dict[str, Any]] = []
    for entry in grouped.values():
        entry["start_time"] = sorted(entry.pop("dates_set"))
        final_rows.append(entry)

    return pd.DataFrame(final_rows) if final_rows else pd.DataFrame()


def scraper() -> pd.DataFrame:
    """Scrapes Doyle's Public House events via HTTP-first + Xvfb/Chrome hydration fallback."""
    logger.info(f"[START] Scraping Doyle's Public House: {TARGET_URL}")
    session = build_http_session(pool_size=10)
    staged_items: List[Dict[str, Any]] = []

    # 1. HTTP-first fetch
    try:
        resp = session.get(TARGET_URL, timeout=15)
        resp.raise_for_status()
        soup_http = BeautifulSoup(clean_html_source(resp.text), "html.parser")
        jsonld_items = extract_jsonld_events(soup_http)
        dom_items = extract_popmenu_cards(soup_http)
        staged_items = jsonld_items if len(
            jsonld_items) >= len(dom_items) else dom_items
        if staged_items:
            logger.info(
                f"[HTTP FAST-PATH] Extracted {len(staged_items)} Doyle's event items via HTTP."
            )
    except Exception as http_err:
        logger.warning(
            f"[HTTP LISTING] Failed ({http_err}); falling back to Xvfb + Chrome...")

    # 2. Xvfb + Chrome hydration fallback for JS-rendered Popmenu widgets
    if not staged_items:
        display = None
        driver = None
        try:
            from pyvirtualdisplay import Display
            from selenium.webdriver.common.by import By
            from selenium.webdriver.support import expected_conditions as EC
            from selenium.webdriver.support.ui import WebDriverWait

            display = Display(visible=0, size=(1920, 1080), backend="xvfb")
            display.start()
            driver = create_stealth_driver()

            driver.get(TARGET_URL)
            try:
                WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located(
                        (By.CSS_SELECTOR,
                         ".pm-calendar-event-card-wrapper, a[href*='/events/']")
                    )
                )
            except Exception:
                pass
            time.sleep(2.5)
            driver.execute_script(
                "window.scrollTo(0, document.body.scrollHeight / 2);")
            time.sleep(1.5)
            driver.execute_script(
                "window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(1.5)

            soup_browser = BeautifulSoup(
                clean_html_source(driver.page_source), "html.parser")
            jsonld_items = extract_jsonld_events(soup_browser)
            dom_items = extract_popmenu_cards(soup_browser)
            staged_items = (
                jsonld_items if len(jsonld_items) >= len(
                    dom_items) else dom_items
            )
            logger.info(
                f"[CHROME HYDRATION] Extracted {len(staged_items)} Doyle's event items."
            )
        except Exception as e:
            logger.error(
                f"[FATAL ERROR] Chrome hydration failed: {e}", exc_info=True)
        finally:
            if driver:
                try:
                    driver.quit()
                except Exception:
                    pass
            if display:
                try:
                    display.stop()
                except Exception:
                    pass

    if not staged_items:
        logger.warning(
            "[PARSER WARNING] No Doyle's Public House events found.")
        return pd.DataFrame()

    # 3. Concurrent HTTP detail enrichment for unique detail URLs
    unique_urls = {
        it["external_url"]
        for it in staged_items
        if it.get("external_url") and it["external_url"].rstrip("/") != TARGET_URL.rstrip("/")
    }
    if unique_urls:
        logger.info(
            f"Batch-fetching {len(unique_urls)} unique Doyle's detail pages via HTTP (workers=8)..."
        )
        enriched: List[Dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = [
                executor.submit(enrich_detail_via_http, session, item)
                for item in staged_items
            ]
            for fut in as_completed(futures):
                enriched.append(fut.result())
        staged_items = enriched

    return consolidate_records(staged_items)


if __name__ == "__main__":
    logger.info("Executing Doyle's Public House scraper task...")
    raw_df = scraper()

    if not raw_df.empty:
        pipeline = VividaIngestionPipeline(table_name="events")
        success = pipeline.run_standalone_pipeline(raw_df)
        if success:
            logger.info(
                "[PIPELINE SUCCESS] Doyle's Public House scraper execution completed successfully.")
        else:
            logger.error(
                "[PIPELINE ERROR] Doyle's Public House scraper pipeline execution reported errors.")
    else:
        logger.warning("[EXIT] No events scraped. Exiting task.")
