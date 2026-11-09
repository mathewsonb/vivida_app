# import required libraries
import pandas as pd
import requests

# define the scraper function for The Spar Tavern
def scraper() -> pd.Dataframe:
    # use the calendar api to get all events for the next 30 days, and then parse the json to extract the relevant details
    # =============================
    # set start/end dates for the next 30 days, and set the limit to 250 events
    start_date = pd.Timestamp.now().strftime('%Y-%m-%d')
    end_date = (pd.Timestamp.now() + pd.Timedelta(days=30)).strftime('%Y-%m-%d')
    limit = 250

    # set api key for the calendar api
    api_key = "AIzaSyDOtGM5jr8bNp1utVpG2_gSRH03RNGBkI8"

    # set the link value
    link = f"https://clients6.google.com/calendar/v3/calendars/1c3f32a7ad4284a3bbc1d990d9e1fc58ac1229629b3c07cf3bf2ed2a3d2600b0%40group.calendar.google.com/events?calendarId=1c3f32a7ad4284a3bbc1d990d9e1fc58ac1229629b3c07cf3bf2ed2a3d2600b0%40group.calendar.google.com&singleEvents=true&eventTypes=default&eventTypes=focusTime&eventTypes=outOfOffice&timeZone=America%2FLos_Angeles&maxAttendees=1&maxResults={limit}&sanitizeHtml=true&timeMin={start_date}T00%3A00%3A00%2B18%3A00&timeMax={end_date}T00%3A00%3A00-18%3A00&key={api_key}&%24unique=gc237"

    # get the json payload from the api with a simple request
    payload = requests.get(url=link).json()
    # create a dictionary to hold the event data
    tst_df = pd.DataFrame()

    # loop through each event in the json payload and extract the relevant details
    for i, event in enumerate(payload.get("items", [])):
        # get the event title
        try:
            tst_df.loc[i, "title"] = event["summary"].strip() + " - " + event["description"].strip()
        except:
            tst_df.loc[i, "title"] = None

        # get the venue_name
        try:
            tst_df.loc[i, "venue_name"] = "The Spar Tavern"
        except:
            tst_df.loc[i, "venue_name"] = None

        # get the "from" date and format
        try:
            # set the final start_time in the dataframe
            start_date = pd.to_datetime(event["start"]["dateTime"], errors='coerce', format='mixed')
            end_date = pd.to_datetime(event["end"]["dateTime"], errors='coerce', format='mixed')

            if start_date.date() == end_date.date():
                tst_df.loc[i, "start_time"] = [start_date.strftime('%Y-%m-%d')]
            else:
                tst_df.loc[i, "start_time"] = [start_date.strftime('%Y-%m-%d'), end_date.strftime('%Y-%m-%d')]
        except:
            tst_df.loc[i, "start_time"] = None

        # get the link to the details page
        try:
            tst_df.loc[i, "external_url"] = event.get("htmlLink", "").strip()
        except:
            tst_df.loc[i, "external_url"] = None

        # add the location to the dataframe
        try:
            # use venue address
            tst_df.loc[i, "address"] = "2121 N 30th St, Tacoma, WA 98403"
        except:
            tst_df.loc[i, "address"] = None
            
        # batch extract primary details
        try:
            tst_df.loc[i, "description"] = "\n".join(
                [
                    event.get("summary", "").strip(),
                    event.get("description", "").strip(),
                    event.get("location", "").strip()
                ]
            )
        except:
            tst_df.loc[i, "description"] = None
            
        # get full text for expanded description
        try:
            tst_df.loc[i, "big_desc"] = tst_df.loc[i, "description"] # placeholder for now, as the Spar events do not have a separate expanded description
        except Exception as e:
            tst_df.loc[i, "big_desc"] = None

        # get the image link of the event
        try:
            tst_df.loc[i, "image_url"] = "https://encrypted-tbn0.gstatic.com/images?q=tbn:ANd9GcTobzx5jiQULdt9mKXAkQDJxSQRpB40gdGk5q6Ix1Ld1Q&s" # use a fixed image for all Spar events until the webpage is updated
        except:
            tst_df.loc[i, "image_url"] = None
            
        # get the ticket link of the event (if available)
        try:
            tst_df.loc[i, "ticket_link"] = None # placeholder
        except:
            tst_df.loc[i, "ticket_link"] = None

    # reset the index
    tst_df = tst_df.reset_index(drop=True)

    # return the dataframe
    return tst_df