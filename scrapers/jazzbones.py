"""
jazzbones.py
Autonomous Scraper for Jazzbones (Tacoma, WA).
Handles VenuePilot SPA calendar (including iframe-embedded VenuePilot widgets),
alternate calendar routes (/events, /calendar), JSON-LD fallback,
and centralized parse_event_dates normalization.
"""

import json
import logging
import re
import sys
import time
from typing import Any, Dict, List, Optional

from bs4 import BeautifulSoup
import pandas as pd

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
logger = logging.getLogger("scrape_jazzbones")

BASE_URL = "https://jazzbones.com"
TARGET_URLS = [
    "https://jazzbones.com/#/events",
    "https://jazzbones.com/events",
    "https://jazzbones.com/calendar",
]
DEFAULT_VENUE = "Jazzbones"
DEFAULT_ADDRESS = "2803 6th Ave, Tacoma, WA 98406"


def normalize_ws(text: Optional[str]) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("\xa0", " ")).strip()


def resolve_url(raw_url: Optional[str]) -> Optional[str]:
    if not raw_url:
        return None
    clean = str(raw_url).strip()
    if not clean or clean == "#" or clean.lower().startswith("javascript:"):
        return None
    if clean.startswith("#/"):
        return f"{BASE_URL}/{clean}"
    if clean.startswith("//"):
        return f"https:{clean}"
    if clean.startswith("http://") or clean.startswith("https://"):
        return clean
    return f"{BASE_URL}/{clean.lstrip('/')}"


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
    Inspects Chrome performance logs for VenuePilot GraphQL/API event payloads
    (works even when VenuePilot runs inside a cross-origin iframe).
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
                    f"{BASE_URL}/#/events/{ev_slug}" if ev_slug else TARGET_URLS[0]
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


def extract_jsonld_events(soup: BeautifulSoup) -> List[Dict[str, Any]]:
    """Fallback extractor for schema.org MusicEvent / Event JSON-LD blocks."""
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
            if not isinstance(item, dict):
                continue
            ev_type = str(item.get("@type") or "")
            if "Event" not in ev_type:
                continue
            title = normalize_ws(str(item.get("name") or ""))
            raw_start = str(item.get("startDate") or "")
            if not title or not raw_start:
                continue
            dates = parse_event_dates(raw_start[:10])
            if not dates:
                continue
            desc = normalize_ws(str(item.get("description") or ""))
            url = resolve_url(item.get("url")) or TARGET_URLS[0]
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


def parse_vp_cards_from_html(html: str) -> List[Dict[str, Any]]:
    """Extracts VenuePilot event cards from a document or iframe HTML."""
    soup = BeautifulSoup(clean_html_source(html), "html.parser")
    target_section = soup.find("div", class_="vp-events-container") or soup
    for element in target_section(["script", "style"]):
        element.decompose()

    event_items = target_section.find_all(
        "div", class_=re.compile(r"vp-event-card", re.I)
    )
    if not event_items:
        return []

    staged_items: List[Dict[str, Any]] = []
    for event in event_items:
        title_div = event.find("div", class_=re.compile(r"vp-event-name", re.I))
        title = (
            normalize_ws(title_div.get_text(" ", strip=True)) if title_div else ""
        )
        if not title:
            continue

        start_time_list: List[str] = []
        date_span = event.find("span", class_=re.compile(r"vp-date", re.I))
        if date_span:
            start_time_list = parse_event_dates(
                normalize_ws(date_span.get_text(" ", strip=True))
            )

        desc_div = event.find("div", class_=re.compile(r"vp-details", re.I))
        card_desc = (
            normalize_ws(desc_div.get_text(" ", strip=True)) if desc_div else None
        )

        link_anchor = event.find(
            "a", class_=re.compile(r"vp-event-link", re.I), href=True
        )
        detail_url = (
            resolve_url(link_anchor["href"]) if link_anchor else TARGET_URLS[0]
        )

        t_anchor = event.find(
            "a",
            href=re.compile(
                r"ticket|buy|booking|eventbrite|ticketweb|dice|seetickets", re.I
            ),
        )
        ticket_link = (
            resolve_url(t_anchor["href"])
            if t_anchor and t_anchor.get("href")
            else detail_url
        )

        img_div = event.find("div", class_=re.compile(r"vp-cover-img", re.I))
        image_url = (
            extract_css_bg_image(img_div["style"])
            if img_div and img_div.get("style")
            else "https://jazzbones.com/wp-content/uploads/2025/10/jazzbones-logo-chrome.png"
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
    return staged_items


def collect_dom_events_across_frames(driver) -> List[Dict[str, Any]]:
    """Checks top-level DOM, any embedded iframes (e.g. VenuePilot iframe), and JSON-LD."""
    top_html = driver.page_source
    items = parse_vp_cards_from_html(top_html)
    if items:
        return items

    from selenium.webdriver.common.by import By

    iframes = driver.find_elements(By.TAG_NAME, "iframe")
    for idx, frame in enumerate(iframes):
        try:
            src = frame.get_attribute("src") or ""
            driver.switch_to.frame(frame)
            time.sleep(1.5)
            frame_items = parse_vp_cards_from_html(driver.page_source)
            driver.switch_to.default_content()
            if frame_items:
                logger.info(
                    f"[IFRAME MATCH] Extracted {len(frame_items)} VenuePilot cards from iframe #{idx} ({src})."
                )
                return frame_items
        except Exception:
            try:
                driver.switch_to.default_content()
            except Exception:
                pass

    top_soup = BeautifulSoup(top_html, "html.parser")
    jsonld_items = extract_jsonld_events(top_soup)
    if jsonld_items:
        logger.info(f"[JSON-LD MATCH] Extracted {len(jsonld_items)} events from JSON-LD.")
        return jsonld_items

    return []


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
    """Scrapes Jazzbones events across SPA routes, iframes, network payloads, and JSON-LD."""
    logger.info(f"[START] Scraping Jazzbones: {TARGET_URLS[0]}")

    driver = None

    try:
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait

        with virtual_display(size=(1920, 1080)) as disp:
            driver = create_stealth_driver(headless=(disp is None))

            for url in TARGET_URLS:
                logger.info(f"[SELENIUM NAV] Rendering calendar route: {url}")
                driver.get(url)
                try:
                    WebDriverWait(driver, 10).until(
                        EC.presence_of_element_located(
                            (
                                By.CSS_SELECTOR,
                                "div.vp-events-container, div.vp-event-card, iframe[src*='venuepilot']",
                            )
                        )
                    )
                except Exception:
                    pass
                time.sleep(2.5)

                api_records = extract_venuepilot_network_events(driver)
                if api_records:
                    df_api = consolidate_records(api_records)
                    if not df_api.empty:
                        logger.info(
                            f"[VENUEPILOT API] Extracted {len(df_api)} consolidated events from network payload."
                        )
                        return df_api

                dom_records = collect_dom_events_across_frames(driver)
                if dom_records:
                    df_dom = consolidate_records(dom_records)
                    if not df_dom.empty:
                        logger.info(
                            f"[PARSER SUCCESS] Consolidated {len(df_dom)} Jazzbones events from {url}."
                        )
                        return df_dom

        logger.warning(
            "[PARSER WARNING] Found 0 event cards across all Jazzbones calendar routes/iframes."
        )
        return pd.DataFrame()

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
    logger.info("Executing Jazzbones autonomous scraper pipeline task...")
    raw_df = scraper()

    if not raw_df.empty:
        pipeline = VividaIngestionPipeline(table_name="events")
        success = pipeline.run_standalone_pipeline(raw_df)

        if success:
            logger.info(
                "[PIPELINE SUCCESS] Jazzbones scraper pipeline execution completed successfully."
            )
        else:
            logger.error(
                "[PIPELINE ERROR] Jazzbones scraper pipeline execution reported errors."
            )
    else:
        logger.warning("[EXIT] No events scraped. Pipeline handoff skipped.")