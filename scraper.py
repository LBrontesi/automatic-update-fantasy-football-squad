"""
scraper.py — logs into fantacalcio.it, scrapes your squad and player ratings.

Returns a list of dicts:
  {
    "name":   str,          # player name as shown on the site
    "role":   str,          # "P" | "D" | "C" | "A"
    "team":   str,          # Serie A club
    "rating": float,        # voto medio (season average)
    "fvm":    float,        # fantacalcio median value
    "status": str,          # "available" | "doubtful" | "injured"
    "home":   bool,         # True if this matchday fixture is at home
  }
"""

import json
import logging
import time

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException

import config

logger = logging.getLogger(__name__)


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


def _login(driver: webdriver.Chrome) -> None:
    """Log in via the fantacalcio.it auth page."""
    logger.info("Navigating to login page...")
    driver.get(config.LOGIN_URL)
    time.sleep(config.PAGE_LOAD_WAIT)

    # Click the login / profile nav button
    try:
        login_btn = driver.find_element(
            By.XPATH, "/html/body/nav[2]/div/a[2]"
        )
        driver.execute_script("arguments[0].scrollIntoView();", login_btn)
        driver.execute_script("arguments[0].click();", login_btn)
        time.sleep(config.PAGE_LOAD_WAIT)
    except NoSuchElementException:
        logger.warning("Login nav button not found — may already be logged in.")
        return

    # Fill credentials
    wait = WebDriverWait(driver, 15)
    email_input = wait.until(
        EC.presence_of_element_located((
            By.XPATH,
            "//input[@type='email' or @placeholder[contains(., 'email') or contains(., 'Email')]]",
        ))
    )
    email_input.clear()
    email_input.send_keys(config.EMAIL)

    pwd_input = driver.find_element(
        By.XPATH, "//input[@type='password']"
    )
    pwd_input.clear()
    pwd_input.send_keys(config.PASSWORD)
    pwd_input.send_keys(Keys.RETURN)

    time.sleep(config.PAGE_LOAD_WAIT + 1)
    logger.info("Login submitted.")


def _get_player_status(badge_classes: str) -> str:
    """Map icon/badge class names to a status string."""
    badge_classes = badge_classes.lower()
    if "infortunato" in badge_classes or "injured" in badge_classes:
        return "injured"
    if "dubbio" in badge_classes or "doubtful" in badge_classes:
        return "doubtful"
    return "available"


def _scrape_squad_from_page(driver: webdriver.Chrome, league_url: str) -> list[dict]:
    """
    Navigate to a league formation page and scrape player cards.

    The page renders an Angular app with player cards that contain:
      - player name
      - role badge  (P/D/C/A)
      - team name
      - rating/fvm
      - status indicator (injury badge)

    NOTE: Selector paths may need adjustment if fantacalcio updates its markup.
    The fallback strategy tries multiple selector variants to stay robust.
    """
    logger.info(f"Opening formation page: {league_url}")
    driver.get(league_url)
    time.sleep(config.PAGE_LOAD_WAIT + 1)

    players = []

    # ── Strategy 1: player card elements (Angular component list) ─────────────
    card_selectors = [
        ".player-card",
        "[class*='player-item']",
        "[class*='giocatore']",
        "app-player-card",
    ]

    cards = []
    for sel in card_selectors:
        cards = driver.find_elements(By.CSS_SELECTOR, sel)
        if cards:
            logger.info(f"Found {len(cards)} player cards using selector '{sel}'")
            break

    for card in cards:
        try:
            player = _parse_player_card(card)
            if player:
                players.append(player)
        except Exception as e:
            logger.debug(f"Error parsing card: {e}")
            continue

    if not players:
        logger.warning(
            "No players found via card scraping — falling back to table scraping."
        )
        players = _scrape_squad_from_table(driver)

    logger.info(f"Scraped {len(players)} players from {league_url}")
    return players


def _parse_player_card(card) -> dict | None:
    """Parse a single player card element into a dict."""
    try:
        name = card.find_element(
            By.CSS_SELECTOR,
            "[class*='name'], [class*='nome'], .player-name",
        ).text.strip()
    except NoSuchElementException:
        return None

    if not name:
        return None

    # Role
    try:
        role_el = card.find_element(By.CSS_SELECTOR, "[class*='role'], [class*='ruolo']")
        role = role_el.text.strip().upper()[:1]  # keep first char: P/D/C/A
    except NoSuchElementException:
        role = "?"

    # Team
    try:
        team = card.find_element(
            By.CSS_SELECTOR, "[class*='team'], [class*='squadra']"
        ).text.strip()
    except NoSuchElementException:
        team = ""

    # Rating (voto medio) — look for a numeric element
    rating = _extract_float(card, ["[class*='rating']", "[class*='voto']", "[class*='avg']"])

    # FVM
    fvm = _extract_float(card, ["[class*='fvm']", "[class*='value']", "[class*='quota']"])

    # Status
    try:
        badge = card.find_element(
            By.CSS_SELECTOR, "[class*='status'], [class*='stato'], [class*='badge']"
        )
        status = _get_player_status(badge.get_attribute("class") or "")
    except NoSuchElementException:
        status = "available"

    return {
        "name":   name,
        "role":   role if role in ("P", "D", "C", "A") else "?",
        "team":   team,
        "rating": rating,
        "fvm":    fvm,
        "status": status,
        "home":   False,   # filled in by _enrich_with_fixtures()
    }


def _extract_float(element, selectors: list[str]) -> float:
    for sel in selectors:
        try:
            text = element.find_element(By.CSS_SELECTOR, sel).text.strip()
            return float(text.replace(",", "."))
        except (NoSuchElementException, ValueError):
            continue
    return 0.0


def _scrape_squad_from_table(driver: webdriver.Chrome) -> list[dict]:
    """
    Fallback: try reading a roster table (some league views use <table>).
    Adjust the selectors to match whatever table fantacalcio renders.
    """
    players = []
    try:
        rows = driver.find_elements(By.CSS_SELECTOR, "table tbody tr")
        for row in rows:
            cells = row.find_elements(By.TAG_NAME, "td")
            if len(cells) < 3:
                continue
            players.append({
                "name":   cells[1].text.strip() if len(cells) > 1 else cells[0].text.strip(),
                "role":   cells[0].text.strip().upper()[:1],
                "team":   cells[2].text.strip() if len(cells) > 2 else "",
                "rating": _safe_float(cells[3].text) if len(cells) > 3 else 0.0,
                "fvm":    _safe_float(cells[4].text) if len(cells) > 4 else 0.0,
                "status": "available",
                "home":   False,
            })
    except Exception as e:
        logger.error(f"Table fallback also failed: {e}")
    return players


def _safe_float(text: str) -> float:
    try:
        return float(text.replace(",", ".").strip())
    except ValueError:
        return 0.0


def _enrich_with_fixtures(players: list[dict]) -> list[dict]:
    """
    Try to determine home/away for each player this matchday.
    Fantacalcio shows the matchday calendar on the formation page sidebar;
    scraping it is league-specific, so this function is a stub you can extend.

    For now it sets home=False for everyone (safe default — won't break scoring,
    just won't apply the home bonus).  Extend this by scraping the fixture list.
    """
    logger.info(
        "Fixture enrichment not yet implemented — home bonus will not be applied. "
        "See _enrich_with_fixtures() in scraper.py to add this."
    )
    return players


# ── Public API ────────────────────────────────────────────────────────────────

def scrape(league_url: str, driver: webdriver.Chrome | None = None) -> list[dict]:
    """
    Full scrape pipeline for one league.
    If a driver is passed in (e.g. already logged in), reuse it.
    Otherwise creates a new session.
    """
    own_driver = driver is None
    if own_driver:
        driver = _build_driver()

    try:
        if own_driver:
            _login(driver)

        players = _scrape_squad_from_page(driver, league_url)
        players = _enrich_with_fixtures(players)
        return players

    finally:
        if own_driver:
            driver.quit()


def scrape_all_leagues(save: bool = True) -> dict[str, list[dict]]:
    """Scrape all leagues defined in config, reusing the same session."""
    driver = _build_driver()
    results = {}
    try:
        _login(driver)
        for league in config.LEAGUES:
            name = league["name"]
            logger.info(f"Scraping league: {name}")
            players = scrape(league["formation_url"], driver=driver)
            results[name] = players
            if save:
                path = f"{name}_{config.PLAYERS_FILE}"
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(players, f, ensure_ascii=False, indent=2)
                logger.info(f"Saved {len(players)} players → {path}")
    finally:
        driver.quit()
    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    data = scrape_all_leagues(save=True)
    for league, players in data.items():
        print(f"\n=== {league} ===")
        for p in players[:5]:
            print(f"  {p['role']} {p['name']} ({p['team']}) rating={p['rating']} fvm={p['fvm']} status={p['status']}")
        if len(players) > 5:
            print(f"  ... and {len(players) - 5} more")
