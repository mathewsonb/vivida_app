"""
vivida_etl_helpers.py
Consolidated ETL pipeline helpers aligned with Supabase schema & headless execution.
"""
from datetime import datetime
from functools import lru_cache
import hashlib
import json
import re
from typing import Any, List, Optional, Tuple

from bs4 import BeautifulSoup
from dateutil.relativedelta import relativedelta
import pandas as pd
import requests
from selenium.webdriver.chrome.options import Options

# --- 1. HEADLESS CHROME CONFIGURATION ---


def get_headless_chrome_options() -> Options:
    """Returns Chrome options optimized for headless Xvfb / GitHub Actions environments."""
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-blink-features=AutomationControlled")
    return options


# --- 2. HTML & SCRAPER HELPERS ---


def clean_html_source(raw_html: str) -> str:
    """Removes Vue/Angular hydration comment tags and unwraps HTML comments."""
    if not raw_html:
        return ""
    cleaned = re.sub(r"<!\s*--\s*-->", "", raw_html)
    cleaned = re.sub(r"<!--\s*([\s\S]*?)\s*-->", r"\1", cleaned)
    return cleaned


def fetch_event_text(soup: BeautifulSoup) -> Optional[str]:
    """Strips layout script clutter and extracts clean main event descriptions."""
    if not soup:
        return None
    try:
        for element in soup(["script", "style", "nav", "header", "footer", "aside", "svg", "iframe"]):
            element.decompose()

        text = soup.get_text(separator="\n", strip=True)
        patterns = [
            r"Cookie Policy.*",
            r"All rights reserved.*",
            r"Subscribe to our newsletter.*",
            r"Skip to content",
        ]
        for pattern in patterns:
            text = re.sub(pattern, "", text, flags=re.IGNORECASE)
        return text.strip()
    except Exception as e:
        print(f"[Parser Warning] Failed to clean event text: {e}")
        return None


# --- 3. GEOCODING & CLEANING HELPERS ---


@lru_cache(maxsize=1024)
def get_census_coordinates(address_string: str) -> Tuple[Optional[float], Optional[float]]:
    """Geocodes an address string to (Latitude, Longitude) with LRU memory caching."""
    if not address_string or pd.isna(address_string):
        return (None, None)

    url = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"
    params = {
        "address": address_string,
        "benchmark": "Public_AR_Current",
        "format": "json",
    }

    try:
        response = requests.get(url, params=params, timeout=10)
        if response.status_code == 200:
            matches = response.json().get("result", {}).get("addressMatches", [])
            if matches:
                coords = matches[0]["coordinates"]
                return (coords["y"], coords["x"])
    except Exception:
        pass

    return (None, None)


# --- 4. IDENTIFIERS & DETERMINISTIC HASHING ---


def clean_str(val: Any) -> str:
    """Trim, lowercase, and collapse whitespace for unique keys."""
    if not val or pd.isna(val):
        return ""
    return re.sub(r"\s+", " ", str(val).strip().lower())


def generate_event_id(title: str, venue_name: str, start_time: Any, address: str = "") -> str:
    """Generates a deterministic 24-character SHA-256 event ID."""
    norm_title = clean_str(title)
    norm_venue = clean_str(venue_name)
    norm_addr = clean_str(address)

    dates_list = []
    if isinstance(start_time, list):
        for item in start_time:
            if isinstance(item, dict):
                dates_list.append(str(item.get("start_time", "")))
            else:
                dates_list.append(str(item))
    elif isinstance(start_time, str) and start_time:
        if start_time.startswith("["):
            try:
                parsed = json.loads(start_time)
                dates_list = [
                    d.get("start_time", "") if isinstance(d, dict) else str(d)
                    for d in parsed
                ]
            except Exception:
                dates_list = [start_time]
        else:
            dates_list = [start_time]

    cleaned_dates = sorted([clean_str(d) for d in dates_list if clean_str(d)])
    composite_key = f"{norm_title}|{norm_venue}|{','.join(cleaned_dates)}|{norm_addr}"
    hash_digest = hashlib.sha256(composite_key.encode("utf-8")).hexdigest()
    return f"evt_{hash_digest[:24]}"


def _infer_year(month_str: str, day_str: str, default_year: Optional[int] = None) -> Optional[pd.Timestamp]:
    """Parses a month + day without an explicit year, rolling forward if >60 days in the past."""
    now = datetime.now()
    yr = default_year or now.year
    dt = pd.to_datetime(f"{month_str} {day_str}, {yr}", errors="coerce")
    if pd.isna(dt):
        return None
    if default_year is None and (now - dt.to_pydatetime()).days > 60:
        dt = pd.to_datetime(
            f"{month_str} {day_str}, {yr + 1}", errors="coerce")
    return dt


def expand_recurring_dates(
    start_date_str: str,
    frequency: Optional[str] = None,
    max_iterations: int = 10,
) -> List[str]:
    """
    Expands a starting YYYY-MM-DD date forward by 'daily', 'weekly', 'biweekly', or 'monthly'
    for at most `max_iterations` occurrences.
    """
    base_dt = pd.to_datetime(start_date_str, errors="coerce")
    if pd.isna(base_dt):
        return []

    start_iso = base_dt.strftime("%Y-%m-%d")
    if not frequency:
        return [start_iso]

    freq_norm = str(frequency).strip().lower()
    if "daily" in freq_norm or "every day" in freq_norm:
        step = relativedelta(days=1)
    elif "biweekly" in freq_norm or "bi-weekly" in freq_norm or "every other week" in freq_norm:
        step = relativedelta(weeks=2)
    elif "weekly" in freq_norm or "every week" in freq_norm:
        step = relativedelta(weeks=1)
    elif "monthly" in freq_norm or "every month" in freq_norm:
        step = relativedelta(months=1)
    else:
        return [start_iso]

    return [
        (base_dt + step * i).strftime("%Y-%m-%d")
        for i in range(max_iterations)
    ]


def parse_event_dates(
    raw_date_str: Any,
    frequency: Optional[str] = None,
    max_iterations: int = 10,
    default_year: Optional[int] = None,
) -> List[str]:
    """
    Universal date parser enforcing Vivida's 3 scheduling rules (outputs flat YYYY-MM-DD strings):
      1. Dash/through/to ranges ('Dec 4 - 6, 2026', 'Nov 28 through Dec 2, 2026', '12/04/2026 - 12/06/2026'):
         Continuous run -> returns [start_date, end_date] endpoints ONLY (no interior expansion).
      2. Comma/ampersand-separated dates ('Dec 4, Dec 5, Dec 6, 2026' or 'Dec 4, 5 & 6, 2026'):
         Discrete dates -> returns every listed date individually.
      3. Recurrence frequency ('daily', 'weekly', 'biweekly', 'monthly'):
         Generates forward dates capped at `max_iterations` (default 10).
    """
    if raw_date_str is None or (not isinstance(raw_date_str, list) and pd.isna(raw_date_str)):
        return []

    if isinstance(raw_date_str, list):
        out: List[str] = []
        for item in raw_date_str:
            out.extend(
                parse_event_dates(
                    item,
                    frequency=frequency,
                    max_iterations=max_iterations,
                    default_year=default_year,
                )
            )
        return sorted(set(out))

    text = re.sub(r"\s+", " ", str(raw_date_str).replace("\xa0", " ")).strip()
    if not text:
        return []

    # Fast-path clean ISO date strings
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text) and not frequency:
        return [text]

    # Strip leading weekday names ("Fri, Dec 4, 2026" -> "Dec 4, 2026")
    text = re.sub(
        r"^(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*,?\s*", "", text, flags=re.IGNORECASE
    )

    # Detect inline frequency keywords if not passed explicitly
    if not frequency:
        freq_match = re.search(
            r"\b(daily|weekly|bi-?weekly|monthly)\b", text, flags=re.IGNORECASE
        )
        if freq_match:
            frequency = freq_match.group(1)

    range_sep = r"(?:[-–—]|\bthrough\b|\bthru\b|\bto\b)"

    # Rule 1A: Numeric Slash Range -> "12/04/2026 - 12/06/2026"
    m = re.search(
        rf"(\d{{1,2}}/\d{{1,2}}/\d{{2,4}})\s*{range_sep}\s*(\d{{1,2}}/\d{{1,2}}/\d{{2,4}})",
        text,
        flags=re.IGNORECASE,
    )
    if m:
        d1, d2 = pd.to_datetime(m.group(1), errors="coerce"), pd.to_datetime(
            m.group(2), errors="coerce")
        if pd.notnull(d1) and pd.notnull(d2):
            return sorted(set([d1.strftime("%Y-%m-%d"), d2.strftime("%Y-%m-%d")]))

    # Rule 1B: Cross-Year Month Range -> "Dec 28, 2026 - Jan 3, 2027"
    m = re.search(
        rf"([A-Za-z]+\s+\d{{1,2}},?\s*\d{{4}})\s*{range_sep}\s*([A-Za-z]+\s+\d{{1,2}},?\s*\d{{4}})",
        text,
        flags=re.IGNORECASE,
    )
    if m:
        d1, d2 = pd.to_datetime(m.group(1), errors="coerce"), pd.to_datetime(
            m.group(2), errors="coerce")
        if pd.notnull(d1) and pd.notnull(d2):
            return sorted(set([d1.strftime("%Y-%m-%d"), d2.strftime("%Y-%m-%d")]))

    # Rule 1C: Cross-Month Range -> "Nov 28 - Dec 2, 2026" (with optional year)
    m = re.search(
        rf"([A-Za-z]+)\s+(\d{{1,2}})\s*{range_sep}\s*([A-Za-z]+)\s+(\d{{1,2}})(?:,?\s*(\d{{4}}))?",
        text,
        flags=re.IGNORECASE,
    )
    if m:
        m1, d1, m2, d2, yr = m.groups()
        if yr:
            dt1 = pd.to_datetime(f"{m1} {d1}, {yr}", errors="coerce")
            dt2 = pd.to_datetime(f"{m2} {d2}, {yr}", errors="coerce")
        else:
            dt1 = _infer_year(m1, d1, default_year)
            dt2 = _infer_year(m2, d2, default_year)
            if dt1 is not None and dt2 is not None and dt2 < dt1:
                dt2 = dt2 + relativedelta(years=1)
        if dt1 is not None and dt2 is not None and pd.notnull(dt1) and pd.notnull(dt2):
            return sorted(set([dt1.strftime("%Y-%m-%d"), dt2.strftime("%Y-%m-%d")]))

    # Rule 1D: Intra-Month Range -> "Dec 4 - 6, 2026" (with optional year)
    m = re.search(
        rf"([A-Za-z]+)\s+(\d{{1,2}})\s*{range_sep}\s*(\d{{1,2}})(?:,?\s*(\d{{4}}))?",
        text,
        flags=re.IGNORECASE,
    )
    if m:
        mon, d1, d2, yr = m.groups()
        if yr:
            dt1 = pd.to_datetime(f"{mon} {d1}, {yr}", errors="coerce")
            dt2 = pd.to_datetime(f"{mon} {d2}, {yr}", errors="coerce")
        else:
            dt1 = _infer_year(mon, d1, default_year)
            dt2 = _infer_year(mon, d2, default_year)
        if dt1 is not None and dt2 is not None and pd.notnull(dt1) and pd.notnull(dt2):
            return sorted(set([dt1.strftime("%Y-%m-%d"), dt2.strftime("%Y-%m-%d")]))

    # Rule 2A: Same-Month Discrete Comma/Ampersand List -> "Dec 4, 5 & 6, 2026" (allows trailing times)
    m = re.search(
        r"\b([A-Za-z]+)\s+((?:\d{1,2}\s*(?:,|&|\band\b)\s*)+\d{1,2})(?:,?\s*(\d{4}))?",
        text,
        flags=re.IGNORECASE,
    )
    if m:
        mon, days_blob, yr = m.groups()
        days = re.findall(r"\d{1,2}", days_blob)
        parsed_days = []
        for day in days:
            dt = (
                pd.to_datetime(f"{mon} {day}, {yr}", errors="coerce")
                if yr
                else _infer_year(mon, day, default_year)
            )
            if dt is not None and pd.notnull(dt):
                parsed_days.append(dt.strftime("%Y-%m-%d"))
        if parsed_days:
            return sorted(set(parsed_days))

    # Rule 2B: Full Comma-Separated Discrete Dates -> "Dec 4, 2026, Dec 5, 2026"
    full_tokens = re.findall(
        r"(?:[A-Za-z]+\s+\d{1,2},?\s*\d{4}|\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4})",
        text,
    )
    if len(full_tokens) > 1:
        parsed_tokens = []
        for tok in full_tokens:
            dt = pd.to_datetime(tok, errors="coerce")
            if pd.notnull(dt):
                parsed_tokens.append(dt.strftime("%Y-%m-%d"))
        if parsed_tokens:
            return sorted(set(parsed_tokens))

    # Rule 3 / Single Date Fallback (+ optional frequency expansion up to max_iterations)
    dt_single = pd.to_datetime(text, errors="coerce", format="mixed")
    if pd.isna(dt_single):
        # Fallback for yearless single date like "Dec 4" or "Dec 4 at 7:00 PM"
        m_single = re.search(r"\b([A-Za-z]+)\s+(\d{1,2})\b", text)
        if m_single:
            dt_single = _infer_year(m_single.group(
                1), m_single.group(2), default_year)

    if dt_single is not None and pd.notnull(dt_single):
        return expand_recurring_dates(
            dt_single.strftime("%Y-%m-%d"),
            frequency=frequency,
            max_iterations=max_iterations,
        )

    return []
