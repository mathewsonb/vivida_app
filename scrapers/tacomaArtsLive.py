"""
tacomaArtsLive.py
Autonomous Production Scraper for Tacoma Arts Live (Tacoma, WA - Modern Events Calendar).
Uses HTTP-first listing & concurrent detail-page fetching (workers=10),
consolidates recurring MEC grid cards into discrete date arrays, and routes
to VividaIngestionPipeline.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
import glob
import logging
import re
import shutil
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

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
logger = logging.getLogger("scrape_tacoma_arts_live")

BASE_URL = "https://www.tacomaartslive.org"
TARGET_URL = "https://www.tacomaartslive.org/upcoming-events/"
DEFAULT_VENUE = "Tacoma Arts Live"
DEFAULT_ADDRESS = "901 Broadway, Tacoma, WA 98402"

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
    if not clean or clean.startswith("#") or clean.lower().startswith("javascript:"):
        return None
    if clean.startswith("//"):
        return f"https:{clean}"
    if clean.startswith("http://") or clean.startswith("https://"):
        return clean
    return f"{BASE_URL}{clean if clean.startswith('/') else '/' + clean}"


def build_http_session(pool_size: int = 12) -> requests.Session:
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


def scroll_page_to_bottom(driver, max_scrolls: int = 10, pause_time: float = 2.0) -> None:
    """Iteratively scrolls and clicks MEC Load More buttons to unfold all upcoming events."""
    logger.info(
        "[SELENIUM SCROLL] Executing dynamic scroll / load-more loop...")
    last_height = driver.execute_script("return document.body.scrollHeight")

    for _ in range(max_scrolls):
        driver.execute_script(
            "window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(pause_time)

        clicked = False
        try:
            load_more_btns = driver.find_elements(
                "css selector",
                "a.mec-load-more-button, .mec-load-more-wrap a, button.mec-load-more-button",
            )
            for btn in load_more_btns:
                if btn.is_displayed():
                    driver.execute_script("arguments[0].click();", btn)
                    clicked = True
                    time.sleep(pause_time)
                    break
        except Exception:
            pass

        new_height = driver.execute_script("return document.body.scrollHeight")
        if new_height == last_height and not clicked:
            break
        last_height = new_height


def extract_mec_dates(container: BeautifulSoup) -> List[str]:
    """Extracts single-day or multi-day [start, end] ISO dates from Modern Events Calendar blocks."""
    if not container:
        return []

    start_span = container.find(
        "span", class_=re.compile(r"mec-start-date-label", re.I))
    end_span = container.find(
        "span", class_=re.compile(r"mec-end-date-label", re.I))

    if start_span and end_span:
        s_txt = normalize_ws(start_span.get_text(" ", strip=True))
        e_txt = normalize_ws(end_span.get_text(" ", strip=True)).lstrip("-–— ")
        if s_txt and e_txt and e_txt.lower() != s_txt.lower():
            dates = parse_event_dates(f"{s_txt} - {e_txt}")
            if dates:
                return dates

    if start_span:
        dates = parse_event_dates(normalize_ws(
            start_span.get_text(" ", strip=True)))
        if dates:
            return dates

    date_el = container.find(["span", "div"], class_=re.compile(
        r"mec-event-date|mec-date", re.I))
    if date_el:
        dates = parse_event_dates(normalize_ws(
            date_el.get_text(" ", strip=True)))
        if dates:
            return dates

    time_tag = container.find("time")
    if time_tag:
        raw_dt = time_tag.get("datetime") or time_tag.get_text(" ", strip=True)
        return parse_event_dates(normalize_ws(raw_dt))

    return []


def parse_detail_html(
    html: str,
    current_dates: List[str],
    card_title: str,
    detail_url: str,
) -> Tuple[List[str], str, str, Optional[str]]:
    """Extracts fallback dates, Elementor rich text, summary description, and Tessitura/ticket link."""
    det_soup = BeautifulSoup(clean_html_source(html), "html.parser")
    if not det_soup:
        return current_dates, card_title, card_title, detail_url

    dates = current_dates or extract_mec_dates(det_soup)

    det_container = (
        det_soup.find("div", class_=re.compile(
            r"elementor-widget-text-editor", re.I))
        or det_soup.find("div", class_="elementor-widget-container")
        or det_soup.find("div", class_=re.compile(r"entry-content|mec-single-event-description", re.I))
        or det_soup.find("main")
        or det_soup
    )

    rich_text = fetch_event_text(det_container)
    big_desc = normalize_ws(rich_text) if rich_text else card_title

    p_tags = det_container.find_all("p")
    p_text_blocks = [
        normalize_ws(p.get_text(" ", strip=True))
        for p in p_tags
        if normalize_ws(p.get_text(" ", strip=True))
    ]
    if p_text_blocks:
        description = " ".join(p_text_blocks[:2])
    else:
        description = big_desc[:250] if big_desc else card_title

    ticket_link = detail_url
    t_anchor = det_soup.find(
        "a",
        href=re.compile(r"ticket|buy|tessitura|ordertickets", re.IGNORECASE),
    )
    if t_anchor and t_anchor.get("href"):
        ticket_link = resolve_url(t_anchor["href"]) or detail_url

    return dates, description, big_desc, ticket_link


def fetch_detail_via_http(
    session: requests.Session, item: Dict[str, Any]
) -> Tuple[Dict[str, Any], bool]:
    detail_url = item.get("external_url")
    if not detail_url:
        return item, True

    try:
        resp = session.get(detail_url, timeout=15)
        resp.raise_for_status()
        if len(resp.text) < 500:
            return item, False

        dates, description, big_desc, ticket_link = parse_detail_html(
            resp.text,
            item["start_time"],
            item["title"],
            detail_url,
        )
        item["start_time"] = dates
        item["description"] = description
        item["big_desc"] = big_desc
        item["ticket_link"] = ticket_link
        return item, True
    except Exception:
        return item, False


def scraper() -> pd.DataFrame:
    """Scrapes Tacoma Arts Live events via HTTP-first concurrency with lazy single-browser fallback."""
    logger.info(f"[START] Scraping Tacoma Arts Live: {TARGET_URL}")
    session = build_http_session(pool_size=12)

    display = None
    driver = None
    main_html: Optional[str] = None

    def ensure_browser():
        nonlocal display, driver
        if driver is None:
            from pyvirtualdisplay import Display

            display = Display(visible=0, size=(1920, 1080), backend="xvfb")
            display.start()
            driver = create_stealth_driver()
        return driver

    try:
        # 1. HTTP-first listing check (falls back to Chrome if WAF blocks or MEC Load More exists)
        try:
            resp = session.get(TARGET_URL, timeout=15)
            resp.raise_for_status()
            soup_check = BeautifulSoup(resp.text, "html.parser")
            cards = soup_check.select(
                "article.mec-event-article") or soup_check.select("div.col-md-4")
            load_more = soup_check.select_one(
                "a.mec-load-more-button, .mec-load-more-wrap a, button.mec-load-more-button"
            )
            if cards and not load_more:
                main_html = resp.text
                logger.info(
                    f"[HTTP FAST-PATH] Retrieved all {len(cards)} Tacoma Arts Live cards directly via HTTP."
                )
            elif cards:
                logger.info(
                    f"[LISTING] Initial HTTP fetch found {len(cards)} cards with MEC Load More button; "
                    "launching Xvfb + Chrome to unfold full listing..."
                )
        except Exception as http_err:
            logger.warning(
                f"[LISTING] HTTP listing fetch failed ({http_err}); launching Xvfb + Chrome fallback..."
            )

        if not main_html:
            drv = ensure_browser()
            drv.get(TARGET_URL)
            time.sleep(6)
            scroll_page_to_bottom(drv)
            main_html = drv.page_source

            ua = drv.execute_script("return navigator.userAgent;")
            if ua:
                session.headers.update({"User-Agent": ua})
            for cookie in drv.get_cookies():
                session.cookies.set(
                    cookie["name"],
                    cookie["value"],
                    domain=cookie.get("domain"),
                    path=cookie.get("path", "/"),
                )

        soup = BeautifulSoup(main_html, "html.parser")
        event_cards = soup.select("article.mec-event-article")
        if not event_cards:
            event_cards = soup.select("div.col-md-4")

        if not event_cards:
            logger.warning(
                "[PARSER WARNING] Found 0 event cards matching selector grid.")
            return pd.DataFrame()

        logger.info(
            f"[PARSER SUCCESS] Found {len(event_cards)} candidate MEC event cards.")

        # Consolidate recurring MEC cards (same detail_url/title) into discrete date arrays
        grouped_items: Dict[str, Dict[str, Any]] = {}

        for idx, card in enumerate(event_cards, start=1):
            try:
                title_el = card.find("h4", class_=re.compile(
                    r"mec-event-title|title", re.I)) or card.find(["h4", "h3"])
                link_tag = title_el.find(
                    "a", href=True) if title_el else card.find("a", href=True)

                card_title = normalize_ws(link_tag.get_text(
                    " ", strip=True)) if link_tag else ""
                if not card_title and title_el:
                    card_title = normalize_ws(
                        title_el.get_text(" ", strip=True))
                if not card_title:
                    continue

                detail_url = resolve_url(
                    link_tag.get("href")) if link_tag else None
                start_time_list = extract_mec_dates(card)

                group_key = detail_url or card_title.lower()
                if group_key in grouped_items:
                    grouped_items[group_key]["dates_set"].update(
                        start_time_list)
                    continue

                loc_p = card.find("p", class_=re.compile(
                    r"mec-grid-event-location|location", re.I))
                venue_name = DEFAULT_VENUE
                address = DEFAULT_ADDRESS
                if loc_p:
                    loc_parts = [
                        normalize_ws(p)
                        for p in loc_p.get_text(" ", strip=True).split(",")
                        if normalize_ws(p)
                    ]
                    if len(loc_parts) >= 1:
                        venue_name = loc_parts[0]
                    if len(loc_parts) >= 2:
                        address = ", ".join(loc_parts[1:])

                img_tag = card.find("img", src=True) or card.find(
                    "img", attrs={"data-src": True})
                image_url = (
                    resolve_url(img_tag.get("data-src") or img_tag.get("src"))
                    if img_tag
                    else None
                )

                grouped_items[group_key] = {
                    "venue_name": venue_name,
                    "address": address,
                    "title": card_title,
                    "dates_set": set(start_time_list),
                    "description": card_title,
                    "big_desc": card_title,
                    "external_url": detail_url,
                    "image_url": image_url,
                    "ticket_link": detail_url,
                }
            except Exception as item_err:
                logger.error(
                    f"[ITEM ERROR] Failed parsing event #{idx}: {item_err}", exc_info=True)

        if not grouped_items:
            return pd.DataFrame()

        staged_items: List[Dict[str, Any]] = []
        for entry in grouped_items.values():
            entry["start_time"] = sorted(entry.pop("dates_set"))
            staged_items.append(entry)

        logger.info(
            f"[CONSOLIDATED] Grouped {len(event_cards)} MEC cards into {len(staged_items)} unique events. "
            f"Batch-fetching detail pages via HTTP (workers=10)..."
        )

        # 2. Batch-fetch detail pages concurrently via HTTP (workers=10)
        enriched_items: List[Dict[str, Any]] = []
        fallback_queue: List[Dict[str, Any]] = []

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [
                executor.submit(fetch_detail_via_http, session, item)
                for item in staged_items
            ]
            for fut in as_completed(futures):
                updated_item, ok = fut.result()
                if ok:
                    enriched_items.append(updated_item)
                else:
                    fallback_queue.append(updated_item)

        logger.info(
            f"HTTP detail batch complete: {len(enriched_items)} parsed, "
            f"{len(fallback_queue)} queued for Chrome fallback."
        )

        # 3. Single-browser fallback ONLY for detail pages that failed HTTP
        if fallback_queue:
            drv = ensure_browser()
            for item in fallback_queue:
                detail_url = item.get("external_url")
                if detail_url:
                    try:
                        drv.get(detail_url)
                        time.sleep(2.5)
                        dates, description, big_desc, ticket_link = parse_detail_html(
                            drv.page_source,
                            item["start_time"],
                            item["title"],
                            detail_url,
                        )
                        item["start_time"] = dates
                        item["description"] = description
                        item["big_desc"] = big_desc
                        item["ticket_link"] = ticket_link
                    except Exception as fb_err:
                        logger.warning(
                            f"[FALLBACK WARN] Failed {detail_url}: {fb_err}")
                enriched_items.append(item)

        raw_records = []
        for item in enriched_items:
            if not item.get("start_time"):
                logger.warning(
                    f"[SKIP] No valid dates parsed for '{item.get('title')}'.")
                continue
            raw_records.append(item)

        return pd.DataFrame(raw_records) if raw_records else pd.DataFrame()

    except Exception as e:
        logger.error(
            f"[FATAL ERROR] Exception encountered during scraping loop: {e}", exc_info=True)
        return pd.DataFrame()
    finally:
        if driver:
            logger.info("[CLEANUP] Closing Chrome driver instance...")
            try:
                driver.quit()
            except Exception:
                pass
        if display:
            logger.info("[CLEANUP] Stopping PyVirtualDisplay server...")
            try:
                display.stop()
            except Exception:
                pass


if __name__ == "__main__":
    logger.info("Executing Tacoma Arts Live scraper task...")
    raw_df = scraper()

    if not raw_df.empty:
        pipeline = VividaIngestionPipeline(table_name="events")
        success = pipeline.run_standalone_pipeline(raw_df)
        if success:
            logger.info(
                "[PIPELINE SUCCESS] Tacoma Arts Live scraper completed successfully.")
        else:
            logger.error(
                "[PIPELINE ERROR] Tacoma Arts Live scraper pipeline execution reported errors.")
    else:
        logger.warning("[EXIT] No events scraped. Pipeline handoff skipped.")
