# import required libraries
import pandas as pd
from vivida_app.scraper.utils import fetch_rendered_html

# define the scraper function for the Tacoma Parks and Recreation events page
def scraper() -> pd.DataFrame:
    # set the link value
    link = "https://www.parkstacoma.gov/events/list/"

    # get the soup using headless browser automation (dynamic site)
    soup = fetch_rendered_html(link, wait_for_selector="#tribe-events-pg-template > div > section > ul")

    # subset the soup for only the sections of interest
    target_section = soup.find("ul", class_="tribe-events-calendar-list")

    # strip out unnecessary scripts and styles
    for script in target_section(["script", "style"]):
        script.decompose()

    # extract all the event cards
    event_items = target_section.find_all("div", "tribe-common-g-row tribe-events-calendar-list__event-row")

    # create a dictionary to hold the event data
    event_dict = {}
    tp_df = pd.DataFrame()

    # parse each event to get name, link to details, date (range), description, image link, location, price, and ticket link (if available)
    for event in event_items:
        # get the event title
        try:
            event_dict["title"] = event.find("h4").get_text(strip=True, separator=" ")
        except:
            event_dict["title"] = None

        # get the venue_name
        try:
            event_dict["venue_name"] = event.find("span", class_="tribe-events-calendar-list__event-venue-title tribe-common-b2--bold").get_text(strip=True, separator=" ")
        except:
            event_dict["venue_name"] = None

        # get the "from" date and format
        try:
            from_date = event.find("span", class_="tribe-event-date-start").get_text(strip=True)
            from_date = from_date.split(",")[1].strip() + " " + from_date.split(",")[2].strip()[:4]
            from_date = pd.to_datetime(from_date, errors='coerce', format='mixed').strftime('%Y-%m-%d')
        except:
            from_date = None

        # get the "to" date and format
        try:
            to_date = event.find("span", class_="tribe-event-date-end").get_text(strip=True)
            to_date = to_date.split(",")[1].strip() + " " + to_date.split(",")[2].strip()[:4]
            to_date = pd.to_datetime(to_date, errors='coerce', format='mixed').strftime('%Y-%m-%d')
        except:
            to_date = None

        # concatenate the final "start_time" depending on whether there is a "to" date
        event_dict["start_time"] = [d for d in [from_date, to_date] if pd.notnull(d)]

        # batch extract primary details
        try:
            event_dict["description"] = event.find("div", class_="tribe-events-calendar-list__event-description tribe-common-b2 tribe-common-a11y-hidden").get_text(strip=True, separator=" ")
        except:
            event_dict["description"] = None

        # get the link to the details page
        try:
            event_dict["external_url"] = event.find("h4").find("a").get("href").strip()
        except:
            event_dict["external_url"] = None

        # get the image link of the event
        try:
            event_dict["image_url"] = event.find("img").get("src").strip()
        except:
            event_dict["image_url"] = None
            
        # get the ticket link of the event (if available)
        try:
            event_dict["ticket_link"] = event.find("N%^&") # placeholder
        except:
            event_dict["ticket_link"] = None

        # add the location to the dataframe
        try:
            event_dict["address"] = event.find("span", class_="tribe-events-calendar-list__event-venue-address").get_text(strip=True)
        except:
            event_dict["address"] = None

        # use async function to get expanded descriptions
        try:
            det_soup = fetch_rendered_html(event_dict["external_url"])
            event_dict["big_desc"] = re.sub(r"\s+", " ", det_soup.find("section", class_="event-details").get_text(strip=True, separator=" "))
        except:
            event_dict["big_desc"] = None

        # concatenate the event_dict to the dataframe
        tp_df = pd.concat([tp_df, pd.DataFrame([event_dict])], ignore_index=True)

    # reset the index
    tp_df = tp_df.reset_index(drop=True)

    # return the dataframe
    return tp_df