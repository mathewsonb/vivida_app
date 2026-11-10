"""
vivida_etl_helpers.py
Consolidated ETL pipeline helpers aligned with Supabase schema & headless execution.
"""

from functools import lru_cache
import hashlib
import json
import re
from typing import Dict, List, Optional, Tuple, Any

from bs4 import BeautifulSoup
import numpy as np
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
    options.add_argument("--remote-debugging-port=9222")
    options.add_argument("--disable-dev-tools")
    options.add_argument("--no-first-run")
    options.add_argument("--no-default-browser-check")
    options.add_argument("--ignore-certificate-errors")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
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
            
        text = soup.get_text(separator='\n', strip=True)
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
    params = {"address": address_string, "benchmark": "Public_AR_Current", "format": "json"}
    
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
    return re.sub(r'\s+', ' ', str(val).strip().lower())


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
        if start_time.startswith('['):
            try:
                parsed = json.loads(start_time)
                dates_list = [d.get("start_time", "") if isinstance(d, dict) else str(d) for d in parsed]
            except Exception:
                dates_list = [start_time]
        else:
            dates_list = [start_time]

    cleaned_dates = sorted([clean_str(d) for d in dates_list if clean_str(d)])
    composite_key = f"{norm_title}|{norm_venue}|{','.join(cleaned_dates)}|{norm_addr}"
    hash_digest = hashlib.sha256(composite_key.encode('utf-8')).hexdigest()
    return f"evt_{hash_digest[:24]}"