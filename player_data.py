from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from urllib.parse import parse_qs, unquote, urljoin, urlparse

from selenium import webdriver
from selenium.common.exceptions import (
    NoSuchElementException,
    StaleElementReferenceException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from squad_picker import Player
from predictions import LeagueData

log = logging.getLogger("fantasquad")

WAIT_TIMEOUT = 30

LOGIN_BUTTON_SELECTORS = [
    (By.CSS_SELECTOR, "button[type='submit']"),
    (By.XPATH, "//button[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'login') or contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'accedi') or contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'entra') ]"),
    (By.XPATH, "//a[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'accedi')] | //button[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'accedi')]"),
    (By.XPATH, "/html/body/nav[2]/div/a[2]"),
]

USERNAME_SELECTORS = [
    (By.CSS_SELECTOR, "input[name='username']"),
    (By.CSS_SELECTOR, "input[autocomplete='username']"),
    (By.CSS_SELECTOR, "input[placeholder*='Username' i]"),
    (By.XPATH, "//input[@formcontrolname='email'] | //input[@type='email']"),
    (By.XPATH, "//input[contains(@placeholder, 'mail')] | //input[contains(@placeholder, 'email')]"),
    (By.XPATH, "/html/body/app-root/layout-auth/div[1]/div/view-login/nz-card/div[2]/form/nz-form-item[1]/nz-form-control/div/div/nz-input-group/input"),
]

PASSWORD_SELECTORS = [
    (By.CSS_SELECTOR, "input[autocomplete='current-password']"),
    (By.XPATH, "//input[@formcontrolname='password'] | //input[@type='password']"),
    (By.XPATH, "/html/body/app-root/layout-auth/div[1]/div/view-login/nz-card/div[2]/form/nz-form-item[2]/nz-form-control/div/div/nz-input-group/input"),
]

CONFIRM_BUTTON_SELECTORS = [
    (By.XPATH, "//view-lineup//button[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'salva formazione') or contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'save lineup') ]"),
    (By.XPATH, "//*[@id='formation']//button[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'conferma') or contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'invia')]"),
    (By.XPATH, "//*[@id='formation']/div[2]/div[5]/button[1]"),
]

SUCCESS_SELECTORS = [
    (By.CSS_SELECTOR, ".nz-message-success, ant-message-success, .ant-message-success"),
    (By.XPATH, "//*[contains(@class, 'success') and contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'conferm')]"),
]

FORMATION_CONTAINER_SELECTORS = [
    (By.CSS_SELECTOR, "view-lineup"),
    (By.ID, "formation"),
    (By.CSS_SELECTOR, "[class*='formation']"),
]

PLAYER_ROW_SELECTORS = [
    (By.CSS_SELECTOR, "view-lineup ui-player-card"),
    (By.CSS_SELECTOR, "view-lineup [data-player-id]"),
    (By.XPATH, "//*[@id='formation']//*[contains(@class, 'giocatore') or contains(@class, 'player')]"),
    (By.XPATH, "//*[@id='formation']//li"),
    (By.XPATH, "//*[@id='formation']//tr"),
]

NAME_IN_ROW_SELECTORS = [
    (By.CSS_SELECTOR, ".player-name"),
    (By.CSS_SELECTOR, "[data-player-name]"),
    (By.XPATH, ".//*[contains(@class, 'nome') or contains(@class, 'name')]"),
    (By.XPATH, ".//td[1]"),
]

ROLE_IN_ROW_SELECTORS = [
    (By.CSS_SELECTOR, "ui-player-role, ui-role-pill"),
    (By.CSS_SELECTOR, "[data-role], [role]"),
    (By.XPATH, ".//*[contains(@class, 'ruolo') or contains(@class, 'role')]"),
    (By.XPATH, ".//td[2]"),
]

VOTE_IN_ROW_SELECTORS = [
    (By.CSS_SELECTOR, ".stats, [class*='stats'], [data-vote]"),
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


def normalize_league_url(url: str) -> str:
    """Convert the retired legacy formation URL to the current app route."""
    parsed = urlparse(url.strip())
    parts = [part for part in parsed.path.split("/") if part]
    if "area-gioco" not in parts or "inserisci-formazione" not in parts:
        return url.strip()

    alias = parts[0]
    competition_id = parse_qs(parsed.query).get("id", [None])[0]
    if not alias or not competition_id:
        return url.strip()
    return urljoin(url, f"/{alias}/view/competition/{competition_id}/lineup")


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
    raise last_error or NoSuchElementException("No matching selector")


def find_optional_element(driver, selectors):
    try:
        return find_element(driver, selectors, wait=False)
    except NoSuchElementException:
        return None


def _text(value: str) -> str:
    return " ".join((value or "").split())


def _visible(element) -> bool:
    try:
        return element.is_displayed() and element.is_enabled()
    except (StaleElementReferenceException, WebDriverException):
        return False


def _is_login_page(driver) -> bool:
    return bool(find_optional_element(driver, PASSWORD_SELECTORS)) or "/login" in driver.current_url.lower()


def is_logged_in(driver) -> bool:
    try:
        find_element(driver, USERNAME_SELECTORS, wait=False)
        return False
    except NoSuchElementException:
        return True


def login_if_needed(driver, email: str, password: str) -> None:
    if is_logged_in(driver) and not _is_login_page(driver):
        log.info("Already logged in")
        return
    log.info("Login required")

    login_target = driver.current_url
    username = find_optional_element(driver, USERNAME_SELECTORS)
    if username is None:
        login_button = find_element(driver, LOGIN_BUTTON_SELECTORS)
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", login_button)
        driver.execute_script("arguments[0].click();", login_button)
        username = find_element(driver, USERNAME_SELECTORS)
    driver.execute_script("arguments[0].scrollIntoView();", username)
    username.clear()
    username.send_keys(email)

    password_input = find_element(driver, PASSWORD_SELECTORS)
    driver.execute_script("arguments[0].scrollIntoView();", password_input)
    password_input.clear()
    password_input.send_keys(password)
    submit = find_optional_element(driver, LOGIN_BUTTON_SELECTORS)
    if submit is not None and _visible(submit):
        driver.execute_script("arguments[0].click();", submit)
    else:
        password_input.send_keys(Keys.RETURN)

    WebDriverWait(driver, WAIT_TIMEOUT).until(lambda d: not _is_login_page(d))
    next_value = parse_qs(urlparse(login_target).query).get("next", [None])[0]
    if next_value:
        driver.get(urljoin(login_target, unquote(next_value)))
    log.info("Logged in")


def confirm_formation(driver) -> None:
    button = find_element(driver, CONFIRM_BUTTON_SELECTORS)
    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", button)
    driver.execute_script("arguments[0].click();", button)
    log.info("Save formation button clicked")

    try:
        find_element(driver, SUCCESS_SELECTORS, timeout=15)
        log.info("Success confirmed (confirmation message shown)")
    except TimeoutException:
        log.warning("No success message detected, assuming the click went through")


def check_lineup_state(driver) -> bool | None:
    slots = driver.find_elements(By.CSS_SELECTOR, "ui-lineup-slot[data-lineup-slot]")
    starter_slots = [
        slot
        for slot in slots
        if not (slot.get_attribute("data-lineup-slot") or "").startswith("-1:")
    ]
    if starter_slots:
        occupied = sum(
            bool(slot.find_elements(By.CSS_SELECTOR, ".player-name, ui-player-card"))
            for slot in starter_slots
        )
        if occupied == len(starter_slots) and len(starter_slots) >= 11:
            log.info("Lineup detected as already set (%d starters)", occupied)
            return False
        if occupied == 0:
            log.info("Lineup detected as empty")
            return True
        log.info("Lineup is partial (%d/%d starters)", occupied, len(starter_slots))
        return None

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
    page_name = driver.current_url.split('/')[-1].split('?')[0] or 'page'
    path = directory / f"{label}_{page_name}.html"
    path.write_text(driver.page_source, encoding="utf-8")
    try:
        driver.save_screenshot(str(directory / f"{label}_{page_name}.png"))
    except WebDriverException:
        log.warning("Could not save screenshot for %s", label)
    (directory / f"{label}_{page_name}.txt").write_text(
        "URL: " + driver.current_url + "\n"
        "TITLE: " + driver.title + "\n\n"
        + driver.find_element(By.TAG_NAME, "body").text,
        encoding="utf-8",
    )
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
    # The current site uses G/D/M/F; the bot's scoring model uses G/D/C/A.
    for role, aliases in (("G", ("g", "p", "por")), ("D", ("d", "def")), ("C", ("c", "m", "cen", "mid")), ("A", ("a", "f", "att", "fw"))):
        if normalized.strip() in aliases:
            return role
    for role in ("G", "D", "C", "A"):
        if role in normalized.upper():
            return role
    if "M" in normalized.upper():
        return "C"
    if "F" in normalized.upper():
        return "A"
    return "?"


def _role_text(row) -> str:
    element = find_optional_element(row, ROLE_IN_ROW_SELECTORS)
    values = [element.text] if element is not None else []
    for attribute in ("role", "roles", "data-role", "aria-label"):
        value = (element or row).get_attribute(attribute)
        if value:
            values.append(value)
    if not values:
        values.append(row.text)
    return " ".join(values)


def _name_text(row) -> str:
    element = find_optional_element(row, NAME_IN_ROW_SELECTORS)
    if element is not None:
        value = element.get_attribute("data-player-name") or element.text
        if _text(value):
            return _text(value)
    return _text(row.text.splitlines()[0] if row.text else "")


def fetch_league_data(driver, url: str, debug_dir: str | None = None) -> LeagueData:
    url = normalize_league_url(url)
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
        log.error(
            "Formation DOM counts: view-lineup=%d ui-player-card=%d "
            "ui-lineup-slot=%d data-player-id=%d formation=%d",
            len(driver.find_elements(By.CSS_SELECTOR, "view-lineup")),
            len(driver.find_elements(By.CSS_SELECTOR, "ui-player-card")),
            len(driver.find_elements(By.CSS_SELECTOR, "ui-lineup-slot")),
            len(driver.find_elements(By.CSS_SELECTOR, "[data-player-id]")),
            len(driver.find_elements(By.ID, "formation")),
        )
        save_snapshot(driver, debug_dir, "extract_failed")
        raise ExtractionError(
            "Could not find player rows in the formation page. "
            f"Snapshot saved to {debug_dir or 'disabled'} - run locally with "
            "--dry-run and share the debug/ output to map the selectors."
        )

    for row in rows:
        name = _name_text(row)
        if not name:
            continue
        vote_el = find_optional_element(row, VOTE_IN_ROW_SELECTORS)
        players.append(
            Player(
                name=name,
                role=_parse_role(_role_text(row)),
                votes=_parse_votes(vote_el.text) if vote_el else [],
            )
        )

    if not players:
        save_snapshot(driver, debug_dir, "extract_empty")
        raise ExtractionError("No players parsed from the formation page.")

    log.info("Parsed %d players from the formation page", len(players))
    return LeagueData(name=urlparse(url).path.split("/")[1], url=url, players=players,
                      lineup_empty=check_lineup_state(driver))


def _find_player_cards(driver):
    cards = driver.find_elements(By.CSS_SELECTOR, "view-lineup ui-player-card")
    if not cards:
        cards = driver.find_elements(By.CSS_SELECTOR, "ui-player-card")
    return [card for card in cards if _visible(card)]


def _card_name(card) -> str:
    element = find_optional_element(card, NAME_IN_ROW_SELECTORS)
    if element is not None:
        value = element.get_attribute("data-player-name") or element.text
        if _text(value):
            return _text(value)
    return _text(card.text.splitlines()[0] if card.text else "")


def _find_player_card(driver, player_name: str):
    wanted = _text(player_name).casefold()
    cards = _find_player_cards(driver)
    exact = [card for card in cards if _card_name(card).casefold() == wanted]
    if exact:
        return exact[0]
    # Player names can include an extra team abbreviation in the current card.
    partial = [
        card for card in cards
        if wanted and wanted in _card_name(card).casefold()
    ]
    if len(partial) == 1:
        return partial[0]
    return None


def _player_in_starters(driver, player_name: str) -> bool:
    wanted = _text(player_name).casefold()
    slots = driver.find_elements(By.CSS_SELECTOR, "ui-lineup-slot[data-lineup-slot]")
    for slot in slots:
        marker = slot.get_attribute("data-lineup-slot") or ""
        if marker.startswith("-1:"):
            continue
        names = slot.find_elements(By.CSS_SELECTOR, ".player-name")
        if any(wanted == _text(name.text).casefold() for name in names):
            return True
    return False


def _click_player_card(driver, player_name: str) -> None:
    card = _find_player_card(driver, player_name)
    if card is None:
        raise LineupUIError(
            f"Could not find {player_name!r} in the current player list. "
            "The page may still be loading or the site selectors may have changed."
        )
    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", card)
    try:
        ActionChains(driver).double_click(card).perform()
    except WebDriverException:
        # A JS double-click is a useful fallback on headless runners where the
        # native action can be intercepted by an overlay.
        driver.execute_script(
            "arguments[0].dispatchEvent(new MouseEvent('dblclick', "
            "{bubbles: true, cancelable: true, view: window}));",
            card,
        )


def _select_formation(driver, formation: str) -> None:
    label = formation.strip().replace("-", "-")
    compact = label.replace("-", "")
    # The current Angular UI exposes the selector as ui-chip-selector. Clicking
    # it reveals ui-chip options; older pages may render the same options as
    # buttons or role=option elements.
    for element in driver.find_elements(By.CSS_SELECTOR, "ui-chip-selector, ui-chip-list"):
        if _visible(element):
            try:
                element.click()
            except WebDriverException:
                driver.execute_script("arguments[0].click();", element)
            break

    candidates = driver.find_elements(
        By.XPATH,
        f"//*[normalize-space(.)='{label}' or normalize-space(.)='{compact}']",
    )
    for candidate in candidates:
        if _visible(candidate) and candidate.tag_name.lower() in {
            "button", "ui-chip", "ui-chip-option", "li", "span", "div"
        }:
            try:
                candidate.click()
            except WebDriverException:
                driver.execute_script("arguments[0].click();", candidate)
            log.info("Selected formation %s", label)
            return

    # If the requested formation is already active, the selector may not have
    # rendered an option. Continue and let the role-aware placement validate it.
    if label in _text(driver.find_element(By.TAG_NAME, "body").text):
        log.info("Formation %s already active", label)
        return
    raise LineupUIError(f"Could not select formation {label!r} in the lineup UI")


def _select_captain(driver, player_name: str, slot: int) -> None:
    slots = driver.find_elements(
        By.CSS_SELECTOR, f"ui-lineup-captain [data-captain-slot='{slot}']"
    )
    if not slots or not _visible(slots[0]):
        raise LineupUIError(
            "The league does not expose a selectable captain/vice slot, or the "
            "lineup page has not finished loading."
        )
    target = slots[0]
    if _text(player_name).casefold() in _text(target.text).casefold():
        return
    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", target)
    target.click()
    card = _find_player_card(driver, player_name)
    if card is None:
        raise LineupUIError(f"Could not find {player_name!r} to designate it")
    card.click()
    WebDriverWait(driver, WAIT_TIMEOUT).until(
        lambda d: _text(
            d.find_elements(
                By.CSS_SELECTOR, f"ui-lineup-captain [data-captain-slot='{slot}']"
            )[0].text
        ).casefold().find(_text(player_name).casefold()) >= 0
    )


def set_lineup(driver, picked) -> None:
    """Apply a PickedSquad through the current Fantacalcio Angular UI."""
    _select_formation(driver, picked.formation)

    WebDriverWait(driver, WAIT_TIMEOUT).until(
        lambda d: len(d.find_elements(By.CSS_SELECTOR, "ui-lineup-slot[data-lineup-slot]")) >= 11
    )
    for player in picked.starters:
        if _player_in_starters(driver, player.name):
            continue
        _click_player_card(driver, player.name)
        try:
            WebDriverWait(driver, WAIT_TIMEOUT).until(
                lambda d, name=player.name: _player_in_starters(d, name)
            )
        except TimeoutException as exc:
            raise LineupUIError(
                f"Double-clicking {player.name!r} did not place the player in the XI"
            ) from exc

    if not all(_player_in_starters(driver, player.name) for player in picked.starters):
        raise LineupUIError("The lineup UI did not contain all 11 selected starters")

    _select_captain(driver, picked.captain.name, 0)
    if picked.vice is not None:
        _select_captain(driver, picked.vice.name, 1)
    log.info("Lineup players and captaincy applied")
