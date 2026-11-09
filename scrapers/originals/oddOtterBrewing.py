# import required libraries
import pandas as pd
import requests

# define the function to scrape the Odd Otter Brewing events
def scrape() -> pd.DataFrame:
    # use the calendar api to get all events for the next 30 days, and then parse the json to extract the relevant details
    # =============================
    # set start/end dates for the next 30 days, and set the limit to 250 events
    start_date = pd.Timestamp.now().strftime('%Y-%m-%d')
    end_date = (pd.Timestamp.now() + pd.Timedelta(days=30)).strftime('%Y-%m-%d')
    limit = 250

    # set api key for the calendar api
    api_key = "AIzaSyDOtGM5jr8bNp1utVpG2_gSRH03RNGBkI8"

    # set the link value
    link = f"https://clients6.google.com/calendar/v3/calendars/oddotterevents%40gmail.com/events?calendarId=oddotterevents%40gmail.com&singleEvents=true&eventTypes=default&eventTypes=focusTime&eventTypes=outOfOffice&timeZone=America%2FLos_Angeles&maxAttendees=1&maxResults={limit}&sanitizeHtml=true&timeMin={start_date}T00%3A00%3A00%2B18%3A00&timeMax={end_date}T00%3A00%3A00-18%3A00&key={api_key}&%24unique=gc237"

    # get the json payload from the api with a simple request
    payload = requests.get(url=link).json()

    # create a dictionary to hold the event data
    oo_df = pd.DataFrame()

    # loop through each event in the json payload and extract the relevant details
    for i, event in enumerate(payload.get("items", [])):
        # get the event title
        try:
            oo_df.loc[i, "title"] = event["summary"].strip()
        except:
            oo_df.loc[i, "title"] = None

        # get the venue_name
        try:
            oo_df.loc[i, "venue_name"] = "Odd Otter Brewing Company"
        except:
            oo_df.loc[i, "venue_name"] = None

        # get the "from" date and format
        try:
            # set the final start_time in the dataframe
            start_date = pd.to_datetime(event["start"]["dateTime"], errors='coerce', format='mixed')
            end_date = pd.to_datetime(event["end"]["dateTime"], errors='coerce', format='mixed')

            if start_date.date() == end_date.date():
                oo_df.loc[i, "start_time"] = [start_date.strftime('%Y-%m-%d')]
            else:
                oo_df.loc[i, "start_time"] = [start_date.strftime('%Y-%m-%d'), end_date.strftime('%Y-%m-%d')]
        except:
            oo_df.loc[i, "start_time"] = None

        # get the link to the details page
        try:
            oo_df.loc[i, "external_url"] = event.get("htmlLink", "").strip()
        except:
            oo_df.loc[i, "external_url"] = None

        # add the location to the dataframe
        try:
            # use venue address
            oo_df.loc[i, "address"] = "716 Pacific Ave, Tacoma, WA 98402"
        except:
            oo_df.loc[i, "address"] = None
            
        # batch extract primary details
        try:
            oo_df.loc[i, "description"] = event["summary"].strip()
        except:
            oo_df.loc[i, "description"] = None
            
        # get full text for expanded description
        try:
            oo_df.loc[i, "big_desc"] = oo_df.loc[i, "description"] # placeholder for now, as the Spar events do not have a separate expanded description
        except Exception as e:
            oo_df.loc[i, "big_desc"] = None

        # get the image link of the event
        try:
            oo_df.loc[i, "image_url"] = "https://d1w7312wesee68.cloudfront.net/Ow1NOZqxUuq1KeCZrrP0XFscbwjDFH6cmZejMaLWjQM/ext:webp/quality:85/preset:xxl/plain/s3://toast-sites-resources-prod/restaurantImages/c1a91c11-32d5-405a-a174-d6f05e89a0bb/a4375b5c04414b1886bd27233ee15b75.jpg" # use a fixed image for all Spar events until the webpage is updated
        except:
            oo_df.loc[i, "image_url"] = None
            
        # get the ticket link of the event (if available)
        try:
            oo_df.loc[i, "ticket_link"] = None # placeholder
        except:
            oo_df.loc[i, "ticket_link"] = None

    # reset the index
    oo_df = oo_df.reset_index(drop=True)

    # return the dataframe
    return oo_df