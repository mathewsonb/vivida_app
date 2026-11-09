# import required libraries
import pandas as pd
import requests

# define the scraper function for Tacoma Arts Monthly
def scraper() -> pd.Dataframe:
    # Target API endpoint
    cal_id = 54705487
    url = f"https://calendar.time.ly/api/calendars/{cal_id}/events"
    api_key = "c6e5e0363b5925b28552de8805464c66f25ba0ce"

    # Query parameters extracted from cURL
    params = {
        "timezone": "America/Los_Angeles",
        "view": "tile",
        "start_date_utc": "1792738800",
        "end_date_utc": "1795420799",
        "per_page": "500",  # Increase up to 100 to batch pull events
        "page": "1"
    }

    # Exact headers converted directly from your browser cURL
    headers = {
        "accept": "application/json, text/plain, */*",
        "accept-language": "en-US,en;q=0.9",
        "priority": "u=1, i",
        "referer": "https://calendar.time.ly/u1mdrp81/tile?timely_id=timely-iframe-embed-0-event-popup&popup=1",
        "sec-ch-ua": '"Google Chrome";v="155", "Chromium";v="155", "Not(A:Brand";v="24"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36",
        "x-api-key": api_key
    }

    # Execute GET request
    response = requests.get(url, params=params, headers=headers)

    if response.status_code == 200:
        data = response.json()
        items = data.get("data", {}).get("items", []) if isinstance(data, dict) else data
        print(f"Success! Status 200 OK. Retrieved {len(items)} events.")
        
        # Preview key fields of the first event
        if items:
            first_event = items[0]
            print(f"\nSample Event Title: {first_event.get('title')}")
            print(f"Sample Event Venue: {first_event.get('venue', {}).get('name')}")
    else:
        print(f"Failed with Status {response.status_code}: {response.text}")
    # ====================================================
    # create a dictionary to hold the event data
    event_dict = {}
    amt_df = pd.DataFrame()

    # loop over each of the links in the 'url' list of the JSON object
    for item in items:
        # get each link in turn
        event_id = item["id"]

        # Event ID extracted from your URL
        url = f"https://calendar.time.ly/api/calendars/{cal_id}/events/{event_id}"

        headers = {
            "accept": "application/json, text/plain, */*",
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "x-api-key": api_key,
            "referer": "https://calendar.time.ly/u1mdrp81/tile"
        }

        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            event = response.json().get("data", {})

        # get the details url
        try:
            event_dict["external_url"] = item["url"].strip()
        except:
            event_dict["external_url"] = None

        # get the event title
        try:
            event_dict["title"] = item["title"].strip()
        except:
            event_dict["title"] = None

        # get the "from" date and format
        try:
            # transform the text dates to a list
            start_time = pd.to_datetime(item["start_datetime"], errors='coerce', format='mixed')
            try:
                end_time = pd.to_datetime(item["end_datetime"], errors='coerce', format='mixed')
            except:
                end_time = None

            # set the final start_time in the dataframe
            if end_time.strftime('%Y-%m-%d') == start_time.strftime('%Y-%m-%d'):
                event_dict["start_time"] = [start_time.strftime('%Y-%m-%d')]
            elif end_time:
                event_dict["start_time"] = [start_time.strftime('%Y-%m-%d'), end_time.strftime('%Y-%m-%d')]
        except:
            event_dict["start_time"] = None
            
        try:
            event_dict["venue_name"] = event["taxonomies"]["taxonomy_venue"][0]["title"].strip()
        except:
            event_dict["venue_name"] = None

        # add the location to the dataframe
        try:
            # use venue address
            street = event["taxonomies"]["taxonomy_venue"][0]["address"].split(",")[0].strip()
            city = event["taxonomies"]["taxonomy_venue"][0]["city"].strip()
            zip_code = event["taxonomies"]["taxonomy_venue"][0]["postal_code"].strip()
            event_dict["address"] = f"{street}, {city}, WA {zip_code}"
        except:
            try:
                event_dict["address"] = event["taxonomies"]["taxonomy_venue"][0]["address"].strip()
            except:
                event_dict["address"] = None

        # get full text for expanded description
        try:
            event_dict["big_desc"] = re.sub(r"\s+", " ", event["description"].strip())
        except Exception as e:
            event_dict["big_desc"] = None
            
        # batch extract primary details
        try:
            event_dict["description"] = re.sub(r"\s+", " ", event["description_short"].strip())
        except:
            event_dict["description"] = None

        # get the image link of the event
        try:
            event_dict["image_url"] = event["images"][0]["sizes"]["full"]["url"].strip()
        except:
            event_dict["image_url"] = None
            
        # get the ticket link of the event (if available)
        try:
            if event["can_purchase_tickets"] == 'False':
                event_dict["ticket_link"] = None
            else:
                event_dict["ticket_link"] = None # placeholder
        except:
            event_dict["ticket_link"] = None

        # concatenate the event_dict to the dataframe
        amt_df = pd.concat([amt_df, pd.DataFrame([event_dict])], ignore_index=True)

    # reset the index
    amt_df = amt_df.reset_index(drop=True)

    # return the dataframe
    return amt_df