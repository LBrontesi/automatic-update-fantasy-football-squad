"""
submitter.py — applies the optimised lineup on fantacalcio.it via Selenium.

Flow per league:
  1. Navigate to the formation page.
  2. For each starter: find their card, drag/click them into the field.
  3. For each bench player: ensure they stay on the bench (or reset if needed).
  4. Click the save button.
  5. Confirm success toast / screenshot.
"""

import logging
import time
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    NoSuchElementException,
    TimeoutException,
    ElementNotInteractableException,
)

import config

logger = logging.getLogger(__name__)


# ── Driver ────────────────────────────────────────────────────────────────────

def _build_driver() -> webdriver.Chrome:
    opts = Options()
    if config.HEADLESS:
        opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1400,900")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    driver = webdriver.Chrome(options=opts)
    driver.implicitly_wait(8)
    return driver


# ── Login (same as scraper, kept self-contained) ──────────────────────────────

def _login(driver: webdriver.Chrome) -> None:
    from selenium.webdriver.common.keys import Keys
    logger.info("Logging in...")
    driver.get(config.LOGIN_URL)
    time.sleep(config.PAGE_LOAD_WAIT)
    try:
        btn = driver.find_element(By.XPATH, "/html/body/nav[2]/div/a[2]")
        driver.execute_script("arguments[0].click();", btn)
        time.sleep(config.PAGE_LOAD_WAIT)
    except NoSuchElementException:
        logger.warning("Login button not found — assuming already logged in.")
        return

    wait = WebDriverWait(driver, 15)
    email = wait.until(EC.presence_of_element_located(
        (By.XPATH, "//input[@type='email' or @placeholder[contains(.,'mail')]]")
    ))
    email.clear()
    email.send_keys(config.EMAIL)

    pwd = driver.find_element(By.XPATH, "//input[@type='password']")
    pwd.clear()
    pwd.send_keys(config.PASSWORD)
    pwd.send_keys(Keys.RETURN)
    time.sleep(config.PAGE_LOAD_WAIT + 1)
    logger.info("Login submitted.")


# ── Core submission logic ──────────────────────────────────────────────────────

def _find_player_card(driver: webdriver.Chrome, player_name: str):
    """
    Find a player's bench card by name.
    Tries several selector strategies; returns the element or None.
    """
    name_lower = player_name.lower()

    # Strategy 1: look for any element whose text matches the name
    candidates = driver.find_elements(
        By.XPATH,
        f"//*[contains(translate(text(),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'), '{name_lower}')]"
    )
    for el in candidates:
        # Walk up to a clickable card wrapper
        try:
            card = el.find_element(By.XPATH, "./ancestor::*[contains(@class,'player') or contains(@class,'card')][1]")
            return card
        except NoSuchElementException:
            continue

    # Strategy 2: aria-label or data-name attributes
    for attr in ["aria-label", "data-name", "data-player"]:
        try:
            card = driver.find_element(
                By.XPATH,
                f"//*[contains(translate(@{attr},'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'), '{name_lower}')]"
            )
            return card
        except NoSuchElementException:
            continue

    logger.warning(f"Could not find card for player: {player_name}")
    return None


def _find_field_slot(driver: webdriver.Chrome, role: str, slot_index: int):
    """
    Find the Nth empty field slot for the given role.
    Tries both data-role and positional CSS selectors.
    """
    # Try data-role attribute
    role_map = {"P": "goalkeeper", "D": "defender", "C": "midfielder", "A": "forward"}
    eng_role = role_map.get(role, role.lower())

    for sel in [
        f"[data-role='{role}']",
        f"[data-role='{eng_role}']",
        f"[class*='slot-{role.lower()}']",
        f"[class*='{eng_role}-slot']",
        f"[class*='field-slot'][data-position='{role}']",
    ]:
        slots = driver.find_elements(By.CSS_SELECTOR, sel)
        empty_slots = [s for s in slots if _is_empty_slot(s)]
        if len(empty_slots) > slot_index:
            return empty_slots[slot_index]

    logger.warning(f"Could not find field slot for role={role} index={slot_index}")
    return None


def _is_empty_slot(slot) -> bool:
    """Return True if the slot appears to have no player assigned yet."""
    try:
        # If there's a player name inside, it's not empty
        text = slot.text.strip()
        if text and len(text) > 2:
            return False
        cls = slot.get_attribute("class") or ""
        if "empty" in cls or "vuoto" in cls or "placeholder" in cls:
            return True
        return True  # assume empty if nothing found
    except Exception:
        return True


def _place_player(driver: webdriver.Chrome, player: dict, slot_index: int, dry_run: bool) -> bool:
    """
    Move a player from the bench to the correct field slot.
    Returns True on success.
    """
    name = player["name"]
    role = player["role"]

    if dry_run:
        logger.info(f"[DRY-RUN] Would place {name} [{role}] in slot {slot_index}")
        return True

    card = _find_player_card(driver, name)
    slot = _find_field_slot(driver, role, slot_index)

    if card is None or slot is None:
        logger.error(f"Could not place {name}: card={card is not None}, slot={slot is not None}")
        return False

    # Try drag-and-drop first
    try:
        ActionChains(driver).drag_and_drop(card, slot).perform()
        time.sleep(0.5)
        logger.info(f"Dragged {name} → {role} slot {slot_index}")
        return True
    except Exception as e:
        logger.debug(f"Drag failed for {name}: {e} — trying click approach")

    # Fallback: click card then click slot
    try:
        driver.execute_script("arguments[0].click();", card)
        time.sleep(0.3)
        driver.execute_script("arguments[0].click();", slot)
        time.sleep(0.3)
        logger.info(f"Clicked {name} → {role} slot {slot_index}")
        return True
    except ElementNotInteractableException as e:
        logger.error(f"Could not place {name}: {e}")
        return False


def _reset_formation(driver: webdriver.Chrome, dry_run: bool) -> None:
    """
    Click the 'reset' or 'svuota formazione' button if available,
    so we start from a blank slate before applying the new lineup.
    """
    reset_selectors = [
        "[class*='reset']",
        "[class*='svuota']",
        "button[aria-label*='reset' i]",
        "button[aria-label*='svuota' i]",
    ]
    for sel in reset_selectors:
        try:
            btn = driver.find_element(By.CSS_SELECTOR, sel)
            if dry_run:
                logger.info(f"[DRY-RUN] Would click reset button: {sel}")
                return
            btn.click()
            time.sleep(1)
            logger.info("Formation reset.")
            return
        except NoSuchElementException:
            continue
    logger.info("No reset button found — proceeding without resetting.")


def _click_save(driver: webdriver.Chrome, dry_run: bool) -> bool:
    """Click the save/conferma button. Returns True if found and clicked."""
    save_selectors = [
        "button[class*='save']",
        "button[class*='salva']",
        "button[class*='conferma']",
        "button[aria-label*='salva' i]",
        "button[aria-label*='save' i]",
        "//button[contains(translate(.,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'salva')]",
        "//button[contains(translate(.,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'conferma')]",
    ]

    for sel in save_selectors:
        try:
            by = By.XPATH if sel.startswith("//") else By.CSS_SELECTOR
            btn = driver.find_element(by, sel)
            if dry_run:
                logger.info(f"[DRY-RUN] Would click save button: {sel}")
                return True
            driver.execute_script("arguments[0].scrollIntoView();", btn)
            btn.click()
            logger.info("Save button clicked.")
            time.sleep(2)
            return True
        except (NoSuchElementException, ElementNotInteractableException):
            continue

    logger.error("Save button not found — lineup may not have been saved!")
    return False


def _screenshot(driver: webdriver.Chrome, label: str) -> None:
    """Save a screenshot for debugging."""
    path = Path(f"screenshot_{label}.png")
    driver.save_screenshot(str(path))
    logger.info(f"Screenshot saved → {path}")


# ── Public API ────────────────────────────────────────────────────────────────

def submit_lineup(
    lineup: dict,
    league_url: str,
    league_name: str,
    driver: webdriver.Chrome | None = None,
    dry_run: bool = False,
) -> bool:
    """
    Apply `lineup` (as returned by optimizer.optimise()) to the given league.

    Args:
        lineup:       dict with "starters" and "bench" keys.
        league_url:   URL of the formation page.
        league_name:  Human-readable name for logging.
        driver:       Optional existing Selenium session (must be logged in).
        dry_run:      If True, log actions but don't click anything.

    Returns:
        True if saved successfully, False otherwise.
    """
    own_driver = driver is None
    if own_driver:
        driver = _build_driver()

    success = False
    try:
        if own_driver:
            _login(driver)

        logger.info(f"Submitting lineup for [{league_name}] (dry_run={dry_run})")
        driver.get(league_url)
        time.sleep(config.PAGE_LOAD_WAIT + 1)

        if not dry_run:
            _screenshot(driver, f"{league_name}_before")

        # Reset first so we don't stack players
        _reset_formation(driver, dry_run)

        starters = lineup["starters"]

        # Group starters by role to track slot indices
        by_role: dict[str, list] = {}
        for p in starters:
            by_role.setdefault(p["role"], []).append(p)

        placed = 0
        for role, role_players in by_role.items():
            for idx, player in enumerate(role_players):
                ok = _place_player(driver, player, idx, dry_run)
                if ok:
                    placed += 1

        logger.info(f"Placed {placed}/{len(starters)} starters.")

        saved = _click_save(driver, dry_run)

        if not dry_run:
            _screenshot(driver, f"{league_name}_after")

        success = saved and placed == len(starters)
        if success:
            logger.info(f"✓ Lineup submitted successfully for {league_name}.")
        else:
            logger.warning(f"⚠ Lineup submission may be incomplete for {league_name}.")

    except Exception as e:
        logger.exception(f"Unexpected error during submission for {league_name}: {e}")
        if not dry_run:
            try:
                _screenshot(driver, f"{league_name}_error")
            except Exception:
                pass
    finally:
        if own_driver:
            driver.quit()

    return success


def submit_all_leagues(lineup: dict, dry_run: bool = False) -> dict[str, bool]:
    """
    Submit the same lineup to every league in config.LEAGUES.
    Reuses a single Selenium session across leagues.

    Returns dict mapping league name → success bool.
    """
    driver = _build_driver()
    results = {}
    try:
        _login(driver)
        for league in config.LEAGUES:
            name = league["name"]
            url  = league["formation_url"]
            ok = submit_lineup(lineup, url, name, driver=driver, dry_run=dry_run)
            results[name] = ok
    finally:
        driver.quit()
    return results


if __name__ == "__main__":
    import json
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    dry = "--dry-run" in sys.argv

    try:
        with open(config.LINEUP_FILE) as f:
            lineup = json.load(f)
    except FileNotFoundError:
        print(f"ERROR: {config.LINEUP_FILE} not found. Run optimizer.py first.")
        sys.exit(1)

    results = submit_all_leagues(lineup, dry_run=dry)
    for league, ok in results.items():
        status = "✓ OK" if ok else "✗ FAILED"
        print(f"  {status}  {league}")
