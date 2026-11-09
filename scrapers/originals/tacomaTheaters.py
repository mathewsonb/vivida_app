# import required libraries
import pandas as pd
from vivida_app.vivida_etl import fetch_rendered_html, format_raw_date
import re
import time
from selenium import webdriver
from selenium.webdriver.chrome.options import Options, WebDriverWait
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from bs4 import BeautifulSoup

# define the scraper function for the McMenamins Elks Temple events page
def scraper() -> pd.DataFrame:
    # set the link value
    link = "https://www.tacomacitytheaters.org/events"

    # set local options for the browser automation
    options = Options()
    options.add_argument("--start-maximized")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-blink-features=AutomationControlled")

    # initialize the Chrome WebDriver with the specified options
    driver = webdriver.Chrome(options=options)

    # define an async function to get the unfolded HTML content of a page
    try:
        print("Navigating to page...")
        driver.get(link)
        
        # Wait until initial event container loads
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "#content > div.full.hybrid.event_listing_page.clearfix > div.full_column.non-widget-area > div:nth-child(3)"))
        )

        # 2. Keep clicking "Load More" on the LIVE driver until it's gone or disabled
        while True:
            try:
                # Find the live button in Selenium
                button = driver.find_element(By.ID, "loadMoreEvents")
                
                # Check if button is displayed and clickable
                if button.is_displayed() and button.is_enabled():
                    print("Clicking 'Load More'...")
                    # Scroll button into view to ensure clean click
                    driver.execute_script("arguments[0].scrollIntoView(true);", button)
                    driver.execute_script("arguments[0].click();", button)
                    
                    # Give the page 2.5 seconds to load newly fetched events
                    time.sleep(2.5)
                else:
                    print("Button disabled or hidden. All events loaded.")
                    break
            except Exception as e:
                print("No more 'Load More' button found or all items unfolded.")
                break

        # 3. Capture the final, fully unfolded HTML source snapshot
        final_html = driver.page_source

    finally:
        driver.quit()

    # get the soup using headless browser automation (dynamic site)
    soup = BeautifulSoup(final_html, "html.parser")

    # subset the soup for only the sections of interest
    target_section = soup.find("div", class_="eventList event_list_grid event_list is-loaded")

    # strip out unnecessary scripts and styles
    for script in target_section(["script", "style"]):
        script.decompose()

    # extract all the event cards
    event_items = target_section.find_all("div", class_=re.compile(r"^eventItem entry"))

    # create a dictionary to hold the event data
    event_dict = {}
    rpt_df = pd.DataFrame()

    # parse each event to get name, link to details, date (range), description, image link, location, price, and ticket link (if available)
    for event in event_items:
        # get the event title
        try:
            event_dict["title"] = event.find("h3").get_text(strip=True)
        except:
            event_dict["title"] = None

        # get the venue_name
        try:
            event_dict["venue_name"] = event.find("div", class_="location").get_text(strip=True)
        except:
            event_dict["venue_name"] = None

        # get the "from" date and format
        try:
            # transform the text date
            start_time = event.find("div", class_="date").get_text(strip=True)
            start_time = format_raw_date(start_time)  # Call the function to format the raw date string

            # assign the start date variable in the dataframe
            event_dict["start_time"] = [start_time]
            # print(f"Raw start_time: {event_dict["start_time"]}")  # Debugging line to check the raw date string
        except:
            event_dict["start_time"] = None

        # batch extract primary details
        try:
            event_dict["description"] = event.find("h4", class_="tagline").get_text(strip=True)
        except:
            event_dict["description"] = None

        # get the link to the details page
        try:
            event_dict["external_url"] = event.find("h3").find("a").get("href").strip()
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
            event_dict["address"] = "901 Broadway, Tacoma, WA 98402"
        except:
            event_dict["address"] = None

        # use async function to get expanded descriptions
        try:
            det_soup = fetch_rendered_html(event_dict["external_url"])
            event_dict["big_desc"] = re.sub(r"\s+", " ", det_soup.find("div", class_="leftColumn").get_text(strip=True, separator=" "))
        except Exception as e:
            event_dict["big_desc"] = None

        # concatenate the event_dict to the dataframe
        rpt_df = pd.concat([rpt_df, pd.DataFrame([event_dict])], ignore_index=True)

    # reset the index
    rpt_df = rpt_df.reset_index(drop=True)

    # return the dataframe
    return rpt_df