from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from selenium import webdriver
from selenium.common.exceptions import NoSuchElementException, TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from squad_picker import Player
from predictions import LeagueData

log = logging.getLogger("fantasquad")

WAIT_TIMEOUT = 30

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

FORMATION_CONTAINER_SELECTORS = [
    (By.ID, "formation"),
    (By.CSS_SELECTOR, "[class*='formation']"),
]

PLAYER_ROW_SELECTORS = [
    (By.XPATH, "//*[@id='formation']//*[contains(@class, 'giocatore') or contains(@class, 'player')]"),
    (By.XPATH, "//*[@id='formation']//li"),
    (By.XPATH, "//*[@id='formation']//tr"),
]

NAME_IN_ROW_SELECTORS = [
    (By.XPATH, ".//*[contains(@class, 'nome') or contains(@class, 'name')]"),
    (By.XPATH, ".//td[1]"),
]

ROLE_IN_ROW_SELECTORS = [
    (By.XPATH, ".//*[contains(@class, 'ruolo') or contains(@class, 'role')]"),
    (By.XPATH, ".//td[2]"),
]

VOTE_IN_ROW_SELECTORS = [
    (By.XPATH, ".//*[contains(@class, 'voto') or contains(@class, 'vote')]"),
    (By.XPATH, ".//td[3]"),
]

LINEUP_CONFIRMED_SELECTORS = [
    (By.XPATH, "//*[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'formazione confermata')]"),
]

LINEUP_EMPTY_SELECTORS = [
    (By.XPATH, "//*[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'nessun giocatore')]"),
    (By.XPATH, "//*[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'formazione non confermata')]"),
]

ROLE_ALIASES = {
    "portiere": "G",
    "difensore": "D",
    "centrocampista": "C",
    "attaccante": "A",
}


class ExtractionError(RuntimeError):
    pass


class LineupUIError(RuntimeError):
    pass


def create_driver(headless: bool = True) -> webdriver.Chrome:
    options = webdriver.ChromeOptions()
    if headless:
        options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
    options.set_capability("goog:loggingPrefs", {"performance": "ALL"})
    return webdriver.Chrome(options=options)


def find_element(driver, selectors, wait: bool = True, timeout: int = WAIT_TIMEOUT):
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
    raise last_error


def is_logged_in(driver) -> bool:
    try:
        find_element(driver, USERNAME_SELECTORS, wait=False)
        return False
    except NoSuchElementException:
        return True


def login_if_needed(driver, email: str, password: str) -> None:
    if is_logged_in(driver):
        log.info("Already logged in")
        return
    log.info("Login required")

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


def confirm_formation(driver) -> None:
    button = find_element(driver, CONFIRM_BUTTON_SELECTORS)
    driver.execute_script("arguments[0].scrollIntoView();", button)
    driver.execute_script("arguments[0].click();", button)
    log.info("Confirm button clicked")

    try:
        find_element(driver, SUCCESS_SELECTORS, timeout=15)
        log.info("Success confirmed (confirmation message shown)")
    except TimeoutException:
        log.warning("No success message detected, assuming the click went through")


def check_lineup_state(driver) -> bool | None:
    try:
        find_element(driver, LINEUP_CONFIRMED_SELECTORS, wait=False)
        log.info("Lineup detected as already set")
        return False
    except NoSuchElementException:
        pass
    try:
        find_element(driver, LINEUP_EMPTY_SELECTORS, wait=False)
        log.info("Lineup detected as empty")
        return True
    except NoSuchElementException:
        pass
    log.warning("Could not detect lineup state from the page, assuming empty")
    return None


def save_snapshot(driver, debug_dir: str | None, label: str) -> str | None:
    if not debug_dir:
        return None
    directory = Path(debug_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{label}_{driver.current_url.split('/')[-1].split('?')[0] or 'page'}.html"
    path.write_text(driver.page_source, encoding="utf-8")
    log.info("Saved page snapshot to %s", path)
    return str(path)


def sniff_api_endpoints(driver) -> list[str]:
    try:
        logs = driver.get_log("performance")
    except Exception:
        return []
    urls = []
    for entry in logs:
        message = entry.get("message", "")
        match = re.search(r'"url"\s*:\s*"([^"]+)"', message)
        if not match:
            continue
        url = match.group(1)
        lowered = url.lower()
        if any(k in lowered for k in ("api", "formazione", "voti", "stat", "squadra", "giocatori")):
            urls.append(url)
    unique = list(dict.fromkeys(urls))
    if unique:
        log.info("Candidate API endpoints observed: %s", json.dumps(unique, indent=2))
    return unique


def _parse_votes(raw: str) -> list[float]:
    votes = []
    for token in re.findall(r"[\d,]+(?:\.\d+)?", raw.replace(",", ".")):
        try:
            votes.append(float(token))
        except ValueError:
            continue
    return votes[:5]


def _parse_role(raw: str) -> str:
    normalized = raw.lower()
    for alias, role in ROLE_ALIASES.items():
        if alias in normalized:
            return role
    for role in ("G", "D", "C", "A"):
        if role in normalized.upper():
            return role
    return "?"


def fetch_league_data(driver, url: str, debug_dir: str | None = None) -> LeagueData:
    log.info("Opening %s", url)
    driver.get(url)
    find_element(driver, FORMATION_CONTAINER_SELECTORS)
    sniff_api_endpoints(driver)

    players: list[Player] = []
    rows = None
    for by, selector in PLAYER_ROW_SELECTORS:
        try:
            rows = WebDriverWait(driver, WAIT_TIMEOUT).until(
                EC.presence_of_all_elements_located((by, selector))
            )
        except TimeoutException:
            continue
        if rows:
            break

    if not rows:
        save_snapshot(driver, debug_dir, "extract_failed")
        raise ExtractionError(
            "Could not find player rows in the formation page. "
            f"Snapshot saved to {debug_dir or 'disabled'} - run locally with "
            "--dry-run and share the debug/ output to map the selectors."
        )

    for row in rows:
        name_el = find_element(row, NAME_IN_ROW_SELECTORS, wait=False)
        role_el = find_element(row, ROLE_IN_ROW_SELECTORS, wait=False)
        vote_el = find_element(row, VOTE_IN_ROW_SELECTORS, wait=False)
        if name_el is None:
            continue
        players.append(
            Player(
                name=name_el.text.strip(),
                role=_parse_role(role_el.text) if role_el else "?",
                votes=_parse_votes(vote_el.text) if vote_el else [],
            )
        )

    if not players:
        save_snapshot(driver, debug_dir, "extract_empty")
        raise ExtractionError("No players parsed from the formation page.")

    log.info("Parsed %d players from the formation page", len(players))
    return LeagueData(name=url.split("/")[2], url=url, players=players,
                      lineup_empty=check_lineup_state(driver))


def set_lineup(driver, picked) -> None:
    raise LineupUIError(
        "Setting the lineup programmatically is not mapped yet. "
        "Run locally with --dry-run, then share the debug/ HTML snapshots "
        "so the add/remove-player selectors can be implemented."
    )
