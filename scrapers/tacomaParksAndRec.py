"""
tacomaParksAndRec.py
Autonomous Production Scraper for Metro Parks Tacoma (The Events Calendar / Tribe Engine).
Uses HTTP-first fetching (Tribe REST API + concurrent HTML detail fallback, workers=10)
with lazy Xvfb/Chrome fallback, in-browser Tribe API execution after WAF clearance,
and centralized parse_event_dates range normalization.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import logging
import re
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
    create_stealth_driver,
    fetch_event_text,
    parse_event_dates,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(filename)s:%(lineno)d] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("scrape_parks_tacoma")

BASE_URL = "https://www.parkstacoma.gov"
TARGET_URL = "https://www.parkstacoma.gov/events/list/"
TRIBE_API_URL = "https://www.parkstacoma.gov/wp-json/tribe/events/v1/events"
DEFAULT_VENUE = "Metro Parks Tacoma"
DEFAULT_ADDRESS = "4702 S 19th St, Tacoma, WA 98405"

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


def clean_and_parse_single_date(raw_str: str) -> Optional[pd.Timestamp]:
    """Strips weekday prefixes, times, and non-breaking spaces before converting to Timestamp."""
    if not raw_str:
        return None

    clean_str = normalize_ws(raw_str)
    clean_str = re.sub(
        r"^(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*,?\s*",
        "",
        clean_str,
        flags=re.IGNORECASE,
    )
    clean_str = re.sub(
        r"\b\d{1,2}:\d{2}\s*(?:am|pm)?\b.*",
        "",
        clean_str,
        flags=re.IGNORECASE,
    ).strip()

    dt = pd.to_datetime(clean_str, errors="coerce", format="mixed")
    return dt if pd.notnull(dt) else None


def parse_tribe_date_range(event_el: BeautifulSoup) -> List[str]:
    """
    Parses Tribe Events Calendar date blocks for single-day and multi-day spans,
    returning [start] or [start, end] endpoints via parse_event_dates.
    """
    if not event_el:
        return []

    from_span = event_el.find("span", class_=re.compile(
        r"tribe-event-date-start", re.I))
    to_span = event_el.find("span", class_=re.compile(
        r"tribe-event-date-end", re.I))

    if from_span and to_span:
        dt_start = clean_and_parse_single_date(
            from_span.get_text(" ", strip=True))
        dt_end = clean_and_parse_single_date(to_span.get_text(" ", strip=True))
        if dt_start:
            start_iso = dt_start.strftime("%Y-%m-%d")
            if dt_end and dt_end.date() > dt_start.date():
                end_iso = dt_end.strftime("%Y-%m-%d")
                return parse_event_dates(f"{start_iso} - {end_iso}")
            return parse_event_dates(start_iso)

    if from_span:
        dt_start = clean_and_parse_single_date(
            from_span.get_text(" ", strip=True))
        if dt_start:
            return parse_event_dates(dt_start.strftime("%Y-%m-%d"))

    time_tag = (
        event_el.find("time", class_=re.compile(
            r"tribe-events-calendar-list__event-datetime", re.I))
        or event_el.find("time")
    )
    if time_tag:
        datetime_attr = time_tag.get(
            "datetime") or time_tag.get("data-datetime")
        if datetime_attr:
            dt_attr = pd.to_datetime(datetime_attr, errors="coerce")
            if pd.notnull(dt_attr):
                return parse_event_dates(dt_attr.strftime("%Y-%m-%d"))
        return parse_event_dates(normalize_ws(time_tag.get_text(" ", strip=True)))

    return []


def strip_html_text(raw_html: Optional[str]) -> str:
    if not raw_html:
        return ""
    soup = BeautifulSoup(clean_html_source(raw_html), "html.parser")
    text = fetch_event_text(soup) or soup.get_text(" ", strip=True)
    return normalize_ws(text)


def normalize_tribe_events_payload(
    events: List[Dict[str, Any]], seen_urls: Set[str]
) -> List[Dict[str, Any]]:
    """Converts Tribe REST API event JSON objects into Vivida standard records."""
    records: List[Dict[str, Any]] = []
    for ev in events:
        title = strip_html_text(ev.get("title"))
        if not title:
            continue

        detail_url = resolve_url(ev.get("url"))
        dedup_key = detail_url or title.lower()
        if dedup_key in seen_urls:
            continue
        seen_urls.add(dedup_key)

        raw_start = ev.get("start_date") or ""
        raw_end = ev.get("end_date") or ""
        dt_start = pd.to_datetime(raw_start, errors="coerce")
        dt_end = pd.to_datetime(raw_end, errors="coerce")

        if pd.isna(dt_start):
            start_times = parse_event_dates(raw_start)
        else:
            start_iso = dt_start.strftime("%Y-%m-%d")
            if pd.notnull(dt_end) and dt_end.date() > dt_start.date():
                start_times = parse_event_dates(
                    f"{start_iso} - {dt_end.strftime('%Y-%m-%d')}")
            else:
                start_times = parse_event_dates(start_iso)

        if not start_times:
            continue

        venue_obj = ev.get("venue") if isinstance(
            ev.get("venue"), dict) else {}
        venue_name = strip_html_text(venue_obj.get("venue")) or DEFAULT_VENUE

        addr_parts = [
            strip_html_text(venue_obj.get("address")),
            strip_html_text(venue_obj.get("city")),
            strip_html_text(venue_obj.get("state")
                            or venue_obj.get("province")),
            strip_html_text(venue_obj.get("zip")),
        ]
        full_addr = ", ".join([p for p in addr_parts if p])
        address = full_addr if len(full_addr) > 5 else DEFAULT_ADDRESS

        short_desc = strip_html_text(ev.get("excerpt"))
        full_desc = strip_html_text(ev.get("description"))
        cost_str = strip_html_text(ev.get("cost"))
        if cost_str:
            full_desc = normalize_ws(f"{full_desc} Cost: {cost_str}")

        img_obj = ev.get("image") if isinstance(ev.get("image"), dict) else {}
        image_url = resolve_url(img_obj.get("url"))
        ticket_link = resolve_url(ev.get("website")) or detail_url

        records.append(
            {
                "venue_name": venue_name,
                "address": address,
                "title": title,
                "start_time": start_times,
                "description": short_desc or full_desc or title,
                "big_desc": full_desc or short_desc or title,
                "external_url": detail_url,
                "image_url": image_url,
                "ticket_link": ticket_link,
            }
        )
    return records


def fetch_via_tribe_api(session: requests.Session, max_pages: int = 10) -> List[Dict[str, Any]]:
    """
    Fast-path: queries WordPress Tribe Events REST API directly to retrieve structured
    titles, dates, venues, addresses, images, and full HTML descriptions without scraping.
    """
    records: List[Dict[str, Any]] = []
    seen_urls: Set[str] = set()
    now_str = pd.Timestamp.now(
        tz="America/Los_Angeles").strftime("%Y-%m-%d 00:00:00")

    for page in range(1, max_pages + 1):
        resp = session.get(
            TRIBE_API_URL,
            params={"per_page": 50, "page": page, "start_date": now_str},
            timeout=15,
        )
        if resp.status_code != 200:
            if page == 1:
                raise RuntimeError(
                    f"Tribe REST API returned HTTP {resp.status_code}")
            break

        data = resp.json()
        events = data.get("events", [])
        if not events:
            break

        records.extend(normalize_tribe_events_payload(events, seen_urls))

        total_pages = int(data.get("total_pages") or 1)
        if page >= total_pages:
            break

    return records


def wait_for_waf_clearance(driver, timeout: int = 20) -> None:
    """Polls until Cloudflare/WAF 'Just a moment' challenge resolves and page body renders."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        title = (driver.title or "").lower()
        body_len = len(driver.page_source or "")
        if (
            "just a moment" not in title
            and "attention required" not in title
            and "checking your browser" not in title
            and body_len > 4000
        ):
            return
        time.sleep(1.5)


def fetch_tribe_api_in_browser(driver, max_pages: int = 10) -> List[Dict[str, Any]]:
    """
    Executes same-origin fetch() calls to the Tribe REST API directly inside the
    WAF-cleared Chrome browser session, inheriting cf_clearance cookies & TLS fingerprint.
    """
    records: List[Dict[str, Any]] = []
    seen_urls: Set[str] = set()
    now_str = pd.Timestamp.now(
        tz="America/Los_Angeles").strftime("%Y-%m-%d 00:00:00")

    driver.set_script_timeout(20)
    js_fetch = """
    const url = arguments[0];
    const done = arguments[1];
    fetch(url, { credentials: 'include', headers: { 'Accept': 'application/json' } })
      .then(async (r) => {
        const text = await r.text();
        done({ status: r.status, text: text });
      })
      .catch((err) => done({ status: 0, error: String(err) }));
    """

    for page in range(1, max_pages + 1):
        from urllib.parse import urlencode

        qs = urlencode({"per_page": 50, "page": page, "start_date": now_str})
        api_url = f"/wp-json/tribe/events/v1/events?{qs}"
        res = driver.execute_async_script(js_fetch, api_url)
        if not isinstance(res, dict) or res.get("status") != 200:
            logger.info(
                f"[IN-BROWSER TRIBE API] Returned status {res.get('status') if isinstance(res, dict) else 'ERR'} on page {page}."
            )
            break

        try:
            data = json.loads(res.get("text") or "{}")
        except Exception:
            break

        events = data.get("events", [])
        if not events:
            break

        records.extend(normalize_tribe_events_payload(events, seen_urls))

        total_pages = int(data.get("total_pages") or 1)
        if page >= total_pages:
            break

    return records


def parse_detail_html(
    html: str,
    current_dates: List[str],
    current_desc: str,
    current_ticket: Optional[str],
) -> Tuple[List[str], str, Optional[str]]:
    """Extracts fallback dates, rich description text, and ticket link from detail HTML."""
    det_soup = BeautifulSoup(clean_html_source(html), "html.parser")
    if not det_soup:
        return current_dates, current_desc, current_ticket

    dates = current_dates or parse_tribe_date_range(det_soup)

    container = (
        det_soup.find("section", class_=re.compile(r"event-details"))
        or det_soup.find("div", class_=re.compile(r"tribe-events-single|entry-content|elementor-text-editor"))
        or det_soup.find("main")
        or det_soup
    )
    rich_text = fetch_event_text(container)
    big_desc = normalize_ws(rich_text) if rich_text else current_desc

    ticket_link = current_ticket
    t_btn = det_soup.find("a", href=re.compile(
        r"ticket|register|buy", re.IGNORECASE))
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
        )
        item["start_time"] = dates
        item["big_desc"] = big_desc
        item["ticket_link"] = ticket_link
        return item, True
    except Exception:
        return item, False


def scraper() -> pd.DataFrame:
    """Scrapes Metro Parks Tacoma events via Tribe REST API first, falling back to Chrome + in-browser API / HTML."""
    logger.info(f"[START] Scraping Metro Parks Tacoma: {TARGET_URL}")
    session = build_http_session(pool_size=12)

    # 1. Fast-Path: WordPress Tribe Events REST API via HTTP
    try:
        api_records = fetch_via_tribe_api(session)
        if api_records:
            logger.info(
                f"[TRIBE API SUCCESS] Retrieved {len(api_records)} structured events directly via REST API."
            )
            return pd.DataFrame(api_records)
        logger.warning(
            "[TRIBE API] Returned 0 events; falling back to HTML scraping...")
    except Exception as api_err:
        logger.warning(
            f"[TRIBE API] Unavailable ({api_err}); falling back to HTML scraping...")

    # 2. Secondary Path: HTTP HTML Listing + Concurrent Detail Fetch (with lazy Xvfb/Chrome fallback)
    main_html: Optional[str] = None
    try:
        resp = session.get(TARGET_URL, timeout=15)
        resp.raise_for_status()
        if "tribe-events" in resp.text or "/event/" in resp.text:
            main_html = resp.text
            logger.info(
                "[HTTP FAST-PATH] Loaded Metro Parks Tacoma HTML listing via HTTP.")
    except Exception as http_err:
        logger.warning(
            f"[HTTP LISTING] Failed ({http_err}); launching Xvfb + Chrome fallback...")

    if not main_html:
        from pyvirtualdisplay import Display

        display = Display(visible=0, size=(1920, 1080), backend="xvfb")
        display.start()
        driver = None
        try:
            driver = create_stealth_driver()
            driver.get(TARGET_URL)
            wait_for_waf_clearance(driver, timeout=20)
            time.sleep(2)

            # Try same-origin Tribe REST API directly inside the WAF-cleared browser first!
            browser_api_records = fetch_tribe_api_in_browser(driver)
            if browser_api_records:
                logger.info(
                    f"[IN-BROWSER TRIBE API SUCCESS] Retrieved {len(browser_api_records)} structured events via Chrome session."
                )
                return pd.DataFrame(browser_api_records)

            driver.execute_script(
                "window.scrollTo(0, document.body.scrollHeight / 2);")
            time.sleep(2)
            main_html = driver.page_source
            logger.info(
                f"[CHROME PAGE] Loaded title='{normalize_ws(driver.title)}', html_len={len(main_html)}"
            )

            # Transfer solved WAF cookies + exact browser User-Agent to requests.Session
            ua = driver.execute_script("return navigator.userAgent;")
            if ua:
                session.headers.update({"User-Agent": ua})
            for cookie in driver.get_cookies():
                session.cookies.set(
                    cookie["name"],
                    cookie["value"],
                    domain=cookie.get("domain"),
                    path=cookie.get("path", "/"),
                )
        finally:
            if driver:
                try:
                    driver.quit()
                except Exception:
                    pass
            display.stop()

    soup = BeautifulSoup(clean_html_source(main_html), "html.parser")
    target_section = (
        soup.find("ul", class_=re.compile(r"tribe-events-calendar-list"))
        or soup.find("div", id="tribe-events-pg-template")
        or soup.find("main")
        or soup
    )
    for element in target_section(["script", "style"]):
        element.decompose()

    event_items = target_section.find_all(
        ["div", "article", "li"],
        class_=re.compile(
            r"tribe-events-calendar-list__event-row|tribe-events-calendar-list__event\b|tribe-events-single|event-card|type-tribe_events",
            re.I,
        ),
    )
    if not event_items:
        event_items = target_section.find_all("article")

    logger.info(
        f"[PARSER SUCCESS] Found {len(event_items)} Metro Parks Tacoma event rows.")

    staged_items: List[Dict[str, Any]] = []
    seen_urls: Set[str] = set()

    for idx, event in enumerate(event_items, start=1):
        try:
            h4_tag = event.find(["h4", "h3", "h2"], class_=re.compile(
                r"title", re.I)) or event.find(["h4", "h3", "h2"])
            card_title = normalize_ws(h4_tag.get_text(
                " ", strip=True)) if h4_tag else ""
            if not card_title:
                continue

            anchor = h4_tag.find(
                "a", href=True) if h4_tag else event.find("a", href=True)
            detail_url = resolve_url(anchor["href"]) if anchor else None

            dedup_key = detail_url or card_title.lower()
            if dedup_key in seen_urls:
                continue
            seen_urls.add(dedup_key)

            venue_span = event.find(["span", "div"], class_=re.compile(
                r"tribe-events-calendar-list__event-venue-title|venue"))
            venue_name = normalize_ws(venue_span.get_text(
                " ", strip=True)) if venue_span else DEFAULT_VENUE

            addr_span = event.find(["span", "div"], class_=re.compile(
                r"tribe-events-calendar-list__event-venue-address|address"))
            address = normalize_ws(addr_span.get_text(
                " ", strip=True)) if addr_span else DEFAULT_ADDRESS

            start_time_seq = parse_tribe_date_range(event)

            desc_div = event.find("div", class_=re.compile(
                r"tribe-events-calendar-list__event-description|excerpt"))
            description = normalize_ws(desc_div.get_text(
                " ", strip=True)) if desc_div else card_title

            img_tag = event.find("img", src=True)
            image_url = resolve_url(img_tag.get("src")) if img_tag else None

            t_anchor = event.find("a", href=re.compile(
                r"ticket|register|buy", re.IGNORECASE))
            ticket_link = resolve_url(t_anchor["href"]) if t_anchor and t_anchor.get(
                "href") else detail_url

            staged_items.append(
                {
                    "venue_name": venue_name,
                    "address": address,
                    "title": card_title,
                    "start_time": start_time_seq,
                    "description": description,
                    "big_desc": description,
                    "external_url": detail_url,
                    "image_url": image_url,
                    "ticket_link": ticket_link,
                }
            )
        except Exception as item_err:
            logger.error(
                f"[ITEM ERROR] Failed parsing Metro Parks event #{idx}: {item_err}", exc_info=True)

    if not staged_items:
        return pd.DataFrame()

    logger.info(
        f"Batch-fetching {len(staged_items)} detail pages via HTTP (workers=10)...")
    enriched_items: List[Dict[str, Any]] = []
    fallback_queue: List[Dict[str, Any]] = []

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(
            fetch_detail_via_http, session, item) for item in staged_items]
        for fut in as_completed(futures):
            updated_item, ok = fut.result()
            if ok:
                enriched_items.append(updated_item)
            else:
                fallback_queue.append(updated_item)

    if fallback_queue:
        from pyvirtualdisplay import Display

        display = Display(visible=0, size=(1920, 1080), backend="xvfb")
        display.start()
        driver = None
        try:
            driver = create_stealth_driver()
            for item in fallback_queue:
                detail_url = item.get("external_url")
                if detail_url:
                    try:
                        driver.get(detail_url)
                        wait_for_waf_clearance(driver, timeout=12)
                        dates, big_desc, ticket_link = parse_detail_html(
                            driver.page_source,
                            item["start_time"],
                            item["big_desc"] or item["description"] or "",
                            item.get("ticket_link"),
                        )
                        item["start_time"] = dates
                        item["big_desc"] = big_desc
                        item["ticket_link"] = ticket_link
                    except Exception as fb_err:
                        logger.warning(
                            f"[FALLBACK WARN] Failed {detail_url}: {fb_err}")
                enriched_items.append(item)
        finally:
            if driver:
                try:
                    driver.quit()
                except Exception:
                    pass
            display.stop()

    raw_records = []
    for item in enriched_items:
        if not item.get("start_time"):
            logger.warning(
                f"[SKIP] No valid dates parsed for '{item.get('title')}'.")
            continue
        raw_records.append(item)

    return pd.DataFrame(raw_records) if raw_records else pd.DataFrame()


if __name__ == "__main__":
    logger.info("Executing Metro Parks Tacoma scraper task...")
    raw_df = scraper()

    if not raw_df.empty:
        pipeline = VividaIngestionPipeline(table_name="events")
        success = pipeline.run_standalone_pipeline(raw_df)
        if success:
            logger.info(
                "[PIPELINE SUCCESS] Metro Parks Tacoma scraper execution completed successfully.")
        else:
            logger.error(
                "[PIPELINE ERROR] Metro Parks Tacoma scraper pipeline execution reported errors.")
    else:
        logger.warning("[EXIT] No events scraped. Pipeline handoff skipped.")
