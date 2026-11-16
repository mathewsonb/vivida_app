"""
tacomaDome.py
Autonomous Production Scraper for Tacoma Dome (Tacoma, WA).
Uses HTTP-first listing & concurrent detail-page fetching (workers=10),
with lazy single-browser Xvfb/Chrome fallback and centralized parse_event_dates normalization.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
import glob
import logging
import re
import shutil
import sys
import time
from typing import Any, Dict, List, Optional, Set, Tuple

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
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(filename)s:%(lineno)d] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("scrape_tacoma_dome")

BASE_URL = "https://www.tacomadome.org"
TARGET_URL = "https://www.tacomadome.org/events"
DEFAULT_VENUE = "Tacoma Dome"
DEFAULT_ADDRESS = "2727 E D St, Tacoma, WA 98402"

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


def extract_css_bg_image(style_attr: Optional[str]) -> Optional[str]:
    """Safely extracts background image URL from inline CSS style attributes."""
    if not style_attr:
        return None
    match = re.search(
        r'url\((?:["\']?)(.*?)(?:["\']?)\)', style_attr, re.IGNORECASE)
    if match:
        return resolve_url(match.group(1))
    return None


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


def create_stealth_driver():
    """Lazy-instantiates undetected Chrome driver configured for Xvfb display rendering."""
    import undetected_chromedriver as uc

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
    if chrome_bin:
        options.binary_location = chrome_bin

    return uc.Chrome(options=options, version_main=None)


def parse_detail_html(
    html: str,
    current_dates: List[str],
    current_desc: str,
    current_ticket: Optional[str],
    detail_url: str,
) -> Tuple[List[str], str, Optional[str]]:
    """Extracts fallback dates, rich description text, and ticket link from detail HTML."""
    det_soup = BeautifulSoup(clean_html_source(html), "html.parser")
    if not det_soup:
        return current_dates, current_desc, current_ticket

    dates = current_dates
    if not dates:
        det_date = det_soup.select_one("div.date") or det_soup.find("time")
        if det_date:
            raw_dt = det_date.get(
                "aria-label") or det_date.get_text(" ", strip=True)
            dates = parse_event_dates(normalize_ws(raw_dt))

    container = (
        det_soup.select_one("div.leftColumn")
        or det_soup.select_one("div.event-description")
        or det_soup.find("main")
        or det_soup
    )
    rich_text = fetch_event_text(container)
    big_desc = normalize_ws(rich_text) if rich_text else current_desc

    ticket_link = current_ticket
    if not ticket_link or ticket_link == detail_url:
        t_btn = det_soup.select_one("a.tickets, a.onsalenow") or det_soup.find(
            "a",
            href=re.compile(
                r"ticket|ticketmaster|axs|buy|bit\.ly", re.IGNORECASE),
        )
        if t_btn and t_btn.get("href"):
            ticket_link = resolve_url(t_btn["href"]) or ticket_link

    return dates, big_desc, ticket_link


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

        dates, big_desc, ticket_link = parse_detail_html(
            resp.text,
            item["start_time"],
            item["big_desc"] or item["description"] or "",
            item.get("ticket_link"),
            detail_url,
        )
        item["start_time"] = dates
        item["big_desc"] = big_desc
        item["ticket_link"] = ticket_link
        return item, True
    except Exception:
        return item, False


def scraper() -> pd.DataFrame:
    """Scrapes Tacoma Dome events via HTTP-first concurrency with single-browser fallback."""
    logger.info(f"[START] Scraping Tacoma Dome: {TARGET_URL}")
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
        # 1. HTTP-first listing check (falls back to Chrome if WAF blocks or Load More pagination exists)
        try:
            resp = session.get(TARGET_URL, timeout=15)
            resp.raise_for_status()
            soup_check = BeautifulSoup(resp.text, "html.parser")
            cards = soup_check.find_all(
                "div", class_=re.compile(r"^eventItem.*clearfix$"))
            load_btn = soup_check.select_one(
                "#loadMoreEvents, a.loadMore, button.loadMore, a.btn-load-more"
            )
            if cards and not load_btn:
                main_html = resp.text
                logger.info(
                    f"[HTTP FAST-PATH] Retrieved all {len(cards)} Tacoma Dome event blocks directly via HTTP."
                )
            elif cards:
                logger.info(
                    f"[LISTING] Initial HTTP fetch found {len(cards)} cards with Load More pagination; "
                    "launching Xvfb + Chrome to unfold full listing..."
                )
        except Exception as http_err:
            logger.warning(
                f"[LISTING] HTTP listing fetch failed ({http_err}); launching Xvfb + Chrome fallback..."
            )

        if not main_html:
            from selenium.webdriver.common.by import By

            drv = ensure_browser()
            drv.get(TARGET_URL)
            time.sleep(5)

            load_clicks = 0
            while True:
                try:
                    buttons = drv.find_elements(By.ID, "loadMoreEvents")
                    if not buttons:
                        buttons = drv.find_elements(
                            By.CSS_SELECTOR, "a.loadMore, button.loadMore, a.btn-load-more"
                        )
                    if buttons and buttons[0].is_displayed() and buttons[0].is_enabled():
                        btn = buttons[0]
                        drv.execute_script(
                            "arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});",
                            btn,
                        )
                        time.sleep(0.5)
                        drv.execute_script("arguments[0].click();", btn)
                        load_clicks += 1
                        logger.info(
                            f"[PAGINATION] Clicked 'Load More' (click #{load_clicks})...")
                        time.sleep(2.5)
                    else:
                        if load_clicks:
                            logger.info(
                                f"[PAGINATION COMPLETE] All events unfolded ({load_clicks} clicks)."
                            )
                        break
                except Exception as nav_err:
                    logger.info(
                        f"[PAGINATION END] Stopped after {load_clicks} clicks: {nav_err}")
                    break

            drv.execute_script(
                "window.scrollTo(0, document.body.scrollHeight / 2);")
            time.sleep(1)
            main_html = drv.page_source

        soup = BeautifulSoup(clean_html_source(main_html), "html.parser")
        target_section = (
            soup.find("div", class_=re.compile(r"full_column|non-widget-area"))
            or soup
        )
        for element in target_section(["script", "style"]):
            element.decompose()

        event_items = target_section.find_all(
            "div", class_=re.compile(r"^eventItem.*clearfix$"))
        logger.info(
            f"[PARSER SUCCESS] Found {len(event_items)} Tacoma Dome event blocks.")

        staged_items: List[Dict[str, Any]] = []
        seen_urls: Set[str] = set()

        for idx, event in enumerate(event_items, start=1):
            try:
                h3_tag = event.find("h3")
                card_title = normalize_ws(h3_tag.get_text(
                    " ", strip=True)) if h3_tag else ""
                if not card_title:
                    continue

                anchor = event.find("a", href=True)
                detail_url = resolve_url(anchor["href"]) if anchor else None

                dedup_key = detail_url or card_title.lower()
                if dedup_key in seen_urls:
                    continue
                seen_urls.add(dedup_key)

                date_div = event.find("div", class_=re.compile(r"date"))
                raw_date_str = None
                if date_div:
                    raw_date_str = normalize_ws(
                        date_div.get(
                            "aria-label") or date_div.get_text(" ", strip=True)
                    )
                start_time_seq = parse_event_dates(
                    raw_date_str) if raw_date_str else []

                h4_tag = event.find("h4")
                description = (
                    normalize_ws(h4_tag.get_text(" ", strip=True))
                    if h4_tag
                    else card_title
                )

                bg_div = event.find(
                    "div", class_=re.compile(r"background-img"))
                image_url = (
                    extract_css_bg_image(bg_div["style"])
                    if bg_div and bg_div.get("style")
                    else None
                )
                if not image_url:
                    img_tag = event.find("img", src=True)
                    image_url = resolve_url(
                        img_tag.get("src")) if img_tag else None

                t_anchor = event.find("a", class_=re.compile(
                    r"tickets|onsalenow", re.IGNORECASE))
                ticket_link = (
                    resolve_url(t_anchor["href"])
                    if t_anchor and t_anchor.get("href")
                    else detail_url
                )

                staged_items.append(
                    {
                        "venue_name": DEFAULT_VENUE,
                        "address": DEFAULT_ADDRESS,
                        "title": card_title,
                        "start_time": start_time_seq,
                        "raw_date_str": raw_date_str,
                        "description": description or card_title,
                        "big_desc": description or card_title,
                        "external_url": detail_url,
                        "image_url": image_url,
                        "ticket_link": ticket_link,
                    }
                )
            except Exception as item_err:
                logger.error(
                    f"[ITEM ERROR] Failed parsing Tacoma Dome event #{idx}: {item_err}",
                    exc_info=True,
                )

        if not staged_items:
            return pd.DataFrame()

        # 2. Batch-fetch detail pages concurrently via HTTP (workers=10)
        logger.info(
            f"Batch-fetching {len(staged_items)} detail pages via HTTP (workers=10)..."
        )
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
                        dates, big_desc, ticket_link = parse_detail_html(
                            drv.page_source,
                            item["start_time"],
                            item["big_desc"] or item["description"] or "",
                            item.get("ticket_link"),
                            detail_url,
                        )
                        item["start_time"] = dates
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
                    f"[SKIP] No valid dates parsed for '{item.get('title')}' "
                    f"(raw='{item.get('raw_date_str')}')"
                )
                continue
            item.pop("raw_date_str", None)
            raw_records.append(item)

        return pd.DataFrame(raw_records) if raw_records else pd.DataFrame()

    except Exception as e:
        logger.error(
            f"[FATAL ERROR] Exception encountered during scraping loop: {e}",
            exc_info=True,
        )
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
    logger.info("Executing Tacoma Dome scraper task...")
    raw_df = scraper()

    if not raw_df.empty:
        pipeline = VividaIngestionPipeline(table_name="events")
        success = pipeline.run_standalone_pipeline(raw_df)
        if success:
            logger.info(
                "[PIPELINE SUCCESS] Tacoma Dome scraper execution completed successfully.")
        else:
            logger.error(
                "[PIPELINE ERROR] Tacoma Dome scraper pipeline execution reported errors.")
    else:
        logger.warning("[EXIT] No events scraped. Pipeline handoff skipped.")
