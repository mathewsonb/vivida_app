# import required libraries
import pandas as pd
from vivida_app.scraper.utils import fetch_rendered_html
import re

# define the scraper function for the Point Defiance Zoo & Aquarium events page
def scraper() -> pd.DataFrame:
    # set the link value
    link = "https://www.pdza.org/events/"

    # get the soup using headless browser automation (dynamic site)
    soup = fetch_rendered_html(link, wait_for_selector="#tribe-events-content")

    # subset the soup for only the sections of interest
    target_section = soup.find("div", class_="tribe-events-list list")

    # strip out unnecessary scripts and styles
    for script in target_section(["script", "style"]):
        script.decompose()

    # extract all the event cards
    event_items = target_section.find_all("div", class_="event-card event-card--list")

    # create a dictionary to hold the event data
    event_dict = {}
    pdz_df = pd.DataFrame()

    # parse each event to get name, link to details, date (range), description, image link, location, price, and ticket link (if available)
    for event in event_items:
        # get the event title
        try:
            event_dict["title"] = event.find("a", class_="event-card__title").get_text(strip=True, separator=" ")
        except:
            event_dict["title"] = None

        # get the venue_name
        try:
            event_dict["venue_name"] = "Point Defiance Zoo & Aquarium"
        except:
            event_dict["venue_name"] = None

        # get the "from" date and format
        try:
            # transform the text date so that if the month is before the current month, assign this year + 1
            now = pd.Timestamp.now()
            start_time = event.find("div", class_="event-card__date").get_text(strip=True)[:5]
            start_time = start_time + f", {now.year}"

            # get the month in the date
            start_time = pd.to_datetime(start_time, errors='coerce', format='mixed')
            start_time_month = start_time.month

            # if the current month comes after the date month, shift year forward
            if start_time_month<now.month:
                start_time = start_time + pd.DateOffset(years=1)
                start_time = start_time

            # assign the start date variable in the dataframe
            event_dict["start_time"] = [start_time.strftime('%Y-%m-%d')]
        except:
            event_dict["start_time"] = None

        # batch extract primary details
        try:
            event_dict["description"] = event.find("div", class_="event-card__description").get_text(strip=True, separator=" ")
        except:
            event_dict["description"] = None

        # get the link to the details page
        try:
            event_dict["external_url"] = event.find("a", class_="link-arrow text-btn").get("href").strip()
        except:
            event_dict["external_url"] = None

        # get the image link of the event
        try:
            event_dict["image_url"] = event.find("a", class_="event-card__image").get("style").split("url(")[1].split(")")[0].strip()
        except:
            event_dict["image_url"] = None
            
        # get the ticket link of the event (if available)
        try:
            event_dict["ticket_link"] = None # placeholder
        except:
            event_dict["ticket_link"] = None

        # add the location to the dataframe
        try:
            event_dict["address"] = "5400 N Pearl St, Tacoma, WA 98407"
        except:
            event_dict["address"] = None

        # use async function to get expanded descriptions
        try:
            det_soup = fetch_rendered_html(event_dict["external_url"])
            event_dict["big_desc"] = re.sub(r"\s+", " ", det_soup.find("section", class_="section single-event section--seaglass").get_text(strip=True, separator=" "))
        except Exception as e:
            event_dict["big_desc"] = None

        # concatenate the event_dict to the dataframe
        pdz_df = pd.concat([pdz_df, pd.DataFrame([event_dict])], ignore_index=True)

    # reset the index
    pdz_df = pdz_df.reset_index(drop=True)

    # return the dataframe
    return pdz_df