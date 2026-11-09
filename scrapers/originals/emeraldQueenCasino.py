# import required libraries
import pandas as pd
import requests
from bs4 import BeautifulSoup
from vivida_app.vivida_etl import fetch_rendered_html

# define the scraper function for the Emerald Queen Casino events page
def scraper() -> pd.DataFrame:    
# wrap the scrape in error handling, since their site is currently down
    try:
        # set the head url as the link value
        link = "https://emeraldqueen.com/tickets/"

        # set the venue location as the default location for the events
        location = "2920 E R St, Tacoma, WA 98404"

        # get the HTML content of the page
        response = requests.get(link)
        soup = BeautifulSoup(response.content, 'html.parser')

        # filter to only the section of interest, which is the table containing the data we want to extract
        target_table = soup.find("section", class_="content page-925 moto-section") # IMPORTANT: this is the div that contains the table we want to extract

        # find all divs with the class that matches the pattern
        event_items = target_table.find_all("div", class_="moto-widget moto-widget-row moto-spacing-top-medium moto-spacing-right-auto moto-spacing-bottom-medium moto-spacing-left-auto")

        # create a dictionary to hold the event data
        event_dict = {}
        eqc_df = pd.DataFrame()

        # parse each event to get name, link to details, date (range), description, image link, location, price, and ticket link (if available)
        for event in event_items:
            # batch extract primary details
            try:
                details = event.find_all("div", class_="moto-widget-text-content moto-widget-text-editable")
                # extract the name, date range, and description from the details
                event_dict["title"] = details[0].get_text(strip=True)
                event_dict["start_time"] = details[1].get_text(strip=True)
                event_dict["description"] = details[2].get_text(strip=True, separator=" ")
            except:
                event_dict["title"] = None
                event_dict["start_time"] = None
                event_dict["description"] = None

            # get the link to the details page
            try:
                event_dict["external_url"] = "https://emeraldqueen.com" + event.find("a")["href"]
            except:
                event_dict["external_url"] = None

            # get the image link of the event
            try:
                event_dict["image_url"] = "https://emeraldqueen.com" + event.find("img").get("data-src")
            except:
                event_dict["image_url"] = None

            # follow the details link to get richer details
            det_soup = fetch_rendered_html(event_dict["external_url"], wait_for_selector="#section-content")
                
            # get the ticket link of the event (if available)
            try:
                event_dict["ticket_link"] = det_soup.find("a", href=re.compile(r"ticketmaster", re.IGNORECASE)).get("href")
            except:
                event_dict["ticket_link"] = None
                
            # use async function to get expanded descriptions
            try:
                event_dict["big_desc"] = re.sub(r"\s+", " ", det_soup.get_text(strip=True, separator=" "))
            except Exception as e:
                event_dict["big_desc"] = None

            # concatenate the event_dict to the dataframe
            eqc_df = pd.concat([eqc_df, pd.DataFrame([event_dict])], ignore_index=True)

        # add the location and location link to the dataframe (invariant for all events at the Tacoma Dome)
        eqc_df["address"] = location
        eqc_df["venue_name"] = "Emerald Queen Casino"

        # format the data as a datetime object
        eqc_df["start_time"] = [[x.strftime('%Y-%m-%d')] for x in pd.to_datetime(eqc_df["start_time"].str.replace('•', ''), format='mixed', errors='coerce')]

        # reset the index
        eqc_df = eqc_df.reset_index(drop=True)
    except Exception as e:
        print(f"EQC scrape failed with error code {e}")

    # return the dataframe
    return eqc_df