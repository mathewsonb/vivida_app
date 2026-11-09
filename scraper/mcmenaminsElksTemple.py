# import required libraries
import pandas as pd
from vivida_app.scraper.utils import fetch_rendered_html

# define the scraper function for the McMenamins Elks Temple events page
def scraper() -> pd.DataFrame:
    # set the link value
    link = "https://www.mcmenamins.com/elks-temple/spanish-ballroom"

    # get the soup using headless browser automation (dynamic site)
    soup = fetch_rendered_html(link, wait_for_selector="#full_calendar")

    # subset the soup for only the sections of interest
    target_section = soup.find("div", id="full_calendar")

    # strip out unnecessary scripts and styles
    for script in target_section(["script", "style"]):
        script.decompose()

    # extract all the event cards
    event_items = target_section.find_all("div", class_=re.compile(r"^calendar-item"))

    # create a dictionary to hold the event data
    event_dict = {}
    mcm_df = pd.DataFrame()

    # parse each event to get name, link to details, date (range), description, image link, location, price, and ticket link (if available)
    for event in event_items[:5]: # set a limit for TEST
        # get the event title
        try:
            event_dict["title"] = event.find("h3").get_text(strip=True, separator=" ")
        except:
            event_dict["title"] = None

        # get the venue_name
        try:
            event_dict["venue_name"] = "McMenamins Elks Temple"
        except:
            event_dict["venue_name"] = None

        # get the "from" date and format
        try:
            # transform the text date
            start_time = event.find("p", class_="uk-panel-time uk-text-truncate").get_text(strip=True)
            start_time = start_time.split(",")[1].strip()
            start_time = pd.to_datetime(start_time, errors='coerce', format='mixed')

            # assign the start date variable in the dataframe
            event_dict["start_time"] = [start_time.strftime('%Y-%m-%d')]
        except:
            event_dict["start_time"] = None

        # batch extract primary details
        try:
            event_dict["description"] = re.sub(r"\s+", " ", event.find("button", class_="uk-button uk-button-primary uk-button-details").get_text(strip=True, separator=" "))
        except:
            event_dict["description"] = None

        # get the link to the details page
        try:
            event_dict["external_url"] = "https://www.mcmenamins.com" + event.find("a").get("href").strip()
        except:
            event_dict["external_url"] = None

        # get the image link of the event
        try:
            event_dict["image_url"] = event.find("div", class_="uk-panel").get("style").split("url('")[1].split("')")[0].strip()
        except:
            event_dict["image_url"] = None
            
        # get the ticket link of the event (if available)
        try:
            event_dict["ticket_link"] = event.find("button", class_="uk-button uk-button-primary").get("onclick").split("open('")[1].split("')")[0].strip() # placeholder
        except:
            event_dict["ticket_link"] = None

        # add the location to the dataframe
        try:
            event_dict["address"] = "565 Broadway, Tacoma, WA 98402"
        except:
            event_dict["address"] = None

        # use async function to get expanded descriptions
        try:
            det_soup = fetch_rendered_html(event_dict["external_url"])
            event_dict["big_desc"] = re.sub(r"\s+", " ", det_soup.find("div", class_="uk-width-1-1 uk-width-xlarge-7-10").get_text(strip=True, separator=" "))
        except Exception as e:
            event_dict["big_desc"] = None

        # concatenate the event_dict to the dataframe
        mcm_df = pd.concat([mcm_df, pd.DataFrame([event_dict])], ignore_index=True)

    # reset the index
    mcm_df = mcm_df.reset_index(drop=True)

    # return the dataframe
    return mcm_df