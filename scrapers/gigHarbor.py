"""
gigHarbor.py
Autonomous Scraper for Visit Gig Harbor Events (The Events Calendar / Tribe).
Uses Tribe REST API fast-path with HTTP-first list/detail fallback (workers=10)
and lazy Xvfb/Chrome hydration, plus centralized parse_event_dates normalization.
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
logger = logging.getLogger("scrape_gig_harbor")

BASE_URL = "https://visitgigharbor.com"
BASE_LIST_URL = "https://visitgigharbor.com/events-festivals/list/page/"
TRIBE_API_URL = "https://visitgigharbor.com/wp-json/tribe/events/v1/events"
DEFAULT_VENUE = "Gig Harbor, WA"
DEFAULT_ADDRESS = "Gig Harbor, WA"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json;q=0.8,*/*;q=0.7",
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


def strip_html_text(raw_html: Optional[str]) -> str:
    if not raw_html:
        return ""
    soup = BeautifulSoup(clean_html_source(raw_html), "html.parser")
    text = fetch_event_text(soup) or soup.get_text(" ", strip=True)
    return normalize_ws(text)


def normalize_event_group_key(detail_url: Optional[str], title: str) -> str:
    if detail_url:
        stripped = re.sub(r"/\d{4}-\d{2}-\d{2}/?$", "", detail_url.rstrip("/"))
        return stripped.lower()
    return title.lower()


def format_tribe_venue_and_address(venue_obj: Any) -> Tuple[str, str]:
    if not isinstance(venue_obj, dict):
        return DEFAULT_VENUE, DEFAULT_ADDRESS

    venue_name = strip_html_text(venue_obj.get("venue")) or DEFAULT_VENUE
    street = normalize_ws(venue_obj.get("address"))
    city = normalize_ws(venue_obj.get("city")) or "Gig Harbor"
    state = normalize_ws(venue_obj.get(
        "state") or venue_obj.get("province")) or "WA"
    zip_code = normalize_ws(venue_obj.get("zip"))

    state_zip = f"{state} {zip_code}".strip()
    parts = [p for p in [street, city, state_zip] if p]
    address = ", ".join(parts) if parts else DEFAULT_ADDRESS
    return venue_name, address


def fetch_via_tribe_api(session: requests.Session, max_pages: int = 5) -> List[Dict[str, Any]]:
    """Fast-path: queries The Events Calendar (Tribe) REST API on visitgigharbor.com."""
    grouped: Dict[str, Dict[str, Any]] = {}
    now_str = pd.Timestamp.now(
        tz="America/Los_Angeles").strftime("%Y-%m-%d 00:00:00")

    for page in range(1, max_pages + 1):
        resp = session.get(
            TRIBE_API_URL,
            params={"per_page": 50, "page": page, "start_date": now_str},
            timeout=10,
        )
        if resp.status_code != 200:
            break

        data = resp.json()
        events = data.get("events", []) if isinstance(data, dict) else []
        if not events:
            break

        for ev in events:
            title = strip_html_text(ev.get("title"))
            if not title:
                continue
            detail_url = resolve_url(ev.get("url"))

            raw_start = ev.get("start_date") or ""
            raw_end = ev.get("end_date") or ""
            dt_start = pd.to_datetime(raw_start, errors="coerce")
            dt_end = pd.to_datetime(raw_end, errors="coerce")
            if pd.isna(dt_start):
                start_times = parse_event_dates(raw_start)
            else:
                s_iso = dt_start.strftime("%Y-%m-%d")
                if pd.notnull(dt_end) and dt_end.date() > dt_start.date():
                    start_times = parse_event_dates(
                        f"{s_iso} - {dt_end.strftime('%Y-%m-%d')}"
                    )
                else:
                    start_times = parse_event_dates(s_iso)

            if not start_times:
                continue

            group_key = normalize_event_group_key(detail_url, title)
            if group_key in grouped:
                grouped[group_key]["dates_set"].update(start_times)
                continue

            venue_name, address = format_tribe_venue_and_address(
                ev.get("venue"))
            short_desc = strip_html_text(ev.get("excerpt"))
            full_desc = strip_html_text(ev.get("description"))
            # img_obj = ev.get("image") if isinstance(
            #     ev.get("image"), dict) else {}

            grouped[group_key] = {
                "venue_name": venue_name,
                "address": address,
                "title": title,
                "dates_set": set(start_times),
                "description": short_desc or full_desc[:250] or title,
                "big_desc": full_desc or short_desc or title,
                "external_url": detail_url,
                # resolve_url(img_obj.get("url")),
                "image_url": "https://visitgigharbor.com/wp-content/uploads/2026/10/cropped-vgh-logo.png",
                "ticket_link": resolve_url(ev.get("website")) or detail_url,
            }

        total_pages = int(data.get("total_pages") or 1)
        if page >= total_pages:
            break

    records: List[Dict[str, Any]] = []
    for entry in grouped.values():
        entry["start_time"] = sorted(entry.pop("dates_set"))
        records.append(entry)
    return records


def extract_detail_urls_from_list(soup: BeautifulSoup) -> List[str]:
    urls: List[str] = []
    title_blocks = soup.find_all(
        ["h3", "h4"],
        class_=re.compile(r"tribe-events-calendar-list__event-title", re.I),
    )
    for block in title_blocks:
        anchor = block.find("a", href=True)
        if anchor and anchor.get("href"):
            resolved = resolve_url(anchor["href"])
            if resolved:
                urls.append(resolved)
    return urls


def parse_detail_html(html: str, url: str) -> Optional[Dict[str, Any]]:
    """Parses a Visit Gig Harbor Tribe event detail page into a structured record."""
    det_soup = BeautifulSoup(clean_html_source(html), "html.parser")
    if not det_soup:
        return None

    target_section = (
        det_soup.find("div", class_=re.compile(
            r"tribe-events-single|entry-content", re.I))
        or det_soup.find("div", id="tribe-events-content")
        or det_soup
    )
    for element in target_section(["script", "style"]):
        element.decompose()

    h1_tag = target_section.find("h1")
    title = normalize_ws(h1_tag.get_text(" ", strip=True)) if h1_tag else ""
    if not title:
        return None

    venue_name = DEFAULT_VENUE
    venue_el = target_section.find(
        ["span", "dd", "h3"], class_=re.compile(r"tribe-venue", re.I)
    )
    if venue_el:
        parsed_venue = normalize_ws(venue_el.get_text(" ", strip=True))
        if parsed_venue:
            venue_name = parsed_venue

    # Parse start and optional end date spans
    start_dates: List[str] = []
    start_el = target_section.find(
        ["span", "abbr"],
        class_=re.compile(
            r"tribe-events-schedule__date--start|tribe-event-date-start|tribe-events-abbr",
            re.I,
        ),
    )
    end_el = target_section.find(
        ["span", "abbr"],
        class_=re.compile(
            r"tribe-events-schedule__date--end|tribe-event-date-end", re.I
        ),
    )
    if start_el:
        s_txt = normalize_ws(start_el.get_text(" ", strip=True))
        e_txt = normalize_ws(end_el.get_text(
            " ", strip=True)) if end_el else ""
        date_expr = f"{s_txt} - {e_txt}" if e_txt and e_txt != s_txt else s_txt
        start_dates = parse_event_dates(date_expr)

    if not start_dates:
        schedule_el = target_section.find(
            ["div", "h2"], class_=re.compile(r"tribe-events-schedule", re.I)
        )
        if schedule_el:
            start_dates = parse_event_dates(
                normalize_ws(schedule_el.get_text(" ", strip=True))
            )

    if not start_dates:
        return None

    # Featured image (prefer og:image or dedicated Tribe event image over inline icons)
    image_url: Optional[str] = None
    og_img = det_soup.find("meta", property="og:image")
    if og_img and og_img.get("content"):
        image_url = resolve_url(og_img["content"])
    if not image_url:
        hero_wrap = target_section.find(
            "div", class_=re.compile(r"tribe-events-event-image|featured-image", re.I)
        )
        img_tag = (hero_wrap or target_section).find("img", src=True)
        if img_tag and img_tag.get("src"):
            image_url = resolve_url(img_tag["src"])

    # External ticket / registration link
    ticket_link = url
    t_btn = target_section.find(
        "a",
        href=re.compile(
            r"eventbrite|ticket|register|rsvp|tix|brownpapertickets", re.I),
    )
    if t_btn and t_btn.get("href"):
        ticket_link = resolve_url(t_btn["href"]) or url

    p_tag = target_section.find(
        "p", class_=re.compile(r"wp-block-paragraph", re.I)
    ) or target_section.find("p")
    short_desc = normalize_ws(p_tag.get_text(" ", strip=True)) if p_tag else ""

    rich_text = fetch_event_text(target_section)
    big_desc = normalize_ws(rich_text) if rich_text else (short_desc or title)
    description = short_desc or (big_desc[:250] if big_desc else title)

    # Address Parsing (Tribe microdata format)
    address = DEFAULT_ADDRESS
    address_sec = target_section.find(
        "address",
        class_=re.compile(
            r"tribe-block__venue__address|tribe-events-address", re.I),
    )
    if address_sec:
        street = address_sec.find(
            "span", class_=re.compile(r"tribe-street-address", re.I))
        locality = address_sec.find(
            "span", class_=re.compile(r"tribe-locality", re.I))
        region = address_sec.find(
            ["abbr", "span"], class_=re.compile(r"tribe-region", re.I)
        )
        postal = address_sec.find(
            "span", class_=re.compile(r"tribe-postal-code", re.I))

        st_str = normalize_ws(street.get_text()) if street else ""
        loc_str = normalize_ws(
            locality.get_text()) if locality else "Gig Harbor"
        reg_str = normalize_ws(region.get_text()) if region else "WA"
        post_str = normalize_ws(postal.get_text()) if postal else ""

        addr_parts = [p for p in [st_str, loc_str,
                                  f"{reg_str} {post_str}".strip()] if p]
        if addr_parts:
            address = ", ".join(addr_parts)

    return {
        "venue_name": venue_name,
        "address": address,
        "title": title,
        "start_time": start_dates,
        "description": description,
        "big_desc": big_desc,
        "external_url": url,
        "image_url": image_url,
        "ticket_link": ticket_link,
    }


def fetch_detail_via_http(
    session: requests.Session, url: str
) -> Tuple[str, Optional[Dict[str, Any]], bool]:
    try:
        resp = session.get(url, timeout=15)
        resp.raise_for_status()
        if len(resp.text) < 500:
            return url, None, False
        record = parse_detail_html(resp.text, url)
        return url, record, True
    except Exception:
        return url, None, False


def scraper(max_pages: int = 5) -> pd.DataFrame:
    """Scrapes Visit Gig Harbor events via Tribe REST API / HTTP-first + Chrome fallback."""
    logger.info(
        f"[START] Scraping Visit Gig Harbor Events (scanning up to {max_pages} pages)..."
    )
    session = build_http_session(pool_size=12)

    # 1. Probe Tribe REST API first
    try:
        api_records = fetch_via_tribe_api(session, max_pages=max_pages)
        if api_records:
            logger.info(
                f"[TRIBE API SUCCESS] Retrieved {len(api_records)} unique Gig Harbor events via REST API."
            )
            return pd.DataFrame(api_records)
    except Exception:
        pass

    display = None
    driver = None
    detail_urls: List[str] = []
    use_browser_listing = False

    def ensure_browser():
        nonlocal display, driver
        if driver is None:
            from pyvirtualdisplay import Display

            display = Display(visible=0, size=(1920, 1080), backend="xvfb")
            display.start()
            driver = create_stealth_driver()
        return driver

    try:
        # 2. HTTP-first list pagination
        for page in range(1, max_pages + 1):
            page_url = f"{BASE_LIST_URL}{page}/"
            logger.info(f"[PAGINATION] Scanning list page {page}: {page_url}")
            try:
                resp = session.get(page_url, timeout=15)
                if resp.status_code == 404:
                    break
                resp.raise_for_status()
                soup = BeautifulSoup(
                    clean_html_source(resp.text), "html.parser")
                urls = extract_detail_urls_from_list(soup)
                if not urls:
                    if page == 1:
                        use_browser_listing = True
                    break
                detail_urls.extend(urls)
            except Exception as http_err:
                logger.warning(
                    f"[HTTP LISTING] Page {page} failed ({http_err}).")
                if page == 1:
                    use_browser_listing = True
                break

        if use_browser_listing:
            from selenium.webdriver.common.by import By
            from selenium.webdriver.support import expected_conditions as EC
            from selenium.webdriver.support.ui import WebDriverWait

            drv = ensure_browser()
            for page in range(1, max_pages + 1):
                page_url = f"{BASE_LIST_URL}{page}/"
                drv.get(page_url)
                try:
                    WebDriverWait(drv, 10).until(
                        EC.presence_of_element_located(
                            (
                                By.CSS_SELECTOR,
                                "h4.tribe-events-calendar-list__event-title, .tribe-events-calendar-list",
                            )
                        )
                    )
                except Exception:
                    pass
                soup = BeautifulSoup(clean_html_source(
                    drv.page_source), "html.parser")
                urls = extract_detail_urls_from_list(soup)
                if not urls:
                    break
                detail_urls.extend(urls)

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

        detail_urls = sorted(set(detail_urls))
        if not detail_urls:
            logger.warning(
                "[PARSER WARNING] Found 0 Gig Harbor event detail URLs.")
            return pd.DataFrame()

        logger.info(
            f"[PARSER SUCCESS] Extracted {len(detail_urls)} unique event URLs. Fetching details (workers=10)..."
        )

        # 3. Concurrent HTTP detail fetching
        parsed_records: List[Dict[str, Any]] = []
        fallback_urls: List[str] = []

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [
                executor.submit(fetch_detail_via_http, session, u) for u in detail_urls
            ]
            for fut in as_completed(futures):
                u, rec, ok = fut.result()
                if ok:
                    if rec:
                        parsed_records.append(rec)
                else:
                    fallback_urls.append(u)

        if fallback_urls:
            drv = ensure_browser()
            for u in fallback_urls:
                try:
                    drv.get(u)
                    time.sleep(1.5)
                    rec = parse_detail_html(drv.page_source, u)
                    if rec:
                        parsed_records.append(rec)
                except Exception as fb_err:
                    logger.warning(f"[FALLBACK WARN] Failed {u}: {fb_err}")

        # 4. Consolidate recurring dates for identical events
        grouped: Dict[str, Dict[str, Any]] = {}
        for rec in parsed_records:
            key = normalize_event_group_key(
                rec.get("external_url"), rec["title"])
            if key in grouped:
                grouped[key]["dates_set"].update(rec["start_time"])
                continue
            entry = dict(rec)
            entry["dates_set"] = set(rec["start_time"])
            grouped[key] = entry

        final_rows: List[Dict[str, Any]] = []
        for entry in grouped.values():
            entry["start_time"] = sorted(entry.pop("dates_set"))
            final_rows.append(entry)

        return pd.DataFrame(final_rows) if final_rows else pd.DataFrame()

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
    logger.info("Executing Visit Gig Harbor autonomous scraper pipeline task...")
    raw_df = scraper(max_pages=5)

    if not raw_df.empty:
        pipeline = VividaIngestionPipeline(table_name="events")
        success = pipeline.run_standalone_pipeline(raw_df)

        if success:
            logger.info(
                "[PIPELINE SUCCESS] Visit Gig Harbor scraper pipeline execution completed successfully."
            )
        else:
            logger.error(
                "[PIPELINE ERROR] Visit Gig Harbor scraper pipeline execution reported errors."
            )
    else:
        logger.warning("[EXIT] No events scraped. Pipeline handoff skipped.")
