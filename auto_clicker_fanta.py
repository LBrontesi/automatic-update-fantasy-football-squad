import logging
import os
import sys
import time

from dotenv import load_dotenv
from selenium import webdriver
from selenium.common.exceptions import NoSuchElementException, TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

load_dotenv()

DEFAULT_LEAGUE_URLS = [
    "https://leghe.fantacalcio.it/rusticatorsapienza/area-gioco/inserisci-formazione?id=362238",
    "https://leghe.fantacalcio.it/ilprimoverofanta/area-gioco/inserisci-formazione?id=416045",
]

LOGIN_BUTTON_SELECTORS = [
    (By.XPATH, "//a[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'accedi')] | //button[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'accedi')]"),
    (By.XPATH, "/html/body/nav[2]/div/a[2]"),
]

USERNAME_SELECTORS = [
    (By.XPATH, "//input[@formcontrolname='email'] | //input[@type='email']"),
    (By.XPATH, "//input[contains(@placeholder, 'mail')] | //input[contains(@placeholder, 'email')]"),
    (By.XPATH, "/html/body/app-root/layout-auth/div[1]/div/view-login/nz-card/div[2]/form/nz-form-item[1]/nz-form-control/div/div/nz-input-group/input"),
]

PASSWORD_SELECTORS = [
    (By.XPATH, "//input[@formcontrolname='password'] | //input[@type='password']"),
    (By.XPATH, "/html/body/app-root/layout-auth/div[1]/div/view-login/nz-card/div[2]/form/nz-form-item[2]/nz-form-control/div/div/nz-input-group/input"),
]

CONFIRM_BUTTON_SELECTORS = [
    (By.XPATH, "//*[@id='formation']//button[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'conferma') or contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'invia')]"),
    (By.XPATH, "//*[@id='formation']/div[2]/div[5]/button[1]"),
]

SUCCESS_SELECTORS = [
    (By.CSS_SELECTOR, ".nz-message-success, ant-message-success, .ant-message-success"),
    (By.XPATH, "//*[contains(@class, 'success') and contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'conferm')]"),
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("fantasquad")

WAIT_TIMEOUT = 30
RETRIES_PER_LEAGUE = 2


def get_config():
    email = os.environ.get("FANTA_EMAIL", "").strip()
    password = os.environ.get("FANTA_PASSWORD", "").strip()
    if not email or not password:
        raise SystemExit(
            "FANTA_EMAIL and FANTA_PASSWORD must be set (in .env or as environment variables)."
        )
    league_urls = os.environ.get("LEAGUE_URLS", "").strip()
    if league_urls:
        league_urls = [u.strip() for u in league_urls.split(",") if u.strip()]
    else:
        league_urls = DEFAULT_LEAGUE_URLS
    headless = os.environ.get("HEADLESS", "true").strip().lower() not in ("0", "false", "no")
    return email, password, league_urls, headless


def create_driver(headless):
    options = webdriver.ChromeOptions()
    if headless:
        options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
    return webdriver.Chrome(options=options)


def find_element(driver, selectors, wait=True, timeout=WAIT_TIMEOUT):
    last_error = None
    for by, selector in selectors:
        try:
            if wait:
                return WebDriverWait(driver, timeout).until(
                    EC.presence_of_element_located((by, selector))
                )
            return driver.find_element(by, selector)
        except (TimeoutException, NoSuchElementException) as e:
            last_error = e
    if last_error:
        raise last_error


def is_logged_in(driver):
    try:
        find_element(driver, USERNAME_SELECTORS, wait=False)
        return False
    except NoSuchElementException:
        return True


def do_login(driver, email, password):
    login_button = find_element(driver, LOGIN_BUTTON_SELECTORS)
    driver.execute_script("arguments[0].scrollIntoView();", login_button)
    driver.execute_script("arguments[0].click();", login_button)

    username = find_element(driver, USERNAME_SELECTORS)
    driver.execute_script("arguments[0].scrollIntoView();", username)
    username.send_keys(email)

    password_input = find_element(driver, PASSWORD_SELECTORS)
    driver.execute_script("arguments[0].scrollIntoView();", password_input)
    password_input.send_keys(password)
    password_input.send_keys(Keys.RETURN)

    WebDriverWait(driver, WAIT_TIMEOUT).until_not(
        EC.presence_of_element_located(USERNAME_SELECTORS[0])
    )
    log.info("Logged in")


def confirm_formation(driver, url):
    log.info("Opening %s", url)
    driver.get(url)

    if not is_logged_in(driver):
        do_login(driver, *get_config()[:2])
        log.info("Re-opening %s after login", url)
        driver.get(url)

    button = find_element(driver, CONFIRM_BUTTON_SELECTORS)
    driver.execute_script("arguments[0].scrollIntoView();", button)
    driver.execute_script("arguments[0].click();", button)
    log.info("Confirm button clicked")

    try:
        find_element(driver, SUCCESS_SELECTORS, timeout=15)
        log.info("Success confirmed (confirmation message shown)")
    except TimeoutException:
        log.warning("No success message detected, assuming the click went through")


def main():
    email, password, league_urls, headless = get_config()
    log.info("Running with %d league(s), headless=%s", len(league_urls), headless)

    driver = create_driver(headless)
    failures = []
    try:
        for url in league_urls:
            for attempt in range(1, RETRIES_PER_LEAGUE + 1):
                try:
                    confirm_formation(driver, url)
                    break
                except Exception as e:
                    log.error("Attempt %d/%d failed for %s: %s", attempt, RETRIES_PER_LEAGUE, url, e)
                    if attempt == RETRIES_PER_LEAGUE:
                        failures.append(url)
                    else:
                        time.sleep(5)
    finally:
        driver.quit()

    if failures:
        log.error("Failed leagues: %s", ", ".join(failures))
        sys.exit(1)
    log.info("All leagues updated")


if __name__ == "__main__":
    main()
