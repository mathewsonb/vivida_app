# import required libraries
import pandas as pd
from vivida_app.scraper.utils import fetch_rendered_html

# define the function to scrape The Valley events
def scraper() -> pd.DataFrame:
    # set the link value (root url for the Valley events page)
    link = "https://www.thevalleytacoma.com/new-events-1"

    # get the soup using headless browser automation (dynamic site)
    soup = fetch_rendered_html(link, wait_for_selector="#content")

    # subset the soup for only the sections of interest
    target_section = soup.find("div", class_="eventlist eventlist--upcoming")

    # strip out unnecessary scripts and styles
    for script in target_section(["script", "style"]):
        script.decompose()

    # extract all the event cards
    event_items = target_section.find_all("article", class_="eventlist-event eventlist-event--upcoming")

    # create a dictionary to hold the event data
    event_dict = {}
    val_df = pd.DataFrame()

    # parse each event to get name, link to details, date (range), description, image link, location, price, and ticket link (if available)
    for event in event_items:
        # get the event title
        try:
            event_dict["title"] = event.find("h1").find("a").get_text(strip=True)
        except:
            event_dict["title"] = None

        # get the venue_name
        try:
            event_dict["venue_name"] = "The Valley"
        except:
            event_dict["venue_name"] = None

        # get the "from" date and format
        try:
            # transform the text dates to a list
            event_dict["start_time"] = [event.find("time", class_="event-date").get("datetime").strip()]
        except:
            event_dict["start_time"] = None
            
        # batch extract primary details
        try:
            event_dict["description"] = event.find("div", class_="sqs-html-content").get_text(strip=True, separator=" ")
        except:
            event_dict["description"] = None

        # get the link to the details page
        try:
            event_dict["external_url"] = "https://www.thevalleytacoma.com" + \
                event.find("h1", class_="eventlist-title").find("a").get("href").strip()
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
            addr_url = event.find("li", class_="eventlist-meta-item eventlist-meta-address event-meta-item").find("a").get("href").strip()
            # use regex to extract the address from the URL
            event_dict["address"] = addr_url.split("q=")[1].strip()
        except:
            event_dict["address"] = None

        # use async function to get expanded descriptions
        try:
            det_soup = fetch_rendered_html(event_dict["external_url"])
            event_dict["big_desc"] = re.sub(r"\s+", " ", det_soup.find("article", class_="eventitem").get_text(strip=True, separator=" "))
        except Exception as e:
            event_dict["big_desc"] = None

        # concatenate the event_dict to the dataframe
        val_df = pd.concat([val_df, pd.DataFrame([event_dict])], ignore_index=True)

    # reset the index
    val_df = val_df.reset_index(drop=True)

    # return the dataframe
    return val_df