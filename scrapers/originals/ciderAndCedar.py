# import required libraries
import pandas as pd
from vivida_app.vivida_etl import fetch_rendered_html

# create a scraper function for Cider and Cedar
def scraper() -> pd.DataFrame:
    # set the link value
    link = "https://www.ciderandcedar.com/events"

    # get the soup using headless browser automation (dynamic site)
    soup = fetch_rendered_html(link, wait_for_selector="#page-regions > section > section.page-section.has-section-divider.user-items-list-section.full-bleed-section.bright-inverse > div.content-wrapper > div > div")
    # print(f"Soup: {soup.prettify()}")  # Print the soup

    # subset the soup for only the sections of interest
    target_section = soup.find("div", class_="user-items-list")

    # strip out unnecessary scripts and styles
    for script in target_section(["script", "style"]):
        script.decompose()

    # extract all the event cards
    event_items = target_section.find_all("li")

    # create a dictionary to hold the event data
    event_dict = {}
    cnc_df = pd.DataFrame()

    # parse each event to get name, link to details, date (range), description, image link, location, price, and ticket link (if available)
    for event in event_items:
        # get the event title
        try:
            event_dict["title"] = event.find("h2").get_text(strip=True)
        except:
            event_dict["title"] = None

        # get the venue_name
        try:
            event_dict["venue_name"] = "Cider & Cedar"
        except:
            event_dict["venue_name"] = None

        # get list of blocks containing date and description
        date_desc_sec = event.find_all("p") # Date = [0], Desc = [1]

        # get the "from" date and format
        try:
            # transform the text dates to a list
            now_year = pd.Timestamp.now().year
            date = " ".join(date_desc_sec[0].get_text(strip=True).split(" ")[:-1])
            start_time = f"{date}, {now_year}"
            start_time = pd.to_datetime(start_time, errors='coerce', format='mixed')

            # check that the month is in the future, and if not, add 1 to the year
            if start_time.month < pd.Timestamp.now().month:
                start_time = start_time + pd.DateOffset(years=1)
                start_time = pd.to_datetime(start_time, errors='coerce', format='mixed')

            # set the final start_time in the dataframe
            event_dict["start_time"] = [start_time.strftime('%Y-%m-%d')]
        except:
            event_dict["start_time"] = None

        # get the link to the details page
        event_dict["external_url"] = link

        # add the location to the dataframe
        try:
            # use venue address
            event_dict["address"] = "744 Market St UNIT 102B, Tacoma, WA 98402"
        except:
            event_dict["address"] = None
            
        # batch extract primary details
        try:
            event_dict["description"] = re.sub(r"\s+", " ", date_desc_sec[1].get_text(strip=True))
        except:
            event_dict["description"] = None

        # get full text for expanded description
        event_dict["big_desc"] = event_dict["description"]

        # get the image link of the event
        try:
            event_dict["image_url"] = "https://images.squarespace-cdn.com/content/v1/679bb91621194f0e31b3c085/54ecdaa4-d251-44d5-b175-1b48f87aae9d/peacesignc%26c.png?format=1500w" # use a fixted image for all Spar events until the webpage is updated
        except:
            event_dict["image_url"] = None
            
        # get the ticket link of the event (if available)
        try:
            event_dict["ticket_link"] = None # placeholder
        except:
            event_dict["ticket_link"] = None

        # concatenate the event_dict to the dataframe
        cnc_df = pd.concat([cnc_df, pd.DataFrame([event_dict])], ignore_index=True)

    # reset the index
    cnc_df = cnc_df.reset_index(drop=True)

    # return the dataframe
    return cnc_df