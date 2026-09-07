import argparse
import logging
import os
import sys
import time

from dotenv import load_dotenv

from player_data import (
    confirm_formation,
    create_driver,
    fetch_league_data,
    login_if_needed,
    normalize_league_url,
    set_lineup,
)
from predictions import get_prediction_source
from schedule_guard import (
    DEFAULT_SCHEDULE_URL,
    DEFAULT_TIMEZONE,
    check_schedule_guard,
)
from squad_picker import DEFAULT_FORMATION, PickedSquad, pick_squad

load_dotenv()

DEFAULT_LEAGUE_URLS = [
    "https://leghe.fantacalcio.it/rusticatorsapienza/area-gioco/inserisci-formazione?id=362238",
    "https://leghe.fantacalcio.it/ilprimoverofanta/area-gioco/inserisci-formazione?id=416045",
]

RETRIES_PER_LEAGUE = 2
RETRY_SLEEP_SECONDS = 5

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("fantasquad")


def env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off")


def schedule_allows_run() -> bool:
    if not env_bool("SKIP_AFTER_FIRST_GAME", True):
        log.info("Schedule guard disabled by SKIP_AFTER_FIRST_GAME")
        return True

    decision = check_schedule_guard(
        url=os.environ.get("SCHEDULE_URL", DEFAULT_SCHEDULE_URL).strip(),
        timezone_name=os.environ.get("SCHEDULE_TIMEZONE", DEFAULT_TIMEZONE).strip(),
    )
    if not decision.allowed:
        log.info("Skipping squad update: %s", decision.reason)
        return False
    log.info("Schedule guard passed: %s", decision.reason)
    return True


def get_config() -> dict:
    email = os.environ.get("FANTACALCIO_EMAIL", "").strip()
    password = os.environ.get("FANTACALCIO_PASSWORD", "").strip()
    if not email or not password:
        raise SystemExit(
            "FANTACALCIO_EMAIL and FANTACALCIO_PASSWORD must be set (in .env or as environment variables)."
        )
    league_urls = os.environ.get("LEAGUE_URL", "").strip()
    if league_urls:
        league_urls = [u.strip() for u in league_urls.split(",") if u.strip()]
    else:
        league_urls = DEFAULT_LEAGUE_URLS
    headless = env_bool("HEADLESS", True)
    weights = {}
    for key in ("avg", "trend", "home", "opponent"):
        raw = os.environ.get(f"WEIGHTS_{key.upper()}", "").strip()
        if raw:
            weights[key] = float(raw)
    return {
        "email": email,
        "password": password,
        "league_urls": league_urls,
        "headless": headless,
        "formation": os.environ.get("FORMATION", DEFAULT_FORMATION).strip(),
        "source": os.environ.get("PREDICTION_SOURCE", "historical").strip(),
        "weights": weights,
        "skip_after_first_game": env_bool("SKIP_AFTER_FIRST_GAME", True),
    }


def print_recommendation(picked: PickedSquad, scores: dict[str, float]) -> None:
    log.info("Recommended XI (%s):", picked.formation)
    for player in picked.starters:
        log.info("  %s %-22s score=%s", player.role, player.name, scores.get(player.name, 0.0))
    log.info("Captain: %s (score=%s)", picked.captain.name, scores.get(picked.captain.name, 0.0))
    if picked.vice:
        log.info("Vice-captain: %s (score=%s)", picked.vice.name, scores.get(picked.vice.name, 0.0))
    log.info("Bench: %s", ", ".join(p.name for p in picked.bench))


def handle_league(driver, cfg: dict, url: str, args: argparse.Namespace) -> None:
    league = fetch_league_data(driver, url, debug_dir=args.debug_dir)
    source = get_prediction_source(cfg["source"], weights=cfg["weights"])
    scores = source.predict(league)
    picked = pick_squad(league.players, scores, cfg["formation"])
    print_recommendation(picked, scores)

    if args.dry_run:
        log.info("Dry run - nothing was changed on the site")
        return

    if league.lineup_empty is False:
        log.info("Lineup already set for this matchday - confirming only")
        confirm_formation(driver)
        return

    log.info("Lineup empty - setting the recommended XI")
    set_lineup(driver, picked)
    confirm_formation(driver)
    log.info("League %s updated", league.name)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Auto-select and confirm your Fantacalcio squads."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log in and read the league pages, compute and print the recommended XI, "
        "but change nothing on the site.",
    )
    parser.add_argument(
        "--visible",
        action="store_true",
        help="Show the browser window even if HEADLESS=true.",
    )
    parser.add_argument(
        "--debug-dir",
        default="debug",
        help="Directory for HTML snapshots on extraction failures (default: debug).",
    )
    args = parser.parse_args()

    if not schedule_allows_run():
        return

    cfg = get_config()
    headless = not args.visible and cfg["headless"]
    log.info(
        "Running %s with %d league(s), headless=%s, formation=%s, source=%s",
        "dry run" if args.dry_run else "live",
        len(cfg["league_urls"]),
        headless,
        cfg["formation"],
        cfg["source"],
    )

    driver = create_driver(headless=headless)
    failures = []
    try:
        for url in cfg["league_urls"]:
            url = normalize_league_url(url)
            for attempt in range(1, RETRIES_PER_LEAGUE + 1):
                try:
                    driver.get(url)
                    login_if_needed(driver, cfg["email"], cfg["password"])
                    handle_league(driver, cfg, url, args)
                    break
                except Exception as e:
                    log.error(
                        "Attempt %d/%d failed for %s: %s",
                        attempt, RETRIES_PER_LEAGUE, url, e,
                    )
                    if attempt == RETRIES_PER_LEAGUE:
                        failures.append(url)
                    else:
                        time.sleep(RETRY_SLEEP_SECONDS)
    finally:
        driver.quit()

    if failures:
        log.error("Failed leagues: %s", ", ".join(failures))
        sys.exit(1)
    log.info("All leagues processed")


if __name__ == "__main__":
    main()
