# import required libraries
import re
import requests
import pandas as pd

# define the function to scrape The Proctor District events
def scraper() -> pd.DataFrame:
    # this corpus is available via an API, so we will get it all at once, and iterate over the result to extract our data
    tpd_json = requests.get(url="https://base44.app/api/apps/6911cf52c9f849cb61177089/entities/Event?sort=event_date&limit=500").json()

    # create a dictionary to hold the event data
    event_dict = {}
    tpd_df = pd.DataFrame()

    # here we iterate over the JSON of dictionaries, extracting desired event info for each entry
    for i, record in enumerate(tpd_json):
        # first, get the date, to ensure we only collect for future dates
        start_time = pd.to_datetime(
            record["event_date"],
            errors='coerce',
            format='mixed',
            utc=True)

        # check that the date is future
        if start_time >= pd.Timestamp.now(tz='UTC'):
            # get the "from" date and format
            try:
                event_dict["start_time"] = [start_time.strftime('%Y-%m-%d')]
            except:
                event_dict["start_time"] = None

            # get the event title
            try:
                event_dict["title"] = record["title"]
            except:
                event_dict["title"] = None

            # get the venue_name
            try:
                event_dict["venue_name"] = record["business_name"]
            except:
                event_dict["venue_name"] = None
                
            # batch extract primary details
            try:
                event_dict["description"] = re.sub(r"\s+", " ", record["description"])
            except:
                event_dict["description"] = None

            # get the link to the details page
            try:
                event_dict["external_url"] = record["event_website"]
            except:
                event_dict["external_url"] = None

            # get the image link of the event
            try:
                event_dict["image_url"] = record["image_url"]
            except:
                event_dict["image_url"] = None
                
            # get the ticket link of the event (if available)
            try:
                event_dict["ticket_link"] = record["registration_url"]
            except:
                event_dict["ticket_link"] = None

            # add the location to the dataframe
            try:
                # use venue address
                location = record["location"].split("—")[-1].strip()
                event_dict["address"] = location
            except:
                event_dict["address"] = None

            # get expanded descriptions
            try:
                event_dict["big_desc"] = event_dict["description"]
            except Exception as e:
                event_dict["big_desc"] = None

            # concatenate the event_dict to the dataframe
            tpd_df = pd.concat([tpd_df, pd.DataFrame([event_dict])], ignore_index=True)

    # reset the index
    tpd_df = tpd_df.reset_index(drop=True)

    # return the dataframe
    return tpd_df