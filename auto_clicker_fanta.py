import argparse
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

from player_data import (
    create_driver,
    login_if_needed,
    normalize_league_url,
    save_snapshot,
)
from lineup_editor import (
    apply_lineup,
    fetch_league_data,
    read_competition_rounds,
    read_round_outcome,
    restore_lineup_page,
)
from learning import (
    adjust_scores,
    apply_round_outcome,
    apply_roster_outcome,
    bind_league,
    empty_state,
    load_state,
    pending_rounds,
    record_pending,
    role_adjustment,
    save_state,
)
from feedback_data import capture_settings, fetch_history, read_calculated_match, collect_roster_results
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


def handle_league(
    driver,
    cfg: dict,
    url: str,
    args: argparse.Namespace,
    learning_state: dict | None = None,
    learning_key: str = "league_1",
) -> dict:
    if learning_state is None:
        learning_state = empty_state()
    league_path = urlparse(url).path.strip('/').replace('/', '_')
    learning_key = bind_league(learning_state, learning_key, league_path)
    debug_dir = str(Path(args.debug_dir) / league_path) if args.debug_dir else None
    current_round, latest_completed = None, None
    try:
        current_round, latest_completed = read_competition_rounds(
            driver, url, debug_dir=debug_dir
        )
        log.info(
            "Learning feedback rounds for %s: current=%s, latest calculated=%s",
            league_path,
            current_round,
            latest_completed,
        )
    except Exception as exc:
        log.warning("Could not read matchday numbers for %s: %s", league_path, exc)
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

    context = None
    feedback_notes = []
    try:
        context = capture_settings(driver)
        context['season'], _ = fetch_history(league.players[0].external_id, context['source'])
    except Exception as exc:
        context = None
        feedback_notes.append(f'Whole-roster feedback context unavailable: {exc}')
        log.warning('%s', feedback_notes[-1])

    checking_feedback = getattr(args, 'check_feedback_source', False)
    if checking_feedback:
        if not context or latest_completed is None:
            raise RuntimeError('Cannot check feedback without scoring context and a completed round')
        match = read_calculated_match(driver, url, latest_completed, None,
            [p.external_id for p in league.players])
        snapshot = {'context': context, 'roster': {str(p.external_id): {'role': p.role} for p in league.players}}
        checked = collect_roster_results(snapshot, match)
        result['feedback_source_check'] = {
            'round': latest_completed, 'real_matchweek': match['real_matchweek'],
            'season': context['season'], 'players': len(checked),
            'official_scores_checked': len(set(map(int, checked)) & set(match['players'])),
        }
        log.info('Verified whole-roster feedback source for %d players, competition round %d / Serie A %d',
                 len(checked), latest_completed, match['real_matchweek'])

    if latest_completed is not None and not checking_feedback:
        owned_ids = {player.external_id for player in league.players if player.external_id is not None}
        for round_number in pending_rounds(learning_state, learning_key, latest_completed):
            try:
                pending = learning_state['pending'][learning_key][str(round_number)]
                if 'roster' in pending:
                    if not context or context['season_id'] != pending['context']['season_id']:
                        raise RuntimeError('Saved and current league seasons do not match')
                    selected_ids = [int(pid) for pid, p in pending['roster'].items() if p['group'] != 'excluded']
                    match = read_calculated_match(driver, url, round_number, selected_ids)
                    actual = collect_roster_results(pending, match)
                    applied, reason = apply_roster_outcome(learning_state, learning_key, round_number, actual, match)
                else:
                    actual_by_role, submitted_at = read_round_outcome(driver, url, round_number, owned_ids)
                    applied, reason = apply_round_outcome(learning_state, learning_key, round_number, actual_by_role, submitted_at)
                feedback_notes.append(f'Round {round_number}: {reason}')
                if applied:
                    log.info("Recorded decision feedback for %s round %d", league_path, round_number)
                else:
                    log.info(
                        "Skipped role-level feedback for %s round %d: %s",
                        league_path,
                        round_number,
                        reason,
                    )
            except Exception as exc:
                # Learning is best-effort; an unavailable result page must not
                # prevent a valid pre-match lineup update.
                log.warning(
                    "Could not collect feedback for %s round %d: %s",
                    league_path,
                    round_number,
                    exc,
                )
                feedback_notes.append(f'Round {round_number}: feedback deferred ({exc})')

    source = get_prediction_source(cfg["source"], weights=cfg["weights"])
    base_scores = source.predict(league)
    scores = adjust_scores(
        learning_state, learning_key, league.players, base_scores,
        season=context['season'] if context else None,
    )
    trained_roles = [
        role for role in ("G", "D", "C", "A")
        if role_adjustment(learning_state, learning_key, role)
    ]
    if trained_roles:
        log.info("Persistent feedback is influencing roles: %s", ", ".join(trained_roles))
    result['learning_adjustments'] = [
        {'name': p.name, 'base': base_scores[p.name], 'adjusted': scores[p.name],
         'change': round(scores[p.name]-base_scores[p.name], 3)}
        for p in league.players if abs(scores[p.name]-base_scores[p.name]) > .0001
    ]
    if result['learning_adjustments']:
        log.info('Persistent feedback changed %d player rankings', len(result['learning_adjustments']))
    picked = pick_squad(league.players, scores, cfg["formation"],
                        allowed_formations=league.allowed_formations,
                        bench_roles=league.bench_roles)
    print_recommendation(picked, scores)
    result.update(formation=picked.formation,
                  starters=[player_report(p, scores[p.name]) for p in picked.starters],
                  bench=[player_report(p, scores[p.name]) for p in picked.bench])
    result['feedback_notes'] = feedback_notes
    reviews = learning_state['reviews'].get(learning_key, {})
    if reviews:
        result['feedback_review'] = reviews[max(reviews, key=int)]

    if args.dry_run or cfg["dry_run"] or checking_feedback:
        log.info("Dry run - nothing was changed on the site")
        result.update(status="dry_run", reason="Recommendation only; nothing saved")
        return result

    log.info("Setting the data-driven recommended XI")
    try:
        restore_lineup_page(driver, url)
        saved = apply_lineup(driver, picked, before_save=schedule_allows_run, debug_dir=debug_dir)
    except Exception:
        save_snapshot(driver, debug_dir, "set_lineup_failed")
        raise
    log.info("League %s updated", league.name)
    if current_round is not None and context:
        record_pending(
            learning_state,
            learning_key,
            current_round,
            picked,
            scores,
            saved_at=datetime.now(timezone.utc),
            roster=league.players, allowed_formations=league.allowed_formations,
            bench_roles=league.bench_roles, context=context, base_scores=base_scores,
        )
        result['feedback_pending'] = current_round
        log.info("Stored whole-roster decision snapshot for %s round %d (%d players)", league_path, current_round, len(league.players))
    else:
        log.warning("Lineup saved, but feedback context/round was unavailable; learning data was not queued")
    result.update(status="saved", reason="Verified after reloading the saved lineup", saved=saved)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Auto-select and confirm your Fantacalcio squads."
    )
    parser.add_argument(
        '--check-feedback-source', action='store_true',
        default=env_bool('CHECK_FEEDBACK_SOURCE', False),
        help='Read and validate the last completed round for the whole roster; do not save or train.',
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

    dry_run = args.dry_run or args.check_feedback_source or env_bool("DRY_RUN", False)
    if not dry_run and not schedule_allows_run():
        write_report([{"league": "All leagues", "status": "skipped", "reason": "Fixture guard prevents updates; see log for kickoff details"}], dry_run)
        return

    cfg = get_config()
    cfg['dry_run'] = dry_run
    learning_path = Path(os.environ.get("LEARNING_STATE_PATH", "data/learning_state.json"))
    learning_state = load_state(learning_path)
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
        for league_index, url in enumerate(cfg["league_urls"], start=1):
            url = normalize_league_url(url)
            for attempt in range(1, RETRIES_PER_LEAGUE + 1):
                try:
                    driver.get(url)
                    login_if_needed(driver, cfg["email"], cfg["password"])
                    results.append(
                        handle_league(
                            driver,
                            cfg,
                            url,
                            args,
                            learning_state,
                            f"league_{league_index}",
                        )
                    )
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
            try:
                if not args.check_feedback_source:
                    save_state(learning_state, learning_path)
            except Exception:
                log.exception("Could not save persistent learning state")
                failures.append("learning-state")
            write_report(results, dry_run)

    if failures:
        log.error("Failed leagues: %s", ", ".join(failures))
        sys.exit(1)
    log.info("All leagues processed")


if __name__ == "__main__":
    main()
