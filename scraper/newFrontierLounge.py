# import required libraries
import pandas as pd
from vivida_app.scraper.utils import fetch_rendered_html
import re

# define the function to scrape New Frontier Lounge events
def scraper() -> pd.DataFrame:
    # set the link value
    link = "https://www.newfrontierlounge.com/calendar#/events"

    # get the soup using headless browser automation (dynamic site)
    soup = fetch_rendered_html(link, wait_for_selector="#widgetElementId1 > div > div > div > div:nth-child(1)")
    # print(f"Soup: {soup.prettify()}")  # Print the soup

    # subset the soup for only the sections of interest
    target_section = soup.find("div", class_="vp-events-container")

    # strip out unnecessary scripts and styles
    for script in target_section(["script", "style"]):
        script.decompose()

    # extract all the event cards
    event_items = target_section.find_all("div", class_="vp-event-card vp-venue-newfrontierlounge vp-col")

    # create a dictionary to hold the event data
    event_dict = {}
    nf_df = pd.DataFrame()

    # parse each event to get name, link to details, date (range), description, image link, location, price, and ticket link (if available)
    for event in event_items:
        # get the event title
        try:
            event_dict["title"] = event.find("div", class_="vp-event-name").get_text(strip=True)
        except:
            event_dict["title"] = None

        # get the venue_name
        try:
            event_dict["venue_name"] = "New Frontier Lounge"
        except:
            event_dict["venue_name"] = None

        # get the "from" date and format
        try:
            # transform the text dates to a list
            now_year = pd.Timestamp.now().year
            raw_date = event.find("span", class_="vp-date").get_text(strip=True)
            month = raw_date.split(" ")[1].strip()
            day = raw_date.split(" ")[2].strip()
            start_time = f"{month} {day}, {now_year}"
            start_time = pd.to_datetime(start_time, errors='coerce', format='mixed').strftime('%Y-%m-%d')

            # check that the month is in the future, and if not, add 1 to the year
            if pd.to_datetime(start_time).month < pd.Timestamp.now().month:
                start_time = f"{month} {day}, {now_year + 1}"
                start_time = pd.to_datetime(start_time, errors='coerce', format='mixed').strftime('%Y-%m-%d')

            # set the final start_time in the dataframe
            event_dict["start_time"] = [start_time]
        except:
            event_dict["start_time"] = None
            
        # batch extract primary details
        try:
            event_dict["description"] = event.find("div", class_="vp-details").get_text(strip=True, separator=" ")
        except:
            event_dict["description"] = None

        # get the link to the details page
        try:
            event_dict["external_url"] = "https://www.newfrontierlounge.com/calendar" + \
                event.find("a", class_="vp-event-link").get("href").strip()
        except:
            event_dict["external_url"] = None

        # get the image link of the event
        try:
            event_dict["image_url"] = event.find("div", class_="vp-cover-img").get("style").split("url(\"")[1].strip().split("\")")[0]
        except:
            event_dict["image_url"] = None
            
        # get the ticket link of the event (if available)
        try:
            event_dict["ticket_link"] = None # placeholder
        except:
            event_dict["ticket_link"] = None

        # add the location to the dataframe
        try:
            # use venue address
            event_dict["address"] = "301 E 25th Street, Tacoma, WA 98421"
        except:
            event_dict["address"] = None

        # use async function to get expanded descriptions
        try:
            det_soup = fetch_rendered_html(event_dict["external_url"])
            event_dict["big_desc"] = re.sub(r"\s+", " ", det_soup.find("div", class_="vp-event-listing vp-venue-newfrontierlounge vp-event-details-modal").get_text(strip=True, separator=" "))
        except:
            event_dict["big_desc"] = None

        # concatenate the event_dict to the dataframe
        nf_df = pd.concat([nf_df, pd.DataFrame([event_dict])], ignore_index=True)

    # reset the index
    nf_df = nf_df.reset_index(drop=True)

    # return the dataframe
    return nf_df