# import required libraries
import requests
import pandas as pd
from bs4 import BeautifulSoup
import json

# define wrapper function to scrape Tacoma Comedy Club events using JSON-LD
def scrape() -> pd.DataFrame:
    # use the fact that the JSON-LD is exposed to get all basic details
    def clean_html_to_text(html_content):
        """Strips HTML tags from the JSON-LD description string."""
        if not html_content:
            return ""
        soup = BeautifulSoup(html_content, "html.parser")
        return soup.get_text(separator=" ", strip=True)

    # parse the appropriate venue address based on keywords if needed
    def parse_address_from_description(raw_description_html):
        """
        Parses the exact location from the description HTML.
        Defaults based on keywords if exact address parsing fails.
        """
        if "3829 6th Ave" in raw_description_html or "6th &" in raw_description_html or "6th and" in raw_description_html:
            return "3829 6th Ave, Tacoma, WA 98406"
        return "933 Market Street, Tacoma, WA 98402"

    # parse the embedded JSON object containing all basic info in one pass
    def scrape_tcc_json_ld():
        url = "https://www.tacomacomedyclub.com/events"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

        # 1. Direct HTTP GET request (No headless browser required!)
        response = requests.get(url, headers=headers)
        if response.status_code != 200:
            raise RuntimeError(f"Failed to fetch page: {response.status_code}")

        # 2. Locate the JSON-LD script block using BeautifulSoup
        soup = BeautifulSoup(response.text, "html.parser")
        json_ld_script = soup.find("script", type="application/ld+json")

        if not json_ld_script:
            raise ValueError("JSON-LD script block not found on page!")

        # 3. Load structured JSON object
        data = json.loads(json_ld_script.string)
        events_raw = data.get("Events", [])

        # 4. Group events by title & location to collect all start_time show dates
        grouped_events = {}

        for evt in events_raw:
            title = evt.get("name", "").strip()
            raw_desc = evt.get("description", "")
            clean_desc = clean_html_to_text(raw_desc)
            address = parse_address_from_description(raw_desc)
            image_url = evt.get("image")
            ticket_url = evt.get("url")

            # Standardize ISO start date to YYYY-MM-DD
            iso_start = evt.get("startDate", "")
            start_date = pd.to_datetime(iso_start, errors="coerce").strftime("%Y-%m-%d")

            # Grouping key to coalesce multi-day showtimes into a single event entry
            group_key = (title, address)

            if group_key not in grouped_events:
                grouped_events[group_key] = {
                    "title": title,
                    "venue_name": "Tacoma Comedy Club",
                    "address": address,
                    "description": clean_desc,
                    "big_desc": clean_desc,
                    "image_url": image_url,
                    "external_url": ticket_url,
                    "start_time": []
                }

            # Append start_date if valid and not already added
            if start_date and start_date != "NaT" and start_date not in grouped_events[group_key]["start_time"]:
                grouped_events[group_key]["start_time"].append(start_date)

        # 5. Build final list & generate deterministic IDs
        final_events = []
        for item in grouped_events.values():
            # Sort dates chronologically for consistent hashing
            item["start_time"].sort()
            final_events.append(item)

        return pd.DataFrame(final_events)

    # Execute
    tcc_df = scrape_tcc_json_ld()

    return tcc_df