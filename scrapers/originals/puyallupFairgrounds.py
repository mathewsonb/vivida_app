# import libraries
import pandas as pd
from vivida_app.vivida_etl import fetch_rendered_html, fetch_event_text


# define the scraper function for the Puyallup Fairgrounds events page
def scraper() -> pd.DataFrame:
    # set the link value
    link = "https://www.thefair.com/events-calendar/"

    # set the venue location as the default location for the events
    location = "110 9th Ave SW, Puyallup, WA 98371"

    # create a list to accumulate all events efficiently
    all_events = []

    # iterate through pages dynamically until no event cards are found
    page = 1
    while True:
        try:
            soup = fetch_rendered_html(url=link + f"?page={page}")
        except Exception as e:
            print(f"Error fetching page {page}: {e}")
            break

        target_table = soup.find("div", class_="container container--md2")
        
        # If the target container or event list is missing, stop pagination
        if not target_table:
            break

        event_items = target_table.find_all("div", class_="event-card-large")
        if not event_items:
            break  # Exit loop when a page yields 0 events

        for event in event_items:
            # Fresh dict declaration per iteration to prevent value leakage
            event_dict = {}

            # 1. Event Title & External Link
            name_info = event.find("h3")
            try:
                event_dict["title"] = name_info.get_text(strip=True, separator=" ")
            except Exception:
                event_dict["title"] = None

            try:
                href = name_info.find("a")["href"].strip()
                event_dict["external_url"] = "https://www.thefair.com" + href if href.startswith("/") else href
            except Exception:
                event_dict["external_url"] = None

            # 2. Date Range Text
            try:
                event_dict["start_time"] = event.find("div", class_="icon-text").get_text(strip=True)
            except Exception:
                event_dict["start_time"] = None

            # 3. Image URL
            try:
                event_dict["image_url"] = event.find("img").get("src")
            except Exception:
                event_dict["image_url"] = None

            # 4. Description Summary
            try:
                event_dict["description"] = event.find("span").get_text(strip=True, separator=" ")
            except Exception:
                event_dict["description"] = None

            # 5. Ticket Link from Card
            try:
                ticket_a = event.find("div", class_="event-card-large__buttons").find("a", href=re.compile(r"ticket", re.I))
                event_dict["ticket_link"] = ticket_a.get("href").strip()
            except Exception:
                event_dict["ticket_link"] = None

            # 6. Deep Render (Single call for big_desc and fallback ticket_link)
            if event_dict.get("external_url"):
                try:
                    det_html = fetch_rendered_html(url=event_dict["external_url"])
                    det_soup = BeautifulSoup(det_html, 'html.parser') if not isinstance(det_html, BeautifulSoup) else det_html
                    
                    # Extract big description using custom helper
                    raw_text = fetch_event_text(det_soup)
                    event_dict["big_desc"] = re.sub(r"\s+", " ", raw_text) if raw_text else event_dict["description"]

                    # Fallback ticket link extraction from the ALREADY rendered detail page
                    if not event_dict["ticket_link"]:
                        try:
                            t_btn = det_soup.find("a", href=re.compile(r"ticket", re.I))
                            event_dict["ticket_link"] = t_btn.get("href").strip() if t_btn else None
                        except Exception:
                            pass
                except Exception:
                    event_dict["big_desc"] = event_dict["description"]
            else:
                event_dict["big_desc"] = event_dict["description"]

            all_events.append(event_dict)

        page += 1

    # Construct final DataFrame once
    pfg_df = pd.DataFrame(all_events)

    if not pfg_df.empty:
        pfg_df["address"] = location
        pfg_df["venue_name"] = "Puyallup Fairgrounds"

        # Clean date parsing: Extract first date string safely before datetime conversion
        pfg_df["clean_date"] = pd.to_datetime(
            pfg_df["start_time"].str.split("-|to").str[0], errors="coerce"
        )

        # Sort safely by title and clean date
        pfg_df = pfg_df.sort_values(by=["title", "clean_date"])

        # Safe date formatting helper for groupby aggregation
        def format_date_range(x):
            valid = x.dropna()
            if valid.empty:
                return None
            min_d, max_d = pd.to_datetime(valid.min(), errors='coerce', format='mixed'), pd.to_datetime(valid.max(), errors='coerce', format='mixed')
            if min_d == max_d:
                return [min_d.strftime("%Y-%m-%d")]
            return [min_d.strftime("%Y-%m-%d"), max_d.strftime("%Y-%m-%d")]

        # Groupby aggregation
        pfg_df = pfg_df.groupby("title", as_index=False).agg({
            "start_time": format_date_range,
            "external_url": "first",
            "image_url": "first",
            "ticket_link": "first",
            "description": "first",
            "address": "first",
            "big_desc": "first",
            "venue_name": "first"
        })

    # Return the final DataFrame
    return pfg_df