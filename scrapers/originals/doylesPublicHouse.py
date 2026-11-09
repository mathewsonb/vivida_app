# import required libraries
import pandas as pd
from vivida_app.vivida_etl import fetch_rendered_html
import re

# define the function to scrape Doyle's Public House events
def scraper() -> pd.DataFrame:
    # set the link value
    link = "https://www.doylespublichouse.com/events"

    # get the soup using headless browser automation (dynamic site)
    soup = fetch_rendered_html(link, wait_for_selector="#section-5624347 > div > div > div > div")
    # print(f"Soup: {soup.prettify()}")  # Print the soup

    # subset the soup for only the sections of interest
    target_section = soup.find("div", class_="MuiGrid-root MuiGrid-item pm-calendar-event-section-wrapper MuiGrid-grid-xs-12")

    # strip out unnecessary scripts and styles
    for script in target_section(["script", "style"]):
        script.decompose()

    # extract all the event cards
    event_items = target_section.find_all("div", class_="pm-calendar-event pm-calendar-event-card")

    # create a dictionary to hold the event data
    event_dict = {}
    dph_df = pd.DataFrame()

    # parse each event to get name, link to details, date (range), description, image link, location, price, and ticket link (if available)
    for event in event_items: # set a limit for TEST
        # get the event title
        try:
            event_dict["title"] = event.find("h2").find("a").get_text(strip=True)
        except:
            event_dict["title"] = None

        # get the venue_name
        try:
            event_dict["venue_name"] = "Doyle's Public House"
        except:
            event_dict["venue_name"] = None

        # get the "from" date and format
        try:
            # transform the text dates to a list
            now_year = pd.Timestamp.now().year
            month = event.find("span", class_="calendar-event-month").get_text(strip=True)
            day = event.find("span", class_="calendar-event-date").get_text(strip=True)
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
            event_dict["description"] = event.find("div", class_="pm-calendar-event-content-right").get_text(strip=True, separator=" ")
        except:
            event_dict["description"] = None

        # get the link to the details page
        try:
            event_dict["external_url"] = "https://www.doylespublichouse.com" + \
                event.find("a").get("href").strip()
        except:
            event_dict["external_url"] = None

        # get the image link of the event
        try:
            event_dict["image_url"] = event.find("img").get("src").strip()
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
            event_dict["address"] = "208 St. Helens Ave South, Tacoma, WA 98402"
        except:
            event_dict["address"] = None

        # use async function to get expanded descriptions
        try:
            det_soup = fetch_rendered_html(event_dict["external_url"])
            event_dict["big_desc"] = det_soup.find("main", id="page-content").get_text(strip=True, separator=" ")
        except Exception as e:
            event_dict["big_desc"] = None

        # concatenate the event_dict to the dataframe
        dph_df = pd.concat([dph_df, pd.DataFrame([event_dict])], ignore_index=True)

    # reset the index
    dph_df = dph_df.reset_index(drop=True)

    # return the dataframe
    return dph_df