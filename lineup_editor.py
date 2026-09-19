"""Read and edit the current league's rendered Classic roster.

Roster membership and statistics come from the same drawer used to place
players. No account-wide API token or other league's cached response is reused.
"""
from __future__ import annotations

import logging
import math
import re

from selenium.common.exceptions import StaleElementReferenceException, TimeoutException, WebDriverException
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

from player_data import (
    WAIT_TIMEOUT, ExtractionError, LineupUIError, _text, _visible,
    check_lineup_locked, check_lineup_state, dismiss_privacy_banner,
    normalize_league_url, save_snapshot,
)
from predictions import LeagueData
from squad_picker import Player, PickedSquad, parse_formation
from urllib.parse import urlparse

log = logging.getLogger("fantasquad")
ROSTER = ".ant-drawer-open ui-player-card, view-lineup ui-player-card"
SLOTS = "view-lineup ui-lineup-slot[data-lineup-slot]"
FORMATION = "view-lineup ui-chip-selector > button"


def _wait(driver, predicate, message):
    try:
        return WebDriverWait(driver, WAIT_TIMEOUT, ignored_exceptions=(StaleElementReferenceException,)).until(predicate)
    except TimeoutException as exc:
        raise LineupUIError(message) from exc


def _click(driver, element):
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", element)
    element.click()


def close_roster(driver):
    for button in driver.find_elements(By.CSS_SELECTOR, ".ant-drawer-open .ant-drawer-close"):
        if _visible(button):
            _click(driver, button)
    _wait(driver, lambda d: not d.find_elements(By.CSS_SELECTOR, ".ant-drawer-open"),
          "Roster drawer did not close")


def open_roster(driver):
    cards = [c for c in driver.find_elements(By.CSS_SELECTOR, ROSTER) if _visible(c)]
    if not cards:
        buttons = driver.find_elements(By.XPATH, "//view-lineup//button[normalize-space(.)='Rosa' or normalize-space(.)='Roster']")
        if not buttons:
            raise LineupUIError("No roster button on this league's lineup page")
        if not driver.find_elements(By.CSS_SELECTOR, ".ant-drawer-open"):
            _click(driver, buttons[0])
        _wait(driver, lambda d: any(_visible(c) for c in d.find_elements(By.CSS_SELECTOR, ROSTER)),
              "This league's roster drawer has no selectable players")
    # Grouping can hide already selected starters/reserves in collapsed groups.
    for label in driver.find_elements(By.CSS_SELECTOR, ".ant-drawer-open label.ant-checkbox-wrapper"):
        if _text(label.text) == "Raggruppa":
            checkbox = label.find_element(By.CSS_SELECTOR, "input")
            if checkbox.is_selected():
                _click(driver, label)
    return [c for c in driver.find_elements(By.CSS_SELECTOR, ROSTER) if _visible(c)]


def _number(value):
    try:
        result = float(str(value).strip().replace(",", "."))
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def player_from_record(record: dict) -> Player:
    """Parse only labelled metrics; FM and MV are not sequential match votes."""
    name = _text(record.get("name"))
    roles = {"P": "G", "G": "G", "D": "D", "C": "C", "A": "A"}
    role = roles.get(record.get("role"))
    if not name or role is None or str(record.get("game_type")) != "1":
        raise ExtractionError("Unsupported or incomplete roster card (Classic leagues only)")
    rating, source = None, "neutral prior (no ratings)"
    for key, label in (("FM", "fantasy average"), ("MV", "average rating")):
        value = _number(record.get("metrics", {}).get(key))
        if value is not None and 0 < value <= 20:
            rating, source = value, label
            break
    percent = _number(record.get("probability"))
    probability = percent / 100 if percent is not None and 0 <= percent <= 100 else None
    status = _text(record.get("status_text")).lower()
    unavailable = bool(re.search(r"\b(squalificato|infortunato|indisponibile|suspended|injured)\b", status))
    try:
        player_id = int(record["id"])
    except (KeyError, ValueError, TypeError) as exc:
        raise ExtractionError(f"Missing player ID on {name}'s card") from exc
    return Player(name, role, [rating if rating is not None else 6.0],
                  external_id=player_id, start_probability=probability,
                  unavailable=unavailable, rating_source=source)


def read_roster(driver):
    open_roster(driver)
    records = driver.execute_script("""
      const cards = [...document.querySelectorAll(arguments[0])];
      return cards.map(card => {
        const region = card.closest('.ant-drawer-body') || card.closest('view-lineup');
        const labels = [...region.querySelectorAll('ui-list-sort-head button')]
          .map(x => x.textContent.trim());
        const stats = [...card.querySelectorAll('.stats > span')]
          .map(x => x.textContent.trim()).filter(x => /^[+-]?\\d+(?:[.,]\\d+)?$/.test(x));
        const status = card.querySelector('ui-player-status-icon');
        return {
          id: card.querySelector('nz-card[data-id]')?.getAttribute('data-id'),
          name: card.querySelector('.player-name .truncate, .player-name')?.textContent,
          role: card.querySelector('ui-player-role [data-role]')?.getAttribute('data-role'),
          game_type: card.querySelector('ui-player-role')?.getAttribute('data-game-type'),
          metrics: labels.length === stats.length ? Object.fromEntries(labels.map((x,i) => [x,stats[i]])) : {},
          probability: card.querySelector('ui-next-match-progress [role=progressbar]')?.getAttribute('aria-valuenow'),
          status_text: status ? [status.getAttribute('title'),status.getAttribute('aria-label'),status.textContent].filter(Boolean).join(' ') : ''
        };
      });
    """, ROSTER)
    players = [player_from_record(record) for record in records]
    ids = [p.external_id for p in players]
    if len(ids) < 11 or len(ids) != len(set(ids)):
        raise ExtractionError("Roster is incomplete or contains duplicate player IDs")
    if not any(p.rating_source != "neutral prior (no ratings)" for p in players):
        raise ExtractionError("No labelled FM/MV statistics in this roster; refusing arbitrary selection")
    log.info("Read %d owned players from this league's roster drawer", len(players))
    return players


def _formation_options(driver):
    return [el for el in driver.find_elements(By.CSS_SELECTOR,
            "ui-chip-selector button, ui-chip-selector ui-chip, .cdk-overlay-container button, .cdk-overlay-container [role=option]")
            if _visible(el) and re.fullmatch(r"[3-5]-[3-5]-[1-3]", _text(el.text))]


def allowed_formations(driver):
    close_roster(driver)
    trigger = driver.find_element(By.CSS_SELECTOR, FORMATION)
    current = _text(trigger.text)
    parse_formation(current)
    if trigger.get_attribute('disabled') is not None or trigger.get_attribute('aria-disabled') == 'true':
        return [current]
    _click(driver, trigger)
    try:
        _wait(driver, lambda d: len(_formation_options(d)) > 1, "Could not read this league's formation options")
        choices = sorted({_text(el.text) for el in _formation_options(driver)})
        for choice in choices:
            parse_formation(choice)
        return choices
    finally:
        # Click the trigger again only when its options are open.
        if len(_formation_options(driver)) > 1:
            _click(driver, driver.find_element(By.CSS_SELECTOR, FORMATION))


def select_formation(driver, formation):
    close_roster(driver)
    if _text(driver.find_element(By.CSS_SELECTOR, FORMATION).text) == formation:
        return
    _click(driver, driver.find_element(By.CSS_SELECTOR, FORMATION))
    option = _wait(driver, lambda d: next((e for e in _formation_options(d) if _text(e.text) == formation), None),
                   f"Formation {formation} not available")
    _click(driver, option)
    _wait(driver, lambda d: _text(d.find_element(By.CSS_SELECTOR, FORMATION).text) == formation,
          f"Formation did not change to {formation}")


def read_bench_roles(driver):
    roles = []
    for slot in driver.find_elements(By.CSS_SELECTOR, SLOTS):
        if not slot.get_attribute("data-lineup-slot").startswith("-1:"):
            continue
        # The direct ui-role is the slot constraint; roles inside a player
        # card describe its occupant and must not be mistaken for a rule.
        markers = slot.find_elements(By.CSS_SELECTOR, ":scope > ui-role [data-role]")
        role = markers[0].get_attribute("data-role") if markers else "*"
        role = "G" if role == "P" else role
        if role not in ("G", "D", "C", "A", "*"):
            raise ExtractionError(f"Unsupported reserve slot role {role}")
        roles.append(role)
    return roles


def fetch_league_data(driver, url, debug_dir=None):
    url = normalize_league_url(url)
    driver.get(url)
    dismiss_privacy_banner(driver)
    _wait(driver, lambda d: d.find_elements(By.CSS_SELECTOR, SLOTS), "Lineup did not load")
    if urlparse(driver.current_url).path.rstrip('/') != urlparse(url).path.rstrip('/'):
        raise ExtractionError("The site redirected away from the requested competition")
    league = LeagueData(name=urlparse(url).path.split('/')[1], url=url)
    if check_lineup_locked(driver):
        league.lineup_locked = True
        return league
    _wait(driver, lambda d: any(_visible(b) for b in d.find_elements(By.XPATH,
          "//view-lineup//button[contains(.,'Salva formazione')]")), "No editable save control on the lineup page")
    league.lineup_empty = check_lineup_state(driver)
    league.players = read_roster(driver)
    league.allowed_formations = allowed_formations(driver)
    league.bench_roles = read_bench_roles(driver)
    return league


def read_lineup(driver):
    """Capture exactly the persisted fields that will be changed."""
    return driver.execute_script("""
      const name = el => (el.querySelector('.player-name .truncate, .player-name')?.textContent || '').trim();
      return {
        formation: document.querySelector(arguments[0])?.textContent.trim(),
        slots: Object.fromEntries([...document.querySelectorAll(arguments[1])].map(el => [el.getAttribute('data-lineup-slot'),name(el)])),
        captains: Object.fromEntries([...document.querySelectorAll('view-lineup [data-captain-slot]')].map(el => [el.getAttribute('data-captain-slot'),name(el)]))
      };
    """, FORMATION, SLOTS)


def verify_lineup(expected, actual):
    if actual != expected:
        raise LineupUIError("Saved lineup does not match the intended XI, reserves, formation or captaincy")


def assert_editable(driver):
    if check_lineup_locked(driver):
        raise LineupUIError("The matchday became locked during this run; no save attempted")


def _find_card(driver, player):
    for card in open_roster(driver):
        identifiers = card.find_elements(By.CSS_SELECTOR, "nz-card[data-id]")
        if identifiers and identifiers[0].get_attribute("data-id") == str(player.external_id):
            return card
    raise LineupUIError(f"{player.name} is absent from this league's selectable roster")


def _place_player(driver, player, bench=False):
    card = _find_card(driver, player)
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", card)
    ActionChains(driver).double_click(card).perform()
    def placed(d):
        slots = read_lineup(d)["slots"]
        return any(name == player.name for marker, name in slots.items() if marker.startswith('-1:') == bench)
    _wait(driver, placed, f"Could not place {player.name} in {'reserves' if bench else 'starters'}")


def _captain(driver, player, marker):
    close_roster(driver)
    slot = driver.find_element(By.CSS_SELECTOR, f"view-lineup [data-captain-slot='{marker}']")
    _click(driver, slot)
    card = _find_card(driver, player)
    _click(driver, card)
    _wait(driver, lambda d: read_lineup(d)["captains"].get(marker) == player.name,
          f"Could not set captain slot {marker}")


def apply_lineup(driver, picked: PickedSquad, before_save=None, debug_dir=None):
    """Validate all players before editing; failures discard the unsaved draft."""
    original_url = driver.current_url
    try:
        assert_editable(driver)
        roster_ids = {p.external_id for p in read_roster(driver)}
        if any(p.external_id not in roster_ids for p in picked.starters + picked.bench):
            raise LineupUIError("Recommendation contains players outside the current league's roster")
        close_roster(driver)
        captain_slots = read_lineup(driver)["captains"]
        if captain_slots and set(captain_slots) != {"0", "1"}:
            raise LineupUIError("Unsupported captain controls; current lineup preserved")
        from player_data import clear_lineup
        clear_lineup(driver)
        select_formation(driver, picked.formation)
        for player in picked.starters:
            _place_player(driver, player)
        for player in picked.bench:
            _place_player(driver, player, bench=True)
        close_roster(driver)
        if captain_slots:
            _captain(driver, picked.captain, "0")
            _captain(driver, picked.vice, "1")
        else:
            log.info("Captaincy is disabled in this league; no captain selection needed")
        close_roster(driver)
        expected = read_lineup(driver)
        starters = [n for m, n in expected['slots'].items() if not m.startswith('-1:')]
        bench = [n for m, n in expected['slots'].items() if m.startswith('-1:')]
        if set(starters) != {p.name for p in picked.starters} or len(starters) != 11:
            raise LineupUIError("Draft does not contain the intended XI")
        if bench != [p.name for p in picked.bench]:
            raise LineupUIError("Draft reserves do not match the intended order")
        if expected['formation'] != picked.formation:
            raise LineupUIError("Draft formation does not match recommendation")
        assert_editable(driver)
        if before_save is not None and not before_save():
            raise LineupUIError("Kickoff guard now prevents saving")
        buttons = driver.find_elements(By.XPATH, "//view-lineup//button[contains(.,'Salva formazione')]")
        if len(buttons) != 1 or not _visible(buttons[0]):
            raise LineupUIError("No unambiguous enabled save button")
        _click(driver, buttons[0])
        _wait(driver, lambda d: d.find_elements(By.CSS_SELECTOR, '.ant-message-success'),
              "Save was not acknowledged; cannot report the squad as updated")
        driver.get(original_url)
        _wait(driver, lambda d: d.find_elements(By.CSS_SELECTOR, SLOTS), "Saved lineup did not reload")
        _wait(driver, lambda d: read_lineup(d) == expected, "Reloaded lineup differs from the requested squad")
        log.info("Verified saved XI, bench order, formation and captaincy after reload")
        return expected
    except Exception:
        # Navigation discards unsubmitted changes. Never click Save on an
        # incomplete draft, and never claim a save without reading it back.
        try:
            save_snapshot(driver, debug_dir, 'draft_failed')
        except (WebDriverException, OSError):
            log.warning('Could not capture the failed draft')
        try:
            driver.get(original_url)
        except WebDriverException:
            log.warning('Could not reload after failure; session will be retried or closed')
        raise
