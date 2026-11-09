# import required libraries
import pandas as pd
import re
from vivida_app.scraper.utils import fetch_rendered_html

# define the scraper function for Gig Harbor events
def scraper() -> pd.DataFrame:
    # set the link value
    link = "https://visitgigharbor.com/events-festivals/list/page/"
    detail_urls = []

    # iterate over the first N pages of events
    for i in range(5): # get the details links for every event in each page, as a list
        # get the soup using headless browser automation (dynamic site)
        soup = fetch_rendered_html(link + str(i+1) +"/", wait_for_selector="#post-0 > div > div > section > ul > li:nth-child(2)")
        # print(f"Soup: {soup.prettify()}")  # Print the soup

        # retrieve all the links from the current page
        url_list = soup.find_all("h4", class_="tribe-events-calendar-list__event-title tribe-common-h6 tribe-common-h4--min-medium")

        for x in url_list:
            # append each set of links to the list
            detail_urls.append(x.find("a").get("href").strip())

    # create a set of all the unique url's
    detail_urls = sorted(set(detail_urls))

    # create a dictionary to hold the event data
    event_dict = {}
    gh_df = pd.DataFrame()

    # iterate over each of the detail page urls
    for url in detail_urls[:20]:
        # get the soup for each detail page
        soup = fetch_rendered_html(url, wait_for_selector="#tribe-events-content")

        # subset the soup for only the sections of interest
        target_section = soup.find("div", class_="entry-content tribe-events-single tribe-blocks-editor")

        # strip out unnecessary scripts and styles
        for script in target_section(["script", "style"]):
            script.decompose()

        # get the event title
        try:
            event_dict["title"] = target_section.find("h1").get_text(strip=True)
        except:
            event_dict["title"] = None

        # get the venue_name
        try:
            event_dict["venue_name"] = target_section.find("h3").get_text(strip=True)
        except:
            event_dict["venue_name"] = None

        # get the "from" date and format
        try:
            # transform the text dates to a list
            now_year = pd.Timestamp.now().year
            start_time = target_section.find("span", class_="tribe-events-schedule__date tribe-events-schedule__date--start").get_text(strip=True)
            start_time = f"{start_time}, {now_year}"
            start_time = pd.to_datetime(start_time, errors='coerce', format='mixed')

            # set the final start_time in the dataframe
            event_dict["start_time"] = [start_time.strftime('%Y-%m-%d')]
        except:
            event_dict["start_time"] = None
            
        # batch extract primary details
        try:
            event_dict["description"] = target_section.find_all("p", class_="wp-block-paragraph")[0].get_text(strip=True)
        except:
            event_dict["description"] = None

        # get the link to the details page
        try:
            event_dict["external_url"] = url
        except:
            event_dict["external_url"] = None

        # get the image link of the event
        try:
            event_dict["image_url"] = target_section.find("img").get("src").strip()
        except:
            event_dict["image_url"] = None
            
        # get the ticket link of the event (if available)
        try:
            event_dict["ticket_link"] = None # placeholder
        except:
            event_dict["ticket_link"] = None

        # add the location to the dataframe
        try:
            # use venue address
            address_sec = target_section.find("address", class_="tribe-block__venue__address")
            street = address_sec.find("span", class_="tribe-street-address").get_text(strip=True)
            city = address_sec.find("span", class_="tribe-locality").get_text(strip=True)
            state = address_sec.find("abbr", class_="tribe-region tribe-events-abbr").get_text(strip=True)
            zip_code = address_sec.find("span", class_="tribe-postal-code").get_text(strip=True)
            event_dict["address"] = f"{street}, {city}, {state} {zip_code}"
        except:
            event_dict["address"] = None

        # use async function to get expanded descriptions
        try:
            event_dict["big_desc"] = re.sub(r"\s+", " ", target_section.get_text(strip=True))
        except Exception as e:
            event_dict["big_desc"] = None

        # concatenate the event_dict to the dataframe
        gh_df = pd.concat([gh_df, pd.DataFrame([event_dict])], ignore_index=True)

    # reset the index
    gh_df = gh_df.reset_index(drop=True)

    # return the dataframe
    return gh_df