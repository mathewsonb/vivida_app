# import required libraries
import pandas as pd
from vivida_app.vivida_etl import fetch_rendered_html

# define the scraper function for City of University Place
def scraper() -> pd.DataFrame:
    # set the link value
    link = "https://www.cityofup.com/132/Events"

    # get the soup using headless browser automation (dynamic site)
    soup = fetch_rendered_html(link, wait_for_selector="#widgetCalendar6b93ab4d-f80c-4bff-9e05-394db03a8d2c > div.widgetBody.cpGrid.cpGrid1 > div > div > div > ol")
    # print(f"Soup: {soup.prettify()}")  # Print the soup

    # subset the soup for only the sections of interest
    target_section = soup.find("div", class_="calendarInnerWrapper")

    # strip out unnecessary scripts and styles
    for script in target_section(["script", "style"]):
        script.decompose()

    # extract all the event cards
    event_items = target_section.find_all("li")

    # create a dictionary to hold the event data
    event_dict = {}
    cup_df = pd.DataFrame()

    # parse each event to get name, link to details, date (range), description, image link, location, price, and ticket link (if available)
    for event in event_items:
        # get the link to the details page
        try:
            event_dict["external_url"] = "https://www.cityofup.com" + event.find("a").get("href").strip()
        except:
            event_dict["external_url"] = None

        # get the event title
        try:
            event_dict["title"] = event.find("a").get_text(strip=True)
        except:
            event_dict["title"] = None

        # get the "from" date and format
        try:
            # transform the text dates to a list
            now_year = pd.Timestamp.now().year
            date = event.find("h4").get_text(strip=True)
            start_time = date.split(",")[1].strip() + f", {now_year}"
            start_time = pd.to_datetime(start_time, errors='coerce', format='mixed')

            # check that the month is in the future, and if not, add 1 to the year
            if start_time.month < pd.Timestamp.now().month:
                start_time = start_time + pd.DateOffset(years=1)
                start_time = pd.to_datetime(start_time, errors='coerce', format='mixed')

            # set the final start_time in the dataframe
            event_dict["start_time"] = [start_time.strftime('%Y-%m-%d')]
        except:
            event_dict["start_time"] = None

        # STEP INTO DETAILS
        # ==================
        # use async function to get expanded descriptions
        det_soup = fetch_rendered_html(
            event_dict["external_url"],
            wait_for_selector="#ctl00_ctl00_MainContent_ModuleContent_ctl00_contentUpdatePanel > div.eventDetails.selfClear"
            )

        # get the venue_name
        addr_soup = det_soup.find("div", itemprop="location")
        try:
            event_dict["venue_name"] = addr_soup.find("div", itemprop="name").get_text(strip=True)
        except:
            event_dict["venue_name"] = None

        # add the location to the dataframe
        try:
            # use venue address
            street = addr_soup.find("span", itemprop="streetAddress").get_text(strip=True)
            city = addr_soup.find("span", itemprop="addressLocality").get_text(strip=True)
            zip_code = addr_soup.find("span", itemprop="postalCode").get_text(strip=True)
            event_dict["address"] = f"{street}, {city}, WA {zip_code}"
        except:
            event_dict["address"] = None

        # get full text for expanded description
        try:
            event_dict["big_desc"] = re.sub(r"\s+", " ", det_soup.find("div", itemprop="description").get_text(strip=True, separator=" "))
        except Exception as e:
            event_dict["big_desc"] = None
            
        # batch extract primary details
        try:
            event_dict["description"] = det_soup.find("div", itemprop="description").find_all("p")[0].get_text(strip=True, separator=" ")
        except:
            event_dict["description"] = None

        # get the image link of the event
        try:
            event_dict["image_url"] = "https://www.cityofup.com" + det_soup.find("img").get("src").strip() # combine for whole URL
        except:
            event_dict["image_url"] = None
            
        # get the ticket link of the event (if available)
        try:
            event_dict["ticket_link"] = None # placeholder
        except:
            event_dict["ticket_link"] = None

        # concatenate the event_dict to the dataframe
        cup_df = pd.concat([cup_df, pd.DataFrame([event_dict])], ignore_index=True)

    # reset the index
    cup_df = cup_df.reset_index(drop=True)

    # return the dataframe
    return cup_df