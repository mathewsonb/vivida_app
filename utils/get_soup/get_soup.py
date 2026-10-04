# vivida_app/get_soup.py

import time
from typing import Optional
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

try:
    import undetected_chromedriver as uc
    HAS_UC = True
except ImportError:
    HAS_UC = False


class GetSoup:
    """
    Bulletproof scraper wrapper. Uses standard driver.get with a hard
    6-second timeout to catch hanging pages without deadlocking Selenium.
    """

    def __init__(self, headless: bool = False, use_uc: bool = True, default_timeout: int = 15):
        self.headless = headless
        self.use_uc = use_uc
        self.default_timeout = default_timeout
        self.driver = None

    def __enter__(self):
        self.start_session()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close_session()

    def start_session(self):
        if self.driver is not None:
            return

        user_agent = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )

        if self.use_uc:
            if not HAS_UC:
                raise ImportError("undetected_chromedriver is not installed.")
            options = uc.ChromeOptions()
            options.add_argument(f"--user-agent={user_agent}")
            options.add_argument("--disable-popup-blocking")
            options.add_argument("--start-maximized")
            options.add_argument("--mute-audio")

            if self.headless:
                options.add_argument("--headless=new")

            self.driver = uc.Chrome(options=options, suppress_welcome=True)

        else:
            options = Options()
            options.add_argument("--start-maximized")
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument("--disable-blink-features=AutomationControlled")
            options.add_argument(f"user-agent={user_agent}")
            options.add_argument("--mute-audio")

            if self.headless:
                options.add_argument("--headless=new")

            self.driver = webdriver.Chrome(options=options)

        # Set a hard 6-second timeout. Chrome WILL raise TimeoutException if a page hangs.
        self.driver.set_page_load_timeout(6)

    def close_session(self):
        if self.driver:
            try:
                self.driver.quit()
            except Exception:
                pass
            finally:
                self.driver = None

    def wait_for_cloudflare_pass(self, max_wait: int = 10) -> bool:
        start_time = time.time()
        while time.time() - start_time < max_wait:
            try:
                title = self.driver.title.lower() if self.driver.title else ""
            except Exception:
                title = ""

            in_challenge = any(kw in title for kw in [
                "just a moment", "challenge", "checking your browser", 
                "403 forbidden", "attention required", "too many requests"
            ])

            if not in_challenge:
                return True
            time.sleep(0.5)
        return False

    def fetch(
        self, 
        url: str, 
        wait_for_selector: Optional[str] = None, 
        delay: float = 0.5
    ) -> Optional[BeautifulSoup]:
        """Navigates safely using native driver.get and a strictly enforced timeout."""
        if not self.driver:
            self.start_session()

        try:
            # Standard blocking call with a 6-second cap
            try:
                self.driver.get(url)
            except TimeoutException:
                # Page load timed out (usually due to background tracking scripts).
                # Issue window.stop() to abort pending downloads; the DOM is already ready.
                try:
                    self.driver.execute_script("window.stop();")
                except Exception:
                    pass

            # Handle security challenge screens if present
            self.wait_for_cloudflare_pass()

            # Optional element wait
            if wait_for_selector:
                try:
                    WebDriverWait(self.driver, 4).until(
                        EC.presence_of_element_located((By.CSS_SELECTOR, wait_for_selector))
                    )
                except Exception:
                    pass

            if delay > 0:
                time.sleep(delay)

            soup = BeautifulSoup(self.driver.page_source, "html.parser")
            page_text = soup.get_text().lower()

            if "403 forbidden" in page_text or "access denied" in page_text:
                return None

            return soup

        except Exception as e:
            print(f"[GetSoup Error] {url}: {e}")
            return None