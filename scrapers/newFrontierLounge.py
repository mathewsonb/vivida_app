"""
newFrontierLounge.py
Autonomous Scraper for New Frontier Lounge (Tacoma, WA).
Handles VenuePilot SPA calendar (#/events) via cross-platform browser hydration +
network/DOM extraction, concurrent non-hash detail fetching when applicable,
and centralized parse_event_dates normalization.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import logging
import re
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
    create_stealth_driver,
    fetch_event_text,
    parse_event_dates,
    virtual_display,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(filename)s:%(lineno)d] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("scrape_new_frontier")

BASE_URL = "https://www.newfrontierlounge.com"
CALENDAR_URL = "https://www.newfrontierlounge.com/calendar"
TARGET_URL = "https://www.newfrontierlounge.com/calendar#/events"
DEFAULT_VENUE = "New Frontier Lounge"
DEFAULT_ADDRESS = "301 E 25th Street, Tacoma, WA 98421"

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
    if not clean or clean == "#" or clean.lower().startswith("javascript:"):
        return None
    if clean.startswith("#/"):
        return f"{CALENDAR_URL}{clean}"
    if clean.startswith("//"):
        return f"https:{clean}"
    if clean.startswith("http://") or clean.startswith("https://"):
        return clean
    return f"{BASE_URL}/{clean.lstrip('/')}"


def build_http_session(pool_size: int = 8) -> requests.Session:
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


def extract_css_bg_image(style_attr: Optional[str]) -> Optional[str]:
    """Safely extracts background image URL from inline CSS style attributes."""
    if not style_attr:
        return None
    match = re.search(
        r'url\((?:["\']?)(.*?)(?:["\']?)\)', style_attr, re.IGNORECASE
    )
    if match:
        return resolve_url(match.group(1).strip())
    return None


def extract_venuepilot_network_events(driver) -> List[Dict[str, Any]]:
    """
    Inspects Chrome performance logs for VenuePilot GraphQL/API event payloads,
    which contain full descriptions, ticket URLs, cover art, and exact dates without extra page loads.
    """
    records: List[Dict[str, Any]] = []
    try:
        logs = driver.get_log("performance")
    except Exception:
        return []

    seen_ids = set()
    for entry in logs:
        try:
            msg = json.loads(entry["message"])["message"]
            if msg.get("method") != "Network.responseReceived":
                continue
            resp = msg.get("params", {}).get("response", {})
            url = resp.get("url", "")
            mime = resp.get("mimeType", "")
            if (
                "json" not in mime
                and "venuepilot" not in url.lower()
                and "graphql" not in url.lower()
            ):
                continue

            req_id = msg["params"]["requestId"]
            body_obj = driver.execute_cdp_cmd(
                "Network.getResponseBody", {"requestId": req_id}
            )
            body_text = body_obj.get("body", "")
            if not body_text or (
                "events" not in body_text and "publicEvents" not in body_text
            ):
                continue

            payload = json.loads(body_text)
            candidates: List[Any] = []

            def walk(node: Any):
                if isinstance(node, dict):
                    for k, v in node.items():
                        if k in ("events", "publicEvents", "items") and isinstance(
                            v, list
                        ):
                            candidates.extend(v)
                        else:
                            walk(v)
                elif isinstance(node, list):
                    for item in node:
                        walk(item)

            walk(payload)

            for ev in candidates:
                if not isinstance(ev, dict):
                    continue
                title = normalize_ws(str(ev.get("name") or ev.get("title") or ""))
                raw_date = str(
                    ev.get("date")
                    or ev.get("startDate")
                    or ev.get("start_time")
                    or ""
                )
                if not title or not raw_date:
                    continue

                dates = parse_event_dates(
                    raw_date[:10]
                    if re.match(r"^\d{4}-\d{2}-\d{2}", raw_date)
                    else raw_date
                )
                if not dates:
                    continue

                ev_id = str(ev.get("id") or f"{title}:{dates[0]}")
                if ev_id in seen_ids:
                    continue
                seen_ids.add(ev_id)

                raw_desc = str(
                    ev.get("description")
                    or ev.get("announce")
                    or ev.get("details")
                    or ""
                )
                clean_desc = ""
                if raw_desc:
                    desc_soup = BeautifulSoup(
                        clean_html_source(raw_desc), "html.parser"
                    )
                    clean_desc = normalize_ws(
                        fetch_event_text(desc_soup)
                        or desc_soup.get_text(" ", strip=True)
                    )

                ev_slug = ev.get("id")
                detail_url = resolve_url(ev.get("url")) or (
                    f"{CALENDAR_URL}#/events/{ev_slug}" if ev_slug else TARGET_URL
                )
                ticket_url = (
                    resolve_url(
                        ev.get("ticketsUrl")
                        or ev.get("ticketUrl")
                        or ev.get("ticket_link")
                    )
                    or detail_url
                )
                image_url = resolve_url(
                    ev.get("coverImage")
                    or ev.get("image")
                    or ev.get("imageUrl")
                    or (
                        ev.get("images", [{}])[0].get("url")
                        if isinstance(ev.get("images"), list)
                        and ev.get("images")
                        and isinstance(ev["images"][0], dict)
                        else None
                    )
                )

                records.append(
                    {
                        "venue_name": DEFAULT_VENUE,
                        "address": DEFAULT_ADDRESS,
                        "title": title,
                        "start_time": dates,
                        "description": clean_desc[:250] if clean_desc else title,
                        "big_desc": clean_desc or title,
                        "external_url": detail_url,
                        "image_url": image_url,
                        "ticket_link": ticket_url,
                    }
                )
        except Exception:
            continue

    return records


def parse_detail_html(
    html: str,
    current_dates: List[str],
    card_desc: Optional[str],
    card_title: str,
    current_ticket: Optional[str],
    detail_url: str,
) -> Tuple[List[str], str, str, Optional[str]]:
    """Extracts fallback dates, rich description, and ticket link from non-hash detail HTML."""
    det_soup = BeautifulSoup(clean_html_source(html), "html.parser")
    if not det_soup:
        fallback = card_desc or card_title
        return current_dates, fallback, fallback, current_ticket or detail_url

    modal_div = (
        det_soup.find(
            "div", class_=re.compile(r"vp-event-details-modal|vp-event-listing", re.I)
        )
        or det_soup
    )

    dates = current_dates
    if not dates:
        date_el = modal_div.find(
            ["span", "div"], class_=re.compile(r"vp-date|date", re.I)
        )
        if date_el:
            dates = parse_event_dates(
                normalize_ws(date_el.get_text(" ", strip=True))
            )

    ticket_link = current_ticket
    t_btn = modal_div.find(
        ["a", "button"],
        href=re.compile(
            r"ticket|buy|booking|eventbrite|ticketfly|dice|seetickets", re.I
        ),
    )
    if t_btn and t_btn.get("href"):
        ticket_link = resolve_url(t_btn["href"]) or ticket_link

    rich_text = fetch_event_text(modal_div)
    big_desc = normalize_ws(rich_text) if rich_text else (card_desc or card_title)
    description = card_desc or (big_desc[:250] if big_desc else card_title)

    return dates, description, big_desc, ticket_link or detail_url


def fetch_detail_via_http(
    session: requests.Session, item: Dict[str, Any]
) -> Dict[str, Any]:
    detail_url = item.get("external_url")
    # Hash-fragment SPA routes (#/events/...) cannot be fetched separately over HTTP
    if not detail_url or "#" in detail_url:
        return item

    try:
        resp = session.get(detail_url, timeout=12)
        resp.raise_for_status()
        if len(resp.text) < 500:
            return item

        dates, description, big_desc, ticket_link = parse_detail_html(
            resp.text,
            item["start_time"],
            item.get("_card_desc"),
            item["title"],
            item.get("ticket_link"),
            detail_url,
        )
        item["start_time"] = dates
        item["description"] = description
        item["big_desc"] = big_desc
        item["ticket_link"] = ticket_link
    except Exception:
        pass
    return item


def consolidate_records(records: List[Dict[str, Any]]) -> pd.DataFrame:
    """Groups recurring nights by (external_url or title) into sorted start_time arrays."""
    grouped: Dict[str, Dict[str, Any]] = {}
    for rec in records:
        title = normalize_ws(rec.get("title"))
        dates = rec.get("start_time") or []
        if not title or not dates:
            continue

        key = (rec.get("external_url") or title).lower()
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
        entry.pop("_card_desc", None)
        final_rows.append(entry)

    return pd.DataFrame(final_rows) if final_rows else pd.DataFrame()


def scraper() -> pd.DataFrame:
    """Scrapes New Frontier Lounge VenuePilot events via browser hydration + API/DOM parsing."""
    logger.info(f"[START] Scraping New Frontier Lounge: {TARGET_URL}")
    session = build_http_session(pool_size=8)

    driver = None

    try:
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait

        with virtual_display(size=(1920, 1080)) as disp:
            driver = create_stealth_driver(headless=(disp is None))

            logger.info(
                f"[SELENIUM NAV] Rendering VenuePilot calendar: {TARGET_URL}"
            )
            driver.get(TARGET_URL)
            try:
                WebDriverWait(driver, 12).until(
                    EC.presence_of_element_located(
                        (By.CSS_SELECTOR, "div.vp-events-container, div.vp-event-card")
                    )
                )
            except Exception:
                pass
            time.sleep(2.5)

            # 1. Fast-path: extract structured VenuePilot API response from network logs if captured
            api_records = extract_venuepilot_network_events(driver)
            if api_records:
                df_api = consolidate_records(api_records)
                if not df_api.empty:
                    logger.info(
                        f"[VENUEPILOT API] Extracted {len(df_api)} consolidated events directly from SPA network payload."
                    )
                    return df_api

            # 2. DOM extraction from hydrated VenuePilot cards
            soup = BeautifulSoup(
                clean_html_source(driver.page_source), "html.parser"
            )
            target_section = soup.find("div", class_="vp-events-container") or soup
            for element in target_section(["script", "style"]):
                element.decompose()

            event_items = target_section.find_all(
                "div", class_=re.compile(r"vp-event-card", re.I)
            )
            if not event_items:
                logger.warning(
                    "[PARSER WARNING] Found 0 event cards matching class 'vp-event-card'."
                )
                return pd.DataFrame()

            logger.info(
                f"[PARSER SUCCESS] Found {len(event_items)} hydrated VenuePilot event cards."
            )

            staged_items: List[Dict[str, Any]] = []
            for idx, event in enumerate(event_items, start=1):
                try:
                    title_div = event.find(
                        "div", class_=re.compile(r"vp-event-name", re.I)
                    )
                    title = (
                        normalize_ws(title_div.get_text(" ", strip=True))
                        if title_div
                        else ""
                    )
                    if not title:
                        continue

                    start_time_list: List[str] = []
                    date_span = event.find(
                        "span", class_=re.compile(r"vp-date", re.I)
                    )
                    if date_span:
                        start_time_list = parse_event_dates(
                            normalize_ws(date_span.get_text(" ", strip=True))
                        )

                    desc_div = event.find(
                        "div", class_=re.compile(r"vp-details", re.I)
                    )
                    card_desc = (
                        normalize_ws(desc_div.get_text(" ", strip=True))
                        if desc_div
                        else None
                    )

                    link_anchor = event.find(
                        "a", class_=re.compile(r"vp-event-link", re.I), href=True
                    )
                    detail_url = (
                        resolve_url(link_anchor["href"]) if link_anchor else TARGET_URL
                    )

                    t_anchor = event.find(
                        "a",
                        href=re.compile(
                            r"ticket|buy|eventbrite|ticketfly|dice|seetickets", re.I
                        ),
                    )
                    ticket_link = (
                        resolve_url(t_anchor["href"])
                        if t_anchor and t_anchor.get("href")
                        else detail_url
                    )

                    img_div = event.find(
                        "div", class_=re.compile(r"vp-cover-img", re.I)
                    )
                    image_url = (
                        extract_css_bg_image(img_div["style"])
                        if img_div and img_div.get("style")
                        else None
                    )

                    staged_items.append(
                        {
                            "venue_name": DEFAULT_VENUE,
                            "address": DEFAULT_ADDRESS,
                            "title": title,
                            "start_time": start_time_list,
                            "_card_desc": card_desc,
                            "description": card_desc or title,
                            "big_desc": card_desc or title,
                            "external_url": detail_url,
                            "image_url": image_url,
                            "ticket_link": ticket_link,
                        }
                    )
                except Exception as item_err:
                    logger.error(
                        f"[ITEM ERROR] Failed parsing New Frontier card #{idx}: {item_err}",
                        exc_info=True,
                    )

        # 3. Concurrent HTTP fetch for any non-hash external detail URLs
        non_hash_count = sum(
            1
            for it in staged_items
            if it.get("external_url") and "#" not in it["external_url"]
        )
        if non_hash_count > 0:
            logger.info(
                f"Batch-fetching {non_hash_count} non-hash detail pages via HTTP (workers=6)..."
            )
            enriched: List[Dict[str, Any]] = []
            with ThreadPoolExecutor(max_workers=6) as executor:
                futures = [
                    executor.submit(fetch_detail_via_http, session, item)
                    for item in staged_items
                ]
                for fut in as_completed(futures):
                    enriched.append(fut.result())
            staged_items = enriched

        return consolidate_records(staged_items)

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


if __name__ == "__main__":
    logger.info("Executing New Frontier Lounge autonomous scraper task...")
    raw_df = scraper()

    if not raw_df.empty:
        pipeline = VividaIngestionPipeline(table_name="events")
        success = pipeline.run_standalone_pipeline(raw_df)

        if success:
            logger.info(
                "[PIPELINE SUCCESS] New Frontier Lounge scraper pipeline execution completed successfully."
            )
        else:
            logger.error(
                "[PIPELINE ERROR] New Frontier Lounge scraper pipeline execution reported errors."
            )
    else:
        logger.warning("[EXIT] No events scraped. Pipeline handoff skipped.")