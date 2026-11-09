import pandas as pd
import requests
from bs4 import BeautifulSoup
import re
from vivida_app.scraper.utils import fetch_event_text

# define the scraper function for the Tacoma Dome events page
def scraper() -> pd.DataFrame:
    # set the head url as the link value
    link = "https://www.tacomadome.org/events"

    # set the venue location as the default location for the events
    location = "2727 E D St, Tacoma, WA 98421"

    # get the HTML content of the page
    response = requests.get(link)
    soup = BeautifulSoup(response.content, 'html.parser')

    # filter to only the section of interest, which is the table containing the data we want to extract
    target_table = soup.find("div", class_="full_column non-widget-area") # IMPORTANT: this is the div that contains the table we want to extract

    # strip out unnecessary scripts and styles
    for script in target_table(["script", "style"]):
        script.decompose()

    # find all blocks where the class has the pattern r"eventItem
    pat = re.compile(r"^eventItem.*clearfix$")

    # find all divs with the class that matches the pattern
    event_items = target_table.find_all(class_=pat)

    # create a dictionary to hold the event data
    event_dict = {}
    td_df = pd.DataFrame()

    # parse each event to get name, link to details, date (range), description, image link, location, price, and ticket link (if available)
    for event in event_items:
        # get the name of the event
        try:
            event_dict["title"] = event.find("h3").get_text(strip=True, separator=" ")
        except AttributeError:
            event_dict["title"] = None

        # get the link to the details page
        try:
            event_dict["external_url"] = event.find("a")["href"]
        except AttributeError:
            event_dict["external_url"] = None

        # get the date range of the event
        try:
            event_dict["start_time"] = event.find("div", class_="date").get("aria-label").strip()
        except AttributeError:
            event_dict["start_time"] = None

        # get the description of the event
        try:
            event_dict["description"] = event.find("h4").get_text(strip=True, separator=" ")
        except AttributeError:
            event_dict["description"] = None

        # get the image link of the event
        try:
            event_dict["image_url"] = event.find("div", class_="background-img").get("style") 
        except AttributeError:
            event_dict["image_url"] = None
            
        # get the ticket link of the event (if available)
        try:
            event_dict["ticket_link"] = event.find("a", class_="tickets onsalenow").get("href").strip()
        except AttributeError:
            event_dict["ticket_link"] = None

        # use async function to get expanded descriptions
        try:
            response = requests.get(event_dict["external_url"])
            event_dict["big_desc"] = re.sub(r"\s+", " ", fetch_event_text(BeautifulSoup(response.content, 'html.parser')))
        except Exception as e:
            event_dict["big_desc"] = None

        # concatenate the event_dict to the dataframe
        td_df = pd.concat([td_df, pd.DataFrame([event_dict])], ignore_index=True)

    # add the location and location link to the dataframe (invariant for all events at the Tacoma Dome)
    td_df["address"] = location
    td_df["venue_name"] = "Tacoma Dome"

    # reset the index
    td_df = td_df.reset_index(drop=True)

    # strip away the decorator characters from the TD image url
    td_df["image_url"] = td_df["image_url"].apply(lambda x : str(x)[22:-2])

    # convert start_time to arrays of dt objects
    td_df[["first_day", "last_day"]] = (
        td_df["start_time"].str.split(" to ", expand=True).apply(lambda col: col.str.strip())
    )

    # Extract year from last_day (\b\d{4}\b matches exact 4-digit year boundaries)
    extracted_year = td_df["last_day"].str.extract(r"(\b\d{4}\b)", expand=False)

    # Boolean mask: has first_day, missing a year in first_day, and has a year in last_day
    needs_year = (
        extracted_year.notna()
    )

    # Apply update in-place
    td_df.loc[needs_year, "first_day"] = (
        td_df.loc[needs_year, "first_day"].str.strip() + " " + extracted_year[needs_year]
    )

    # Parse clean pd.Timestamp columns now that both have YYYY
    td_df["first_day"] = pd.to_datetime(td_df["first_day"], errors="coerce")
    td_df["last_day"]  = pd.to_datetime(td_df["last_day"], errors="coerce")

    # Helper function to safely format datetime objects or handle NaT/None
    def format_dt(val):
        return val.strftime('%Y-%m-%d') if pd.notna(val) else None

    # Corrected list comprehension with ternary 'if/else' BEFORE the 'for' loop
    td_df["start_time"] = [
        [format_dt(x), format_dt(y)] if pd.notna(y) else [format_dt(x)]
        for x, y in zip(td_df["first_day"], td_df["last_day"])
    ]

    # drop the helper columns
    td_df = td_df.drop(columns=["first_day", "last_day"])

    # return the dataframe
    return td_df