"""
cityOfUniversityPlace.py
Autonomous Scraper for City of University Place (WA) Events (CivicPlus).
Uses HTTP-first calendar listing + concurrent detail fetching (workers=10)
with lazy cross-platform browser hydration fallback and centralized parse_event_dates normalization.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import logging
import re
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
logger = logging.getLogger("scrape_city_of_up")

BASE_URL = "https://www.cityofup.com"
TARGET_URL = "https://www.cityofup.com/132/Events"
ALT_CALENDAR_URL = "https://www.cityofup.com/calendar.aspx"
DEFAULT_VENUE = "City of University Place"
DEFAULT_ADDRESS = "3609 Market Place W, University Place, WA 98466"

GENERIC_TITLES = {
    "calendar",
    "events",
    "event",
    "event details",
    "city of university place",
    "university place",
    "view details",
    "more details",
    "learn more",
    "read more",
    "home",
    "menu",
    "search",
}

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


def is_valid_event_title(text: Optional[str]) -> bool:
    clean = normalize_ws(text)
    if not clean or len(clean) < 3:
        return False
    if clean.lower() in GENERIC_TITLES:
        return False
    # Reject bare date strings used as anchor text
    if re.fullmatch(r"(?:[A-Za-z]+\s+\d{1,2}(?:,\s*\d{4})?|\d{1,2}/\d{1,2}/\d{2,4})", clean):
        return False
    return True


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


def extract_listing_candidates(soup: BeautifulSoup) -> List[Dict[str, Any]]:
    """Extracts CivicPlus calendar event links and optional listing dates safely without nav pollution."""
    target_section = (
        soup.find("div", class_="calendarInnerWrapper")
        or soup.find("div", class_=re.compile(r"cpGrid|calendar", re.I))
        or soup
    )
    for element in target_section(["script", "style"]):
        element.decompose()

    candidates: List[Dict[str, Any]] = []
    seen_urls = set()

    event_items = target_section.find_all(
        ["li", "tr", "div"],
        class_=re.compile(r"calendarItem|cpGridRow|eventItem", re.I),
    )

    # Secondary safe CivicPlus fallback: only links explicitly pointing to Calendar EID pages
    if not event_items:
        eid_anchors = target_section.find_all(
            "a", href=re.compile(r"Calendar\.aspx\?.*EID=", re.I)
        )
        event_items = [a.find_parent(["li", "tr", "div"]) or a for a in eid_anchors]

    for event in event_items:
        anchors = (
            event.find_all("a", href=re.compile(r"EID=|/calendar", re.I))
            if event.name != "a"
            else [event]
        )
        if not anchors and event.name != "a":
            anchors = event.find_all("a", href=True)

        chosen_anchor = None
        title_text = ""
        for a in anchors:
            if not a.get("href"):
                continue
            cand_title = normalize_ws(a.get_text(" ", strip=True))
            if is_valid_event_title(cand_title):
                chosen_anchor = a
                title_text = cand_title
                break

        # If all anchors had generic text (e.g., "View Details"), check heading tags inside the card
        if not chosen_anchor and anchors:
            chosen_anchor = anchors[0]
            heading = event.find(
                ["h2", "h3", "h4", "strong", "span"],
                class_=re.compile(r"title|name|header", re.I),
            ) or event.find(["h2", "h3"])
            if heading:
                cand_title = normalize_ws(heading.get_text(" ", strip=True))
                if is_valid_event_title(cand_title):
                    title_text = cand_title

        if not chosen_anchor or not chosen_anchor.get("href"):
            continue

        ext_url = resolve_url(chosen_anchor["href"])
        if not ext_url or ext_url in seen_urls:
            continue
        seen_urls.add(ext_url)

        start_dates: List[str] = []
        date_tag = event.find(
            ["h4", "h3", "span", "td", "div"],
            class_=re.compile(r"date|calendarDate|dateHeader", re.I),
        ) or event.find("h4")
        if date_tag:
            start_dates = parse_event_dates(
                normalize_ws(date_tag.get_text(" ", strip=True))
            )

        candidates.append(
            {
                "title": title_text,
                "external_url": ext_url,
                "start_time": start_dates,
            }
        )

    return candidates


def parse_detail_html(
    html: str,
    fallback_title: str,
    fallback_dates: List[str],
    detail_url: str,
) -> Optional[Dict[str, Any]]:
    """Parses CivicPlus event detail page HTML into a structured event record."""
    det_soup = BeautifulSoup(clean_html_source(html), "html.parser")
    if not det_soup:
        return None

    # 1. JSON-LD check if embedded on CivicPlus detail page
    jsonld_title: Optional[str] = None
    jsonld_dates: List[str] = []
    jsonld_desc: Optional[str] = None
    jsonld_image: Optional[str] = None
    jsonld_venue: Optional[str] = None
    jsonld_addr: Optional[str] = None

    for script in det_soup.find_all("script", type="application/ld+json"):
        raw = script.string or script.get_text()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except Exception:
            continue
        items = data if isinstance(data, list) else data.get("@graph", [data])
        for obj in items:
            if isinstance(obj, dict) and "Event" in str(obj.get("@type") or ""):
                cand_jtitle = normalize_ws(str(obj.get("name") or ""))
                if is_valid_event_title(cand_jtitle):
                    jsonld_title = cand_jtitle
                s_raw = str(obj.get("startDate") or "").strip()
                e_raw = str(obj.get("endDate") or "").strip()
                if s_raw:
                    s_dates = parse_event_dates(s_raw)
                    e_dates = parse_event_dates(e_raw) if e_raw else []
                    if s_dates and e_dates and e_dates[-1] > s_dates[0]:
                        jsonld_dates = sorted(set([s_dates[0], e_dates[-1]]))
                    else:
                        jsonld_dates = s_dates
                jsonld_desc = normalize_ws(str(obj.get("description") or "")) or None
                img = obj.get("image")
                if isinstance(img, list) and img:
                    img = img[0]
                elif isinstance(img, dict):
                    img = img.get("url")
                jsonld_image = resolve_url(img)
                loc = obj.get("location")
                if isinstance(loc, dict):
                    jsonld_venue = normalize_ws(str(loc.get("name") or "")) or None
                    addr_obj = loc.get("address")
                    if isinstance(addr_obj, dict):
                        parts = [
                            normalize_ws(str(addr_obj.get("streetAddress") or "")),
                            normalize_ws(str(addr_obj.get("addressLocality") or "")),
                            f"{normalize_ws(str(addr_obj.get('addressRegion') or 'WA'))} {normalize_ws(str(addr_obj.get('postalCode') or ''))}".strip(),
                        ]
                        clean_parts = [p for p in parts if p]
                        if clean_parts:
                            jsonld_addr = ", ".join(clean_parts)
                break

    # Resolve specific event title (CivicPlus <h1> is the module banner "Calendar", so check specific selectors first)
    title = jsonld_title or ""

    if not is_valid_event_title(title):
        for el in det_soup.find_all(attrs={"itemprop": "name"}):
            if el.find_parent(attrs={"itemprop": "location"}):
                continue
            cand = normalize_ws(el.get_text(" ", strip=True))
            if is_valid_event_title(cand):
                title = cand
                break

    if not is_valid_event_title(title):
        detail_selectors = [
            "h2.calendarTitle",
            "h3.calendarTitle",
            ".calendarTitle",
            ".eventTitle",
            "#divEventDetails h2",
            "#divEventDetails h3",
            ".cpContent h2",
            ".cpContent h3",
            "h2",
            "h1",
        ]
        for sel in detail_selectors:
            for el in det_soup.select(sel):
                cand = normalize_ws(el.get_text(" ", strip=True))
                if is_valid_event_title(cand):
                    title = cand
                    break
            if is_valid_event_title(title):
                break

    if not is_valid_event_title(title) and det_soup.title:
        page_title = normalize_ws(det_soup.title.get_text(" ", strip=True))
        # Strip trailing "| City of University Place, WA" or "- Official Website"
        head_part = re.split(r"\s*[|\-–—]\s*", page_title)[0].strip()
        if is_valid_event_title(head_part):
            title = head_part

    if not is_valid_event_title(title):
        title = fallback_title

    # 2. Dates (JSON-LD -> microdata itemprop -> detail date classes -> listing date)
    dates = jsonld_dates
    if not dates:
        sd_el = det_soup.find(attrs={"itemprop": "startDate"})
        ed_el = det_soup.find(attrs={"itemprop": "endDate"})
        if sd_el:
            s_val = normalize_ws(sd_el.get("content") or sd_el.get_text(" ", strip=True))
            e_val = (
                normalize_ws(ed_el.get("content") or ed_el.get_text(" ", strip=True))
                if ed_el
                else ""
            )
            s_dates = parse_event_dates(s_val)
            e_dates = parse_event_dates(e_val) if e_val else []
            if s_dates and e_dates and e_dates[-1] > s_dates[0]:
                dates = sorted(set([s_dates[0], e_dates[-1]]))
            else:
                dates = s_dates

    if not dates:
        date_el = det_soup.find(
            ["div", "span", "td"],
            class_=re.compile(r"eventDate|calendarDate|date", re.I),
        )
        if date_el:
            dates = parse_event_dates(
                normalize_ws(date_el.get_text(" ", strip=True))
            )

    if not dates:
        dates = fallback_dates

    if not is_valid_event_title(title) or not dates:
        return None

    # 3. Venue & Address
    venue_name = jsonld_venue or DEFAULT_VENUE
    address = jsonld_addr or DEFAULT_ADDRESS

    addr_soup = det_soup.find(attrs={"itemprop": "location"}) or det_soup.find(
        "div", class_=re.compile(r"eventLocation|location|address", re.I)
    )
    if addr_soup:
        v_tag = addr_soup.find(attrs={"itemprop": "name"})
        if v_tag and normalize_ws(v_tag.get_text()):
            venue_name = normalize_ws(v_tag.get_text())

        street_el = addr_soup.find(attrs={"itemprop": "streetAddress"})
        city_el = addr_soup.find(attrs={"itemprop": "addressLocality"})
        zip_el = addr_soup.find(attrs={"itemprop": "postalCode"})
        if street_el and normalize_ws(street_el.get_text()):
            st = normalize_ws(street_el.get_text())
            ct = normalize_ws(city_el.get_text()) if city_el else "University Place"
            zp = normalize_ws(zip_el.get_text()) if zip_el else ""
            address = f"{st}, {ct}, WA {zp}".strip()
        elif not jsonld_addr:
            raw_loc = normalize_ws(addr_soup.get_text(", ", strip=True))
            if raw_loc and len(raw_loc) > 5:
                address = raw_loc

    # 4. Description
    desc_container = det_soup.find(attrs={"itemprop": "description"}) or det_soup.find(
        "div", class_=re.compile(r"eventDescription|cpContent", re.I)
    )
    big_desc = jsonld_desc or ""
    short_desc = ""
    if desc_container:
        rich_text = fetch_event_text(desc_container)
        extracted = (
            normalize_ws(rich_text)
            if rich_text
            else normalize_ws(desc_container.get_text(" ", strip=True))
        )
        if len(extracted) > len(big_desc):
            big_desc = extracted
        p_tags = desc_container.find_all("p")
        for p in p_tags:
            txt = normalize_ws(p.get_text(" ", strip=True))
            if txt:
                short_desc = txt
                break

    if not big_desc:
        big_desc = title
    if not short_desc:
        short_desc = big_desc[:250]

    # 5. Image (prefer og:image or content image over site header logo)
    image_url = jsonld_image
    if not image_url:
        og_img = det_soup.find("meta", property="og:image")
        if og_img and og_img.get("content"):
            image_url = resolve_url(og_img["content"])
    if not image_url and desc_container:
        img_tag = desc_container.find("img", src=True)
        if img_tag and img_tag.get("src"):
            image_url = resolve_url(img_tag["src"])

    return {
        "venue_name": venue_name,
        "address": address,
        "title": title,
        "start_time": dates,
        "description": short_desc,
        "big_desc": big_desc,
        "external_url": detail_url,
        "image_url": image_url,
        "ticket_link": detail_url,
    }


def fetch_detail_via_http(
    session: requests.Session, candidate: Dict[str, Any]
) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]], bool]:
    detail_url = candidate["external_url"]
    try:
        resp = session.get(detail_url, timeout=15)
        resp.raise_for_status()
        if len(resp.text) < 500:
            return candidate, None, False
        rec = parse_detail_html(
            resp.text,
            candidate["title"],
            candidate.get("start_time") or [],
            detail_url,
        )
        return candidate, rec, True
    except Exception:
        return candidate, None, False


def scraper() -> pd.DataFrame:
    """Scrapes City of University Place events via HTTP-first listing + concurrent detail fetching."""
    logger.info(f"[START] Scraping City of University Place: {TARGET_URL}")
    session = build_http_session(pool_size=12)

    disp_ctx = None
    active_disp = None
    driver = None
    candidates: List[Dict[str, Any]] = []

    def ensure_browser():
        nonlocal disp_ctx, active_disp, driver
        if disp_ctx is None:
            disp_ctx = virtual_display(size=(1920, 1080))
            active_disp = disp_ctx.__enter__()
        if driver is None:
            driver = create_stealth_driver(headless=(active_disp is None))
        return driver

    try:
        # 1. HTTP-first calendar fetch
        for list_url in (TARGET_URL, ALT_CALENDAR_URL):
            try:
                resp = session.get(list_url, timeout=15)
                resp.raise_for_status()
                soup_http = BeautifulSoup(
                    clean_html_source(resp.text), "html.parser"
                )
                found = extract_listing_candidates(soup_http)
                if found:
                    candidates = found
                    logger.info(
                        f"[HTTP FAST-PATH] Extracted {len(candidates)} candidate events from {list_url}."
                    )
                    break
            except Exception as http_err:
                logger.warning(f"[HTTP LISTING] Failed {list_url} ({http_err}).")

        # 2. Cross-platform browser fallback if HTTP returned 0 candidates
        if not candidates:
            from selenium.webdriver.common.by import By
            from selenium.webdriver.support import expected_conditions as EC
            from selenium.webdriver.support.ui import WebDriverWait

            logger.info(
                "[HTTP LISTING] 0 static candidates; launching browser fallback..."
            )
            drv = ensure_browser()
            for list_url in (TARGET_URL, ALT_CALENDAR_URL):
                drv.get(list_url)
                try:
                    WebDriverWait(drv, 10).until(
                        EC.presence_of_element_located(
                            (
                                By.CSS_SELECTOR,
                                "div.calendarInnerWrapper, .cpGrid, a[href*='EID=']",
                            )
                        )
                    )
                except Exception:
                    pass
                time.sleep(2)
                soup_browser = BeautifulSoup(
                    clean_html_source(drv.page_source), "html.parser"
                )
                found = extract_listing_candidates(soup_browser)
                if found:
                    candidates = found
                    logger.info(
                        f"[CHROME LISTING] Extracted {len(candidates)} candidate events from {list_url}."
                    )
                    break

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

        if not candidates:
            logger.warning(
                "[PARSER WARNING] Found 0 City of University Place calendar events."
            )
            return pd.DataFrame()

        # 3. Concurrent HTTP detail page traversal
        logger.info(
            f"Batch-fetching {len(candidates)} City of UP detail pages via HTTP (workers=10)..."
        )
        parsed_records: List[Dict[str, Any]] = []
        fallback_candidates: List[Dict[str, Any]] = []

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [
                executor.submit(fetch_detail_via_http, session, cand)
                for cand in candidates
            ]
            for fut in as_completed(futures):
                cand, rec, ok = fut.result()
                if ok:
                    if rec:
                        parsed_records.append(rec)
                else:
                    fallback_candidates.append(cand)

        if fallback_candidates:
            drv = ensure_browser()
            for cand in fallback_candidates:
                try:
                    drv.get(cand["external_url"])
                    time.sleep(1.5)
                    rec = parse_detail_html(
                        drv.page_source,
                        cand["title"],
                        cand.get("start_time") or [],
                        cand["external_url"],
                    )
                    if rec:
                        parsed_records.append(rec)
                except Exception as fb_err:
                    logger.warning(
                        f"[FALLBACK WARN] Failed {cand['external_url']}: {fb_err}"
                    )

        # 4. Consolidate recurring dates by normalized URL / title
        grouped: Dict[str, Dict[str, Any]] = {}
        for rec in parsed_records:
            base_url = re.sub(
                r"&?OccurrenceDate=[^&]+", "", rec["external_url"], flags=re.I
            )
            key = f"{base_url.lower()}::{rec['title'].lower()}"
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
            f"[FATAL ERROR] Exception encountered during scraping loop: {e}",
            exc_info=True,
        )
        return pd.DataFrame()
    finally:
        session.close()
        if driver:
            logger.info("[CLEANUP] Closing Chrome driver instance...")
            try:
                driver.quit()
            except Exception:
                pass
        if disp_ctx is not None:
            logger.info("[CLEANUP] Stopping virtual display context...")
            try:
                disp_ctx.__exit__(None, None, None)
            except Exception:
                pass


if __name__ == "__main__":
    logger.info("Executing City of University Place scraper task...")
    raw_df = scraper()

    if not raw_df.empty:
        pipeline = VividaIngestionPipeline(table_name="events")
        success = pipeline.run_standalone_pipeline(raw_df)
        if success:
            logger.info(
                "[PIPELINE SUCCESS] City of UP scraper execution completed successfully."
            )
        else:
            logger.error(
                "[PIPELINE ERROR] City of UP scraper pipeline execution reported errors."
            )
    else:
        logger.warning("[EXIT] No events scraped. Exiting task.")