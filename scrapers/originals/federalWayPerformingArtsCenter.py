# import required libraries
import pandas as pd
from vivida_app.vivida_etl import fetch_rendered_html

# define the scraper function for the Federal Way Performing Arts Center events page
def scraper() -> pd.DataFrame:
    # set the link value
    link = "https://fwpaec.org/event-calendar/"

    # get the soup using headless browser automation (dynamic site)
    soup = fetch_rendered_html(link, wait_for_selector="#eael-advance-tabs-40a2a53 > div.eael-tabs-content")

    # subset the soup for only the sections of interest
    target_section = soup.find("div", class_="eael-tabs-content")

    if target_section:
        # strip out unnecessary scripts and styles
        for script in target_section(["script", "style"]):
            script.decompose()

        # extract all the event cards safely
        calendar_tab = target_section.find("div", id="full-event-calendar-tab")
        event_items = calendar_tab.find_all("div", class_="col-md-3 col-sm-3") if calendar_tab else []

        # create a list to accumulate event dictionaries efficiently
        events_list = []

        # parse each event card
        for event in event_items:
            # Declare fresh dictionary per iteration to prevent state bleed
            event_dict = {}

            # 1. Title
            try:
                event_dict["title"] = event.find("h4").get_text(strip=True, separator=" ")
            except Exception:
                event_dict["title"] = None

            # 2. Venue Name
            try:
                event_dict["venue_name"] = event.find("div", class_="mec-event-loc-place").get_text(strip=True, separator=" ")
            except Exception:
                event_dict["venue_name"] = "Federal Way Performing Arts & Event Center"

            # 3. Start Time Calculation (Preserving your exact year-shifting logic)
            try:
                now = pd.Timestamp.now()
                start_time_str = event.find("span", class_="mec-start-date-label").get_text(strip=True)
                start_time_str = f"{start_time_str} {now.year}"

                parsed_dt = pd.to_datetime(start_time_str, errors='coerce', format='mixed')
                
                if pd.notna(parsed_dt):
                    if parsed_dt.month < now.month:
                        parsed_dt = parsed_dt + pd.DateOffset(years=1)
                    event_dict["start_time"] = [parsed_dt.strftime('%Y-%m-%d')]
                else:
                    event_dict["start_time"] = None
            except Exception:
                event_dict["start_time"] = None

            # 4. Primary Summary
            try:
                desc_elem = event.find("div", class_=re.compile(r"mec-event-description|excerpt|content"))
                event_dict["description"] = desc_elem.get_text(strip=True, separator=" ") if desc_elem else None
            except Exception:
                event_dict["description"] = None

            # 5. External URL
            try:
                event_dict["external_url"] = event.find("h4").find("a").get("href").strip()
            except Exception:
                event_dict["external_url"] = None

            # 6. Image URL (Checking data-src first, falling back to src)
            try:
                image_sec = event.find("div", class_="mec-event-image")
                img_tag = image_sec.find("img") if image_sec else None
                if img_tag:
                    event_dict["image_url"] = (img_tag.get("data-src") or img_tag.get("src") or "").strip() or None
                else:
                    event_dict["image_url"] = None
            except Exception:
                event_dict["image_url"] = None

            # 7. Ticket Link
            try:
                t_btn = event.find("a", class_=re.compile(r"ticket|booking|buy", re.I))
                event_dict["ticket_link"] = t_btn.get("href").strip() if t_btn else None
            except Exception:
                event_dict["ticket_link"] = None

            # 8. Address / Location
            try:
                event_dict["address"] = event.find("p", class_="mec-grid-event-location").get_text(strip=True)
            except Exception:
                event_dict["address"] = "31510 Pete von Reichbauer Way S, Federal Way, WA 98003"

            # 9. Resilient Deep Detail Fetching for NLP rich text (big_desc)
            if event_dict.get("external_url"):
                try:
                    det_res = fetch_rendered_html(event_dict["external_url"])
                    det_soup = BeautifulSoup(det_res, "html.parser") if isinstance(det_res, str) else det_res
                    
                    # Resilient multi-class lookup to ensure big_desc NEVER drops to None erroneously
                    container = (
                        det_soup.find("div", class_=re.compile(r"mec-single-event-description|mec-events-content")) or
                        det_soup.find("div", class_="entry-content") or
                        det_soup.find("main")
                    )

                    if container:
                        for block in container(["script", "style", "nav", "footer", "button"]):
                            block.decompose()
                        raw_text = container.get_text(strip=True, separator=" ")
                        event_dict["big_desc"] = re.sub(r"\s+", " ", raw_text) if raw_text else event_dict["description"]
                    else:
                        event_dict["big_desc"] = event_dict["description"]
                except Exception:
                    event_dict["big_desc"] = event_dict["description"]
            else:
                event_dict["big_desc"] = event_dict["description"]

            events_list.append(event_dict)

        fw_df = pd.DataFrame(events_list)
    else:
        fw_df = pd.DataFrame()

    fw_df = fw_df.reset_index(drop=True)

    # return the dataframe
    return fw_df