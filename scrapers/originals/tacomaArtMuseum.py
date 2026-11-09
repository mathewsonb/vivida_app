# import required libraries
import pandas as pd
from vivida_app.vivida_etl import fetch_rendered_html
import re

# define the scraper function for Tacoma Art Museum
def scraper() -> pd.Dataframe:
    # set the link value
    link = "https://www.tacomaartmuseum.org/events/"

    # get the soup using headless browser automation (dynamic site)
    soup = fetch_rendered_html(link, wait_for_selector="#pusher > div > div > div.template > div > div > ul:nth-child(2) > li:nth-child(1)")
    # print(f"Soup: {soup.prettify()}")  # Print the soup

    # subset the soup for only the sections of interest
    target_section = soup.find("div", class_="main_column")

    # strip out unnecessary scripts and styles
    for script in target_section(["script", "style"]):
        script.decompose()

    # extract all the event cards
    event_items = target_section.find_all("li")

    # create a dictionary to hold the event data
    event_dict = {}
    tam_df = pd.DataFrame()

    # parse each event to get name, link to details, date (range), description, image link, location, price, and ticket link (if available)
    for event in event_items:
        # get the event title
        try:
            title = event.find("a").find("div", class_="deets").get_text()
            title = re.sub(r"\s+", " ", title).strip()
            event_dict["title"] = title
        except:
            event_dict["title"] = None

        # get the venue_name
        try:
            event_dict["venue_name"] = "Tacoma Art Museum"
        except:
            event_dict["venue_name"] = None

        # get the "from" date and format
        try:
            # transform the text dates to a list
            now_year = pd.Timestamp.now().year
            day = event.find("a").find("div", class_="num").get_text()
            start_time = f"{day}, {now_year}"
            start_time = pd.to_datetime(start_time, errors='coerce', format='mixed')

            # check that the month is in the future, and if not, add 1 to the year
            if start_time.month < pd.Timestamp.now().month:
                start_time = f"{day}, {now_year + 1}"
                start_time = pd.to_datetime(start_time, errors='coerce', format='mixed')

            # set the final start_time in the dataframe
            event_dict["start_time"] = [start_time.strftime('%Y-%m-%d')]
        except:
            event_dict["start_time"] = None

        # get the link to the details page
        try:
            event_dict["external_url"] = event.find("a").get("href").strip()
        except:
            event_dict["external_url"] = None

        # add the location to the dataframe
        try:
            # use venue address
            event_dict["address"] = "1701 Pacific Avenue, Tacoma, WA 98402"
        except:
            event_dict["address"] = None

        # STEP INTO DETAILS
        # ==================
        # use async function to get expanded descriptions
        det_soup = fetch_rendered_html(event_dict["external_url"]).find("div", class_="rtecontent pad")

        # get full text for expanded description
        try:
            event_dict["big_desc"] = re.sub(r"\s+", " ", det_soup.get_text(strip=True, separator=" "))
        except Exception as e:
            event_dict["big_desc"] = None
            
        # batch extract primary details
        try:
            event_dict["description"] = det_soup.find("div", class_="tribe-events-single-event-description tribe-events-content entry-content description").find("h3").get_text(strip=True, separator=" ")
        except:
            event_dict["description"] = None

        # get the image link of the event
        try:
            event_dict["image_url"] = det_soup.find("div", class_="tribe-events-single-event-description tribe-events-content entry-content description").find("img").get("src").strip()
        except:
            event_dict["image_url"] = None
            
        # get the ticket link of the event (if available)
        try:
            event_dict["ticket_link"] = None # placeholder
        except:
            event_dict["ticket_link"] = None

        # concatenate the event_dict to the dataframe
        tam_df = pd.concat([tam_df, pd.DataFrame([event_dict])], ignore_index=True)

    # reset the index
    tam_df = tam_df.reset_index(drop=True)

    # return the dataframe
    return tam_df