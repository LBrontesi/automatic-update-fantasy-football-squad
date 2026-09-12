from __future__ import annotations

import json
import logging
import math
import re
import time
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
FANTACALCIO_APP_KEY = "ICiELOObd5DF5uJEATi77CRvHiiRuMU0"

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
    (By.XPATH, "//button[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'salva formazione') or contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'save lineup') ]"),
    (By.XPATH, "//*[@id='formation']//button[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'conferma') or contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'invia')]"),
    (By.XPATH, "//*[@id='formation']/div[2]/div[5]/button[1]"),
]

CLEAR_LINEUP_BUTTON_SELECTORS = [
    (By.XPATH, "//footer//button[.//nz-icon[@aria-label='mi:delete'] or .//*[contains(@aria-label, 'delete')]]"),
    (By.XPATH, "//button[contains(@title, 'Svuota') or contains(@title, 'Reset') or contains(@aria-label, 'delete')]") ,
]

PRIVACY_DISMISS_SELECTORS = [
    (By.ID, "pt-close"),
    (By.XPATH, "//button[contains(@aria-label, 'Continue without accepting') or contains(normalize-space(.), 'Continue without accepting') or contains(normalize-space(.), 'Continua senza accettare') ]"),
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

ROSTER_OPEN_SELECTORS = [
    (
        By.XPATH,
        "//view-lineup//button[normalize-space(.)='Rosa' or normalize-space(.)='Roster']",
    ),
    (
        By.XPATH,
        "//button[.//span[normalize-space()='Rosa'] or normalize-space()='Rosa' "
        "or .//span[normalize-space()='Roster'] or normalize-space()='Roster']",
    ),
]

PLAYER_CARD_SELECTORS = [
    "view-lineup ui-player-card",
    "ui-player-card",
    "ui-player-list-item",
    "ui-player-row",
    "[data-player-id]",
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
    driver = webdriver.Chrome(options=options)
    try:
        driver.execute_cdp_cmd("Network.enable", {})
    except WebDriverException:
        log.debug("Could not enable Chrome network inspection")
    return driver


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


def dismiss_privacy_banner(driver) -> None:
    """Dismiss the optional consent overlay that can block lineup controls."""
    button = find_optional_element(driver, PRIVACY_DISMISS_SELECTORS)
    if button is None or not _visible(button):
        return
    try:
        driver.execute_script("arguments[0].click();", button)
        log.info("Privacy banner dismissed")
    except WebDriverException:
        log.debug("Privacy banner was already gone")


def _check_lineup_state_once(driver) -> bool | None:
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


def check_lineup_state(driver) -> bool | None:
    """Read lineup state, tolerating Angular replacing slot elements while loading."""
    for attempt in range(3):
        try:
            return _check_lineup_state_once(driver)
        except StaleElementReferenceException:
            if attempt == 2:
                raise
            log.info("Lineup DOM changed while loading; retrying state detection")
            time.sleep(0.5)
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


def _performance_messages(driver) -> list[dict]:
    messages = []
    try:
        entries = driver.get_log("performance")
    except Exception:
        return messages
    for entry in entries:
        try:
            outer = json.loads(entry.get("message", "{}"))
            message = outer.get("message", {})
            if message:
                messages.append(message)
        except (TypeError, json.JSONDecodeError):
            continue
    return messages


def _capture_api_json(driver, url_fragment: str, messages: list[dict] | None = None):
    """Read a JSON response already captured by Chrome's network log."""
    for message in messages or _performance_messages(driver):
        if message.get("method") != "Network.responseReceived":
            continue
        params = message.get("params", {})
        response = params.get("response", {})
        response_url = response.get("url", "")
        if url_fragment not in response_url or response.get("status", 0) >= 400:
            continue
        try:
            body = driver.execute_cdp_cmd(
                "Network.getResponseBody",
                {"requestId": params["requestId"]},
            ).get("body", "")
            return json.loads(body)
        except (KeyError, json.JSONDecodeError, WebDriverException) as exc:
            log.debug("Could not read API response %s: %s", url_fragment, exc)
    return None


def _capture_api_headers(
    driver, url_fragment: str, messages: list[dict] | None = None
) -> dict[str, str]:
    """Reuse auth headers from the Angular request, without logging values."""
    result = {}
    for message in messages or _performance_messages(driver):
        if message.get("method") != "Network.requestWillBeSent":
            continue
        request = message.get("params", {}).get("request", {})
        if url_fragment not in request.get("url", ""):
            continue
        for key, value in (request.get("headers") or {}).items():
            if key.lower() in {"authorization", "app_key", "token"} and value:
                result[key] = value
        if result:
            return result
    return result


def _fetch_api_json(driver, url: str, auth_headers: dict[str, str] | None = None):
    """Fetch a same-session API response from the authenticated page context."""
    script = """
    const [url, appKey, suppliedHeaders, done] = [
      arguments[0], arguments[1], arguments[2], arguments[arguments.length - 1]
    ];
    const findToken = (value, depth = 0, seen = new Set()) => {
      if (depth > 6 || value === null || value === undefined) return null;
      if (typeof value === 'string') {
        const trimmed = value.trim().replace(/^Bearer\\s+/i, '');
        return trimmed.split('.').length === 3 ? trimmed : null;
      }
      if (typeof value !== 'object' || seen.has(value)) return null;
      seen.add(value);
      for (const [key, child] of Object.entries(value)) {
        if (/^(token|accessToken|access_token|jwt)$/i.test(key) && typeof child === 'string' && child.trim()) {
          return child.trim().replace(/^Bearer\\s+/i, '');
        }
        const nested = findToken(child, depth + 1, seen);
        if (nested) return nested;
      }
      return null;
    };
    let token = null;
    for (const storage of [window.localStorage, window.sessionStorage]) {
      for (let index = 0; index < storage.length && !token; index += 1) {
        const raw = storage.getItem(storage.key(index));
        if (!raw) continue;
        try { token = findToken(JSON.parse(raw)); } catch (_) { token = findToken(raw); }
      }
    }
    const headers = Object.assign(
      {Accept: 'application/json', app_key: appKey}, suppliedHeaders || {}
    );
    if (!headers.Authorization && !headers.authorization && token) {
      headers.Authorization = `Bearer ${token}`;
    }
    fetch(url, {credentials: 'include', headers})
      .then(async response => ({
        status: response.status,
        content_type: response.headers.get('content-type') || '',
        body_length: Number(response.headers.get('content-length') || 0),
        body: await response.text(),
      }))
      .then(done)
      .catch(error => done({error: String(error)}));
    """
    try:
        result = driver.execute_async_script(
            script, url, FANTACALCIO_APP_KEY, auth_headers or {}
        )
        log.info(
            "League API fetch status=%s content_type=%s body_length=%s auth_header=%s",
            result.get("status", "unknown"),
            result.get("content_type", "unknown"),
            result.get("body_length", "unknown"),
            "present" if auth_headers or result.get("status") else "unknown",
        )
        if result.get("status", 500) >= 400 or result.get("error"):
            if result.get("error"):
                log.warning("League API fetch failed: browser request error")
            return None
        return json.loads(result.get("body", ""))
    except (AttributeError, json.JSONDecodeError, WebDriverException) as exc:
        log.debug("Could not fetch API response %s: %s", url, exc)
        return None


def _payload_shape(value, depth: int = 0):
    if depth >= 4:
        return type(value).__name__
    if isinstance(value, dict):
        return {
            str(key): _payload_shape(child, depth + 1)
            for key, child in list(value.items())[:30]
        }
    if isinstance(value, list):
        return {"list_length": len(value), "first": _payload_shape(value[0], depth + 1) if value else None}
    return type(value).__name__


def _payload_metric_summary(payload) -> dict[str, dict[str, int | float | None]]:
    """Summarize populated numeric player metrics without exposing values/names."""
    fields = (
        "quotd", "fvmfc", "fvmma", "agrd", "fagrd", "aagr", "faagr",
        "agit", "fagit", "mspv", "l5rfc", "l5ral", "l5rit", "l5frfc",
        "l5fral", "l5frit",
    )
    players = payload.get("players", []) if isinstance(payload, dict) else []
    summary = {}
    for field in fields:
        values = []
        for record in players if isinstance(players, list) else []:
            value = _field_value(record, {field}) if isinstance(record, dict) else None
            values.extend(number for number in _numeric_values(value) if math.isfinite(number))
        positive = [number for number in values if number > 0]
        summary[field] = {
            "positive": len(positive),
            "max": max(positive) if positive else None,
        }
    return summary


def _walk_dicts(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_dicts(child)


def _key_name(value) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def _value_text(value) -> str:
    if isinstance(value, str):
        return _text(value)
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, dict):
        for key in ("code", "shortName", "name", "label", "description", "value", "text"):
            if key in value:
                result = _value_text(value[key])
                if result:
                    return result
        return ""
    if isinstance(value, list):
        return " ".join(result for result in (_value_text(item) for item in value) if result)
    return ""


def _field_value(record: dict, names: set[str]):
    for key, value in record.items():
        if _key_name(key) in names:
            return value
    return None


def _numeric_values(value) -> list[float]:
    if isinstance(value, (int, float)):
        return [float(value)]
    if isinstance(value, list):
        values = []
        for item in value:
            values.extend(_numeric_values(item))
        return values
    if isinstance(value, dict):
        for key in ("votes", "ratings", "scores", "values", "history", "last5"):
            if key in value:
                return _numeric_values(value[key])
        return []
    if isinstance(value, str):
        return _parse_votes(value)
    return []


def _parse_api_players(payload) -> list[Player]:
    """Convert the league API's player records into the scoring model."""
    name_keys = {
        "name", "playername", "fullname", "displayname", "nome",
        "nomegiocatore", "shortname",
    }
    role_keys = {
        "role", "roles", "position", "positioncode", "rolecode",
        "playerrole", "ruolo", "mantrarole", "fcrle",
    }
    vote_keys = {
        "votes", "vote", "ratings", "rating", "scores", "score",
        "lastvotes", "last5", "history", "voti", "fantavoto", "media",
    }
    players = []
    seen = set()
    for record in _walk_dicts(payload):
        name = _value_text(_field_value(record, name_keys))
        if name.endswith("*"):
            continue
        role_raw = _value_text(_field_value(record, role_keys))
        role = _parse_role(role_raw) if role_raw else "?"
        if not name or role == "?":
            continue
        key = (name.casefold(), role)
        if key in seen:
            continue
        seen.add(key)
        votes = []
        # The live endpoint exposes normalized season averages (fagrd/faagr)
        # rather than five individual matchday votes. Use those before the
        # generic historical fields; its l5* trend fields are encoded values,
        # not direct 0-10 ratings, and must not be scored as votes.
        for field_name in ("fagrd", "faagr", "fagit", "agrd", "aagr", "agit"):
            value = _field_value(record, {field_name})
            votes.extend(vote for vote in _numeric_values(value) if vote > 0)
            if votes:
                break
        if not votes:
            for field, value in record.items():
                if _key_name(field) in vote_keys:
                    votes.extend(vote for vote in _numeric_values(value) if vote > 0)
        if not votes:
            # Before the first matchday the performance metrics are empty.
            # Use the platform's current quotation as a data-backed fallback,
            # normalized to the same rough scale as a fantasy vote.
            quotation = _field_value(record, {"quotd"})
            quotation_values = [
                value for value in _numeric_values(quotation) if value > 0
            ]
            if quotation_values:
                votes = [quotation_values[0] / 10.0]
        player_id = _field_value(record, {"id"})
        try:
            player_id = int(player_id) if player_id is not None else None
        except (TypeError, ValueError):
            player_id = None
        players.append(
            Player(
                name=name,
                role=role,
                votes=votes[-5:],
                external_id=player_id,
            )
        )
    if players:
        log.info("Parsed %d players from the league API", len(players))
    return players


def _parse_my_roster_ids(payload) -> set[int]:
    """Extract owned player IDs from the authenticated /league/teams/my API."""
    player_ids: set[int] = set()
    for record in _walk_dicts(payload):
        calendar = _field_value(record, {"cal"})
        if isinstance(calendar, str):
            for value in calendar.split(";"):
                try:
                    if value.strip():
                        player_ids.add(int(value))
                except ValueError:
                    continue
        loans = _field_value(record, {"pl"})
        for loan in _walk_dicts(loans):
            loan_id = _field_value(loan, {"id"})
            try:
                if loan_id is not None:
                    player_ids.add(int(loan_id))
            except (TypeError, ValueError):
                continue
        for key, value in record.items():
            if _key_name(key) not in {"playerid", "player_id"}:
                continue
            try:
                player_ids.add(int(value))
            except (TypeError, ValueError):
                continue
    return player_ids


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
    if normalized.strip() in {"1", "p", "por"}:
        return "G"
    if normalized.strip() == "2":
        return "D"
    if normalized.strip() == "3":
        return "C"
    if normalized.strip() == "4":
        return "A"
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
    dismiss_privacy_banner(driver)
    find_element(driver, FORMATION_CONTAINER_SELECTORS)

    # The current lineup route renders the selected XI and bench, not the
    # complete roster. If a lineup already exists, the caller only needs to
    # confirm it; trying to extract player rows first incorrectly fails on
    # this page because the available-player list is not rendered there.
    try:
        WebDriverWait(driver, WAIT_TIMEOUT).until(
            lambda d: d.find_elements(
                By.CSS_SELECTOR, "ui-lineup-slot[data-lineup-slot]"
            )
        )
    except TimeoutException:
        log.warning("Lineup slots did not render before the extraction timeout")
    api_messages = _performance_messages(driver)
    api_headers = _capture_api_headers(
        driver, "/onboarding/v1/league/players", api_messages
    )
    if api_headers:
        log.info("Reusing league API auth headers: %s", sorted(api_headers))
    api_payload = _capture_api_json(
        driver, "/onboarding/v1/league/players", api_messages
    )
    if api_payload is None:
        api_payload = _fetch_api_json(
            driver,
            "https://apileague.fantacalcio.it/onboarding/v1/league/players",
            auth_headers=api_headers,
        )
    if api_payload is not None:
        log.info("League API payload shape: %s", json.dumps(_payload_shape(api_payload)))
        log.info("League API metric summary: %s", json.dumps(_payload_metric_summary(api_payload)))
    api_players = _parse_api_players(api_payload)
    if api_players:
        my_team_payload = _fetch_api_json(
            driver,
            "https://apileague.fantacalcio.it/onboarding/v1/league/teams/my",
            auth_headers=api_headers,
        )
        roster_ids = _parse_my_roster_ids(my_team_payload)
        if roster_ids:
            api_players = [
                player for player in api_players if player.external_id in roster_ids
            ]
            log.info("Filtered live player pool to %d owned players", len(api_players))
        else:
            log.warning("Could not determine the owned roster from the league API")
            api_players = []
    lineup_empty = check_lineup_state(driver)
    if lineup_empty is False and not api_players:
        return LeagueData(
            name=urlparse(url).path.split("/")[1],
            url=url,
            lineup_empty=False,
        )

    players: list[Player] = api_players
    rows = None
    if not players:
        for by, selector in PLAYER_ROW_SELECTORS:
            try:
                rows = WebDriverWait(driver, WAIT_TIMEOUT).until(
                    EC.presence_of_all_elements_located((by, selector))
                )
            except TimeoutException:
                continue
            if rows:
                break

    if not players and not rows:
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

    if not players:
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
    return LeagueData(
        name=urlparse(url).path.split("/")[1],
        url=url,
        players=players,
        lineup_empty=lineup_empty,
    )


def _find_player_cards(driver):
    cards = []
    for selector in PLAYER_CARD_SELECTORS:
        cards.extend(driver.find_elements(By.CSS_SELECTOR, selector))
    unique = {getattr(card, "id", id(card)): card for card in cards}
    return [card for card in unique.values() if _visible(card)]


def _open_roster_picker(driver, debug_dir: str | None = None):
    cards = _find_player_cards(driver)
    if cards:
        return cards
    button = find_optional_element(driver, ROSTER_OPEN_SELECTORS)
    if button is None or not _visible(button):
        raise LineupUIError(
            "Could not open the roster picker: no visible Rosa/Roster button found."
        )
    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", button)
    log.info("Opening roster picker via button text=%r", _text(button.text))
    def drawer_open(d):
        for wrapper in d.find_elements(By.CSS_SELECTOR, ".ant-drawer-content-wrapper"):
            if not _visible(wrapper):
                continue
            transform = (wrapper.value_of_css_property("transform") or "").replace(" ", "")
            if transform and "-100%" not in transform:
                return True
        return False

    # WebDriver click preserves the browser's real pointer/focus event
    # sequence. Angular's drawer trigger is less reliable with a plain DOM
    # .click() on headless Chrome, so keep DOM and pointer fallbacks.
    click_methods = [
        lambda: button.click(),
        lambda: ActionChains(driver).move_to_element(button).click().perform(),
        lambda: driver.execute_script("arguments[0].click();", button),
    ]
    opened = False
    for click in click_methods:
        try:
            click()
            WebDriverWait(driver, 5).until(drawer_open)
            opened = True
            break
        except (TimeoutException, WebDriverException):
            continue

    if not opened:
        save_snapshot(driver, debug_dir, "roster_picker_not_open")
        raise LineupUIError(
            "Clicking the Rosa/Roster button did not open the roster drawer."
        )
    try:
        WebDriverWait(driver, WAIT_TIMEOUT).until(
            lambda d: bool(_find_player_cards(d))
        )
    except TimeoutException as exc:
        log.warning("Roster drawer opened but no known player-card selector matched")
        save_snapshot(driver, debug_dir, "roster_picker_empty")
        raise LineupUIError(
            "The roster picker opened without rendering selectable player cards."
        ) from exc
    return _find_player_cards(driver)


def clear_lineup(driver) -> None:
    button = find_element(driver, CLEAR_LINEUP_BUTTON_SELECTORS)
    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", button)
    driver.execute_script("arguments[0].click();", button)
    WebDriverWait(driver, WAIT_TIMEOUT).until(
        lambda d: all(
            not slot.find_elements(By.CSS_SELECTOR, ".player-name, ui-player-card")
            for slot in d.find_elements(
                By.CSS_SELECTOR, "ui-lineup-slot[data-lineup-slot]"
            )
            if not (slot.get_attribute("data-lineup-slot") or "").startswith("-1:")
        )
    )
    log.info("Existing lineup cleared")


def _card_name(card) -> str:
    element = find_optional_element(card, NAME_IN_ROW_SELECTORS)
    if element is not None:
        value = element.get_attribute("data-player-name") or element.text
        if _text(value):
            return _text(value)
    return _text(card.text.splitlines()[0] if card.text else "")


def _find_player_card(driver, player_name: str):
    wanted = _text(player_name).casefold()
    cards = _open_roster_picker(driver)
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
    for element in driver.find_elements(
        By.CSS_SELECTOR, "ui-chip-selector > button, ui-chip-selector button, ui-chip-list"
    ):
        if _visible(element):
            try:
                driver.execute_script("arguments[0].click();", element)
            except WebDriverException:
                driver.execute_script("arguments[0].click();", element)
            break

    def find_option(d):
        candidates = d.find_elements(
            By.XPATH,
            f"//*[normalize-space(.)='{label}' or normalize-space(.)='{compact}']",
        )
        return next((candidate for candidate in candidates if _visible(candidate)), None)

    try:
        candidate = WebDriverWait(driver, WAIT_TIMEOUT).until(find_option)
        driver.execute_script(
            "const item = arguments[0]; "
            "const target = item.closest('button,[role=option],li,ui-chip') || item; "
            "target.click();",
            candidate,
        )
        log.info("Selected formation %s", label)
        return
    except TimeoutException:
        pass

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


def set_lineup(driver, picked, debug_dir: str | None = None) -> None:
    """Apply a PickedSquad through the current Fantacalcio Angular UI."""
    _select_formation(driver, picked.formation)
    _open_roster_picker(driver, debug_dir=debug_dir)

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
