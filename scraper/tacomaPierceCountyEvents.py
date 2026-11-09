# import required libraries
import pandas as pd
from selenium import webdriver
from selenium.webdriver.chrome.options import Options, WebDriverWait
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from bs4 import BeautifulSoup
import json
import re

# define the scraper for Tacoma and Pierce County Events Calendar
def scraper() -> pd.DataFrame:
    # use the calendar api to get all events for the next 30 days, and then parse the json to extract the relevant details
    # =============================
    # set start/end dates for the next 30 days, and set the limit to 250 events
    start_date = pd.Timestamp.now().strftime('%Y-%m-%d')
    end_date = (pd.Timestamp.now() + pd.Timedelta(days=30)).strftime('%Y-%m-%d')
    limit = 500

    # set api key for the calendar api
    api_key = "45d9681f15067d6e73734e4e1f75d32d"

    # set the link value
    link = f"https://www.visitpiercecounty.com/includes/rest_v2/plugins_events_events_by_date/find/?json=%7B%22filter%22%3A%7B%22active%22%3Atrue%2C%22%24and%22%3A%5B%7B%22categories.catId%22%3A%7B%22%24in%22%3A%5B%228%22%2C%2210%22%2C%2214%22%2C%229%22%2C%2220%22%2C%2213%22%2C%2212%22%2C%2211%22%5D%7D%7D%5D%2C%22date_range%22%3A%7B%22start%22%3A%7B%22%24date%22%3A%22{start_date}T08%3A00%3A00.000Z%22%7D%2C%22end%22%3A%7B%22%24date%22%3A%22{end_date}T08%3A00%3A00.000Z%22%7D%7D%7D%2C%22options%22%3A%7B%22limit%22%3A{limit}%2C%22skip%22%3A0%2C%22count%22%3Atrue%2C%22castDocs%22%3Afalse%2C%22fields%22%3A%7B%22_id%22%3A1%2C%22location%22%3A1%2C%22date%22%3A1%2C%22startDate%22%3A1%2C%22endDate%22%3A1%2C%22recurrence%22%3A1%2C%22recurType%22%3A1%2C%22latitude%22%3A1%2C%22longitude%22%3A1%2C%22media_raw%22%3A1%2C%22recid%22%3A1%2C%22title%22%3A1%2C%22url%22%3A1%2C%22categories%22%3A1%2C%22accountId%22%3A1%2C%22city%22%3A1%2C%22region%22%3A1%2C%22listing.primary_category%22%3A1%2C%22listing.title%22%3A1%2C%22listing.url%22%3A1%2C%22listing.recid%22%3A1%2C%22listing.acctid%22%3A1%2C%22listing.region%22%3A1%2C%22listing.city%22%3A1%2C%22listing.rankname%22%3A1%7D%2C%22hooks%22%3A%5B%5D%2C%22sort%22%3A%7B%22date%22%3A1%2C%22rank%22%3A1%2C%22title_sort%22%3A1%7D%7D%7D&token={api_key}"

    # set the options for a Selenium browser automation
    options = Options()
    options.add_argument("--start-maximized")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")

    # because we aren't using the predefined function, we initialize the driver here
    driver = webdriver.Chrome(options=options)

    # now we attempt to scrape the JSON object from the page
    try:
        # open the browser and navigate to the URL provided
        driver.get(link)

        # Wait for the body tag to populate with JSON text
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.TAG_NAME, "body"))
        )

        # Extract raw text from body (or <pre> if Chrome wraps JSON in pre tags)
        raw_json_text = driver.find_element(By.TAG_NAME, "body").text

        # Parse JSON string directly into a Python dictionary
        data = json.loads(raw_json_text)
        events = data.get("docs", [])

        # Convert to Pandas DataFrame for ingestion
        df = pd.DataFrame(events)    
        print(f"Successfully scraped {len(df)} events via Selenium!")

    except Exception as e:
        print(f"Error scraping with Selenium: {e}")

    # quit the driver here, since we're operating outside the predefined function
    finally:
        driver.quit()
    # extract all partial detail links, concatentate, and iteratively scrape them
    event_items = [str('https://www.visitpiercecounty.com' + doc['url']) for doc in df['docs']]

    # create a dictionary to hold the event data
    event_dict = {}
    tpc_df = pd.DataFrame()

    # parse each event to get name, link to details, date (range), description, image link, location, price, and ticket link (if available)
    for event in event_items:
        # get the raw html from each detail page in turn
        det_soup = fetch_rendered_html(event).find("div", id="main-content")

        # 1. Remove empty comment markers (common in Vue/Angular templates: <!---->)
        cleaned = re.sub(r"<!\s*--\s*-->", "", det_soup.get_text())
        
        # 2. Unwrap standard multi-line HTML comments to expose hidden block content
        cleaned = re.sub(r"<!--\s*([\s\S]*?)\s*-->", r"\1", cleaned)

        # reindex the raw html into a bs4 object
        soup = BeautifulSoup(cleaned, "html.parser")
        # get the event title
        try:
            event_dict["title"] = det_soup.find("div", class_="info-section").find("h1").get_text(strip=True)
        except:
            event_dict["title"] = None
            
        # get a list of the 'dd' elements
        date_soup = det_soup.find("div", class_="info-section").find_all("dd")
        try:
            # if there are more than one, assume the first is the venue name, and second is date
            if len(date_soup)>1:
                dates = date_soup[1].get_text(strip=True)
                # check type of delimiter to determine output style
                if len(re.split(r"-", dates))>1:
                    dates = dates.split("-")
                    dates = [pd.to_datetime(x.strip(), errors='coerce', format='mixed').strftime('%Y-%m-%d') for x in dates]
                    event_dict["start_time"] = [",".join(dates)]
                elif len(re.findall(r"/", dates))>0:
                    event_dict["start_time"] = [pd.to_datetime(date, errors='coerce', format='mixed').strftime('%Y-%m-%d') for date in dates.split(",")]
                else:
                    event_dict["start_time"] = [pd.to_datetime(dates.strip(), errors='coerce', format='mixed').strftime('%Y-%m-%d')]
            else:
                dates = date_soup[0].get_text(strip=True)
                # check type of delimiter to determine output style
                if len(re.split(r"-", dates))>1:
                    dates = dates.split("-")
                    dates = [pd.to_datetime(x.strip(), errors='coerce', format='mixed').strftime('%Y-%m-%d') for x in dates]
                    event_dict["start_time"] = [",".join(dates)]
                elif len(re.findall(r"/", dates))>0:
                    event_dict["start_time"] = [pd.to_datetime(date, errors='coerce', format='mixed').strftime('%Y-%m-%d') for date in dates.split(",")]
                else:
                    event_dict["start_time"] = [pd.to_datetime(dates.strip(), errors='coerce', format='mixed').strftime('%Y-%m-%d')]
        except:
            event_dict["start_time"] = None

        # get the venue_name
        try:
            event_dict["venue_name"] = det_soup.find("div", class_="two-line-wrap").find("a").get_text(strip=True)
        except:
            try:
                # in the case that the venue name is included in the title...
                event_dict["venue_name"] = re.split(r" at | At | AT | @ ", event_dict["title"])[1].strip()
            except:
                # and if not, assume the venue name is present in the first location ('Presented By')
                try:
                    date_soup[0].get_text(strip=True)
                except:
                    # and if everything fails, assign 'None'
                    event_dict["venue_name"] = None

        # get the link to the details page
        try:
            event_dict["external_url"] = event
        except:
            event_dict["external_url"] = None

        # add the location to the dataframe
        try:
            # use venue address
            loc_soup = det_soup.find("div", class_="two-line-wrap")
            street = loc_soup.find_all("span")[0].get_text(strip=True)
            city = loc_soup.find_all("span")[1].get_text(strip=True)
            event_dict["address"] = street + ", " + city
        except:
            event_dict["address"] = None

        # =======================================
        # get full text for expanded description
        try:
            event_dict["big_desc"] = re.sub(r"\s+", " ", det_soup.get_text(strip=True, separator=" "))
        except:
            event_dict["big_desc"] = None
            
        # batch extract primary details
        try:
            event_dict["description"] = det_soup.find("div", id="descriptionTab").get_text(strip=True, separator=" ") # changes in the class value are event IDs, which is why we use REGEX
        except:
            event_dict["description"] = None

        # get the image link of the event
        try:
            event_dict["image_url"] = det_soup.find("img", class_="slide-img loaded").get("src").strip()
        except:
            event_dict["image_url"] = None
            
        # get the ticket link of the event (if available)
        try:
            event_dict["ticket_link"] = det_soup.find("div", class_="bottom-actions").find("a").get("href").strip()
            # print(event_dict["ticket_link"])
        except:
            event_dict["ticket_link"] = None

        # concatenate the event_dict to the dataframe
        tpc_df = pd.concat([tpc_df, pd.DataFrame([event_dict])], ignore_index=True)

    # reset the index
    tpc_df = tpc_df.reset_index(drop=True)

    # drop all rows where venue or start time are missing
    tpc_df = tpc_df[tpc_df["start_time"].notnull()].reset_index(drop=True)

    # return the dataframe
    return tpc_df