import argparse
import logging
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

from player_data import (
    create_driver,
    login_if_needed,
    normalize_league_url,
    save_snapshot,
)
from lineup_editor import apply_lineup, fetch_league_data
from run_report import write_report, player_report
from predictions import get_prediction_source
from schedule_guard import (
    DEFAULT_SCHEDULE_URL,
    DEFAULT_TIMEZONE,
    check_schedule_guard,
)
from squad_picker import PickedSquad, pick_squad

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
        "formation": os.environ.get("FORMATION", "auto").strip(),
        "source": os.environ.get("PREDICTION_SOURCE", "historical").strip(),
        "weights": weights,
        "skip_after_first_game": env_bool("SKIP_AFTER_FIRST_GAME", True),
        "replace_existing_lineup": env_bool("REPLACE_EXISTING_LINEUP", True),
        "dry_run": env_bool("DRY_RUN", False),
    }


def print_recommendation(picked: PickedSquad, scores: dict[str, float]) -> None:
    log.info("Recommended XI (%s):", picked.formation)
    for player in picked.starters:
        log.info("  %s %-22s score=%s", player.role, player.name, scores.get(player.name, 0.0))
    log.info("Captain: %s (score=%s)", picked.captain.name, scores.get(picked.captain.name, 0.0))
    if picked.vice:
        log.info("Vice-captain: %s (score=%s)", picked.vice.name, scores.get(picked.vice.name, 0.0))
    log.info("Bench: %s", ", ".join(p.name for p in picked.bench))


def handle_league(driver, cfg: dict, url: str, args: argparse.Namespace) -> dict:
    league_path = urlparse(url).path.strip('/').replace('/', '_')
    debug_dir = str(Path(args.debug_dir) / league_path) if args.debug_dir else None
    league = fetch_league_data(driver, url, debug_dir=debug_dir)
    result = {"league": league.name, "status": "skipped", "reason": ""}

    if league.lineup_locked:
        log.info("Lineup is locked because the matchday is live - skipping %s", league.name)
        result["reason"] = "Fantacalcio has locked this matchday"
        return result

    # This mode explicitly preserves a populated lineup, including in dry run.
    if league.lineup_empty is False and not cfg["replace_existing_lineup"]:
        result["reason"] = "Existing lineup preserved by configuration"
        return result

    if not league.players:
        raise RuntimeError("No roster data available for a data-driven lineup")

    source = get_prediction_source(cfg["source"], weights=cfg["weights"])
    scores = source.predict(league)
    picked = pick_squad(league.players, scores, cfg["formation"],
                        allowed_formations=league.allowed_formations,
                        bench_roles=league.bench_roles)
    print_recommendation(picked, scores)
    result.update(formation=picked.formation,
                  starters=[player_report(p, scores[p.name]) for p in picked.starters],
                  bench=[player_report(p, scores[p.name]) for p in picked.bench])

    if args.dry_run or cfg["dry_run"]:
        log.info("Dry run - nothing was changed on the site")
        result.update(status="dry_run", reason="Recommendation only; nothing saved")
        return result

    log.info("Setting the data-driven recommended XI")
    try:
        saved = apply_lineup(driver, picked, before_save=schedule_allows_run, debug_dir=debug_dir)
    except Exception:
        save_snapshot(driver, debug_dir, "set_lineup_failed")
        raise
    log.info("League %s updated", league.name)
    result.update(status="saved", reason="Verified after reloading the saved lineup", saved=saved)
    return result


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

    dry_run = args.dry_run or env_bool("DRY_RUN", False)
    if not dry_run and not schedule_allows_run():
        write_report([{"league": "All leagues", "status": "skipped", "reason": "Fixture guard prevents updates; see log for kickoff details"}], dry_run)
        return

    cfg = get_config()
    headless = not args.visible and cfg["headless"]
    log.info(
        "Running %s with %d league(s), headless=%s, formation=%s, source=%s",
        "dry run" if dry_run else "live",
        len(cfg["league_urls"]),
        headless,
        cfg["formation"],
        cfg["source"],
    )

    driver = create_driver(headless=headless)
    failures = []
    results = []
    try:
        for url in cfg["league_urls"]:
            url = normalize_league_url(url)
            for attempt in range(1, RETRIES_PER_LEAGUE + 1):
                try:
                    driver.get(url)
                    login_if_needed(driver, cfg["email"], cfg["password"])
                    results.append(handle_league(driver, cfg, url, args))
                    break
                except Exception as e:
                    log.error(
                        "Attempt %d/%d failed for %s: %s",
                        attempt, RETRIES_PER_LEAGUE, url, e,
                    )
                    if attempt == RETRIES_PER_LEAGUE:
                        failures.append(url)
                        results.append({"league": urlparse(url).path.split('/')[1],
                                        "status": "failed", "reason": str(e)})
                    else:
                        time.sleep(RETRY_SLEEP_SECONDS)
    finally:
        try:
            driver.quit()
        finally:
            write_report(results, dry_run)

    if failures:
        log.error("Failed leagues: %s", ", ".join(failures))
        sys.exit(1)
    log.info("All leagues processed")


if __name__ == "__main__":
    main()
