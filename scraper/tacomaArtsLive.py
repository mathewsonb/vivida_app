# import required libraries
import pandas as pd
from vivida_app.scraper.utils import fetch_rendered_html
import re

# define the scraper for Tacoma Arts Live
def scraper() -> pd.DataFrame:
    # try a simple soup extraction over the events page
    link = "https://www.tacomaartslive.org/upcoming-events/"

    # get the soup, allowing all possible events to load
    soup = fetch_rendered_html(url=link, wait_for_selector="#mec_skin_events_mec1")

    # subset the soup for only the sections of interest
    target_section = soup.find("div", class_="mec-skin-grid-events-container")

    # strip out unnecessary scripts and styles
    for script in target_section(["script", "style"]):
        script.decompose()

    # extract all the event cards
    event_items = target_section.find_all("div", class_="col-md-4 col-sm-4")

    # create a dictionary to hold the event data
    event_dict = {}
    tal_df = pd.DataFrame()

    # parse each event to get name, link to details, date (range), description, image link, location, price, and ticket link (if available)
    for event in event_items:
        # get the event title
        try:
            event_dict["title"] = event.find("h4").find("a").get_text(strip=True)
        except:
            event_dict["title"] = None

        # get the venue_name
        try:
            event_dict["venue_name"] = event.find("p", class_="mec-grid-event-location").get_text(strip=True).split(",")[0].strip()
        except:
            event_dict["venue_name"] = None

        # get the "from" date and format
        try:
            # set the final start_time in the dataframe
            start_time = pd.to_datetime(
                event.find("span", class_="mec-start-date-label").get_text(strip=True),
                errors='coerce',
                format='mixed'
                )
            event_dict["start_time"] = [start_time.strftime('%Y-%m-%d')]
        except:
            event_dict["start_time"] = None

        # get the link to the details page
        try:
            event_dict["external_url"] = event.find("h4").find("a").get("href").strip()
        except:
            event_dict["external_url"] = None

        # add the location to the dataframe
        try:
            # use venue address
            event_dict["address"] = ", ".join(event.find("p", class_="mec-grid-event-location").get_text(strip=True).split(",")[1:]).strip()
        except:
            event_dict["address"] = None

        # =======================================
        # get full text for expanded description
        det_soup = fetch_rendered_html(event_dict["external_url"])
        try:
            event_dict["big_desc"] = re.sub(r"\s+", " ", det_soup.find("div", class_="elementor elementor-9545").get_text(strip=True, separator=" "))
        except:
            event_dict["big_desc"] = None
            
        # batch extract primary details
        try:
            event_dict["description"] = det_soup.find("div", class_=re.compile(r"elementor-widget__width-initial elementor-widget elementor-widget-text-editor?")).find("div", class_="elementor-widget-container").get_text(strip=True, separator=" ") # changes in the class value are event IDs, which is why we use REGEX
        except:
            event_dict["description"] = None

        # get the image link of the event
        try:
            event_dict["image_url"] = det_soup.find_all("img", decoding="async").get("src").strip()
        except:
            event_dict["image_url"] = None
            
        # get the ticket link of the event (if available)
        try:
            event_dict["ticket_link"] = det_soup.find("div", class_=re.compile(r"elementor-widget elementor-widget-html?")).find("div", class_="elementor-widget-container").find("noscript").find("a").get("href").strip()
            # print(event_dict["ticket_link"])
        except:
            event_dict["ticket_link"] = None

        # concatenate the event_dict to the dataframe
        tal_df = pd.concat([tal_df, pd.DataFrame([event_dict])], ignore_index=True)

    # reset the index
    tal_df = tal_df.reset_index(drop=True)

    # return the dataframe
    return tal_df