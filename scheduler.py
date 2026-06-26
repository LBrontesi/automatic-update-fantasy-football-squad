"""
scheduler.py — orchestrates the full pipeline and runs it on a schedule.

Usage
─────
  # Run once immediately (with dry-run to test without real clicks):
  python scheduler.py --dry-run

  # Run once for real:
  python scheduler.py

  # Run once for a specific league only:
  python scheduler.py --league rusticatorsapienza

  # Start the daemon (runs every N days, default from config):
  python scheduler.py --daemon

  # Only scrape (no optimise/submit):
  python scheduler.py --scrape-only

  # Only optimise from an already-scraped file:
  python scheduler.py --optimise-only --league rusticatorsapienza

Cron alternative (recommended for production)
─────────────────────────────────────────────
  # Add to crontab with:  crontab -e
  # Run every 2 days at 09:00:
  0 9 */2 * * cd /path/to/fantacalcio_bot && python scheduler.py >> bot.log 2>&1
"""

import argparse
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

import config
import scraper
import optimizer
import submitter

# ── Logging setup ─────────────────────────────────────────────────────────────

def setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    fmt   = "%(asctime)s  %(levelname)-8s  %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"

    handlers = [logging.StreamHandler(sys.stdout)]
    if config.LOG_FILE:
        handlers.append(logging.FileHandler(config.LOG_FILE, encoding="utf-8"))

    logging.basicConfig(level=level, format=fmt, datefmt=datefmt, handlers=handlers)


# ── Pipeline steps ─────────────────────────────────────────────────────────────

def run_scrape(league_name: str | None = None) -> dict[str, list[dict]]:
    """Scrape one or all leagues. Returns {league_name: [players]}."""
    logger = logging.getLogger(__name__)
    logger.info("── SCRAPE ──────────────────────────────────────────")

    if league_name:
        leagues = [l for l in config.LEAGUES if l["name"] == league_name]
        if not leagues:
            logger.error(f"League '{league_name}' not found in config.LEAGUES")
            sys.exit(1)
    else:
        leagues = config.LEAGUES

    from selenium.webdriver.chrome.options import Options
    from selenium import webdriver as wd

    opts = Options()
    if config.HEADLESS:
        opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1400,900")
    driver = wd.Chrome(options=opts)
    driver.implicitly_wait(8)

    results: dict[str, list[dict]] = {}
    try:
        scraper._login(driver)
        for league in leagues:
            name = league["name"]
            logger.info(f"Scraping {name}...")
            players = scraper.scrape(league["formation_url"], driver=driver)
            results[name] = players

            path = Path(f"{name}_{config.PLAYERS_FILE}")
            path.write_text(
                json.dumps(players, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            logger.info(f"Saved {len(players)} players → {path}")
    finally:
        driver.quit()

    return results


def run_optimise(
    players_data: dict[str, list[dict]] | None = None,
    league_name: str | None = None,
) -> dict[str, dict]:
    """
    Optimise lineups for each league.
    If players_data is None, loads from saved JSON files.
    Returns {league_name: lineup}.
    """
    logger = logging.getLogger(__name__)
    logger.info("── OPTIMISE ────────────────────────────────────────")

    leagues = (
        [l for l in config.LEAGUES if l["name"] == league_name]
        if league_name
        else config.LEAGUES
    )

    lineups: dict[str, dict] = {}
    for league in leagues:
        name = league["name"]

        if players_data and name in players_data:
            players = players_data[name]
        else:
            path = Path(f"{name}_{config.PLAYERS_FILE}")
            if not path.exists():
                logger.error(
                    f"Players file not found: {path}. Run --scrape-only first."
                )
                continue
            players = json.loads(path.read_text(encoding="utf-8"))

        logger.info(f"Optimising lineup for {name} ({len(players)} players)...")
        lineup = optimizer.optimise(players)

        out_path = f"{name}_{config.LINEUP_FILE}"
        clean = {
            "starters":  [{k: v for k, v in p.items() if k != "_score"} for p in lineup["starters"]],
            "bench":     [{k: v for k, v in p.items() if k != "_score"} for p in lineup["bench"]],
            "formation": lineup["formation"],
            "scores":    lineup["scores"],
        }
        Path(out_path).write_text(
            json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        logger.info(f"Lineup saved → {out_path}")
        lineups[name] = lineup

    return lineups


def run_submit(
    lineups: dict[str, dict] | None = None,
    league_name: str | None = None,
    dry_run: bool = False,
) -> dict[str, bool]:
    """
    Submit lineups for each league.
    If lineups is None, loads from saved JSON files.
    Returns {league_name: success}.
    """
    logger = logging.getLogger(__name__)
    logger.info("── SUBMIT ──────────────────────────────────────────")

    leagues = (
        [l for l in config.LEAGUES if l["name"] == league_name]
        if league_name
        else config.LEAGUES
    )

    from selenium.webdriver.chrome.options import Options
    from selenium import webdriver as wd

    opts = Options()
    if config.HEADLESS:
        opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1400,900")
    driver = wd.Chrome(options=opts)
    driver.implicitly_wait(8)

    results: dict[str, bool] = {}
    try:
        submitter._login(driver)
        for league in leagues:
            name = league["name"]
            url  = league["formation_url"]

            if lineups and name in lineups:
                lineup = lineups[name]
            else:
                path = Path(f"{name}_{config.LINEUP_FILE}")
                if not path.exists():
                    logger.error(f"Lineup file not found: {path}. Run optimise first.")
                    results[name] = False
                    continue
                lineup = json.loads(path.read_text(encoding="utf-8"))

            ok = submitter.submit_lineup(lineup, url, name, driver=driver, dry_run=dry_run)
            results[name] = ok
    finally:
        driver.quit()

    return results


def run_pipeline(
    league_name: str | None = None,
    dry_run: bool = False,
    scrape_only: bool = False,
    optimise_only: bool = False,
) -> None:
    logger = logging.getLogger(__name__)
    start = datetime.now()
    logger.info(f"{'='*52}")
    logger.info(f"  Fantacalcio bot starting  {start:%Y-%m-%d %H:%M}")
    logger.info(f"  dry_run={dry_run}  league={league_name or 'all'}")
    logger.info(f"{'='*52}")

    players_data = None
    lineups      = None

    if not optimise_only:
        players_data = run_scrape(league_name)

    if not scrape_only:
        lineups = run_optimise(players_data, league_name)

        if not optimise_only:
            results = run_submit(lineups, league_name, dry_run)
            logger.info("── RESULTS ─────────────────────────────────────────")
            for name, ok in results.items():
                status = "✓ OK" if ok else "✗ FAILED"
                logger.info(f"  {status}  {name}")

    elapsed = (datetime.now() - start).total_seconds()
    logger.info(f"Done in {elapsed:.1f}s")


# ── Daemon mode ────────────────────────────────────────────────────────────────

INTERVAL_DAYS = 2   # Change here or make it a config setting

def run_daemon(args) -> None:
    logger = logging.getLogger(__name__)
    interval_sec = INTERVAL_DAYS * 24 * 3600
    logger.info(f"Daemon mode: will run every {INTERVAL_DAYS} days.")

    while True:
        try:
            run_pipeline(
                league_name=args.league,
                dry_run=args.dry_run,
            )
        except Exception as e:
            logger.exception(f"Pipeline error: {e}")

        next_run = datetime.fromtimestamp(time.time() + interval_sec)
        logger.info(f"Next run scheduled at {next_run:%Y-%m-%d %H:%M}")
        time.sleep(interval_sec)


# ── CLI ───────────────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(description="Fantacalcio auto-lineup bot")
    p.add_argument("--dry-run",        action="store_true", help="Log actions but skip real clicks")
    p.add_argument("--daemon",         action="store_true", help="Run on a schedule forever")
    p.add_argument("--scrape-only",    action="store_true", help="Only scrape, skip optimise/submit")
    p.add_argument("--optimise-only",  action="store_true", help="Only optimise from saved data, skip submit")
    p.add_argument("--league",         type=str, default=None, help="Target a single league by name")
    p.add_argument("--verbose", "-v",  action="store_true", help="Debug-level logging")
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    setup_logging(args.verbose)

    if args.daemon:
        run_daemon(args)
    else:
        run_pipeline(
            league_name=args.league,
            dry_run=args.dry_run,
            scrape_only=args.scrape_only,
            optimise_only=args.optimise_only,
        )
