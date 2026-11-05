"""Persistent player feedback and hindsight reviews of verified submissions."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import math
from pathlib import Path

ROLES = ("G", "D", "C", "A")
STATE_VERSION = 2
DEFAULT_STATE_PATH = Path("data/learning_state.json")
MIN_ROUNDS_TO_APPLY = 3
SHRINKAGE_ROUNDS = 6
MAX_ABS_ADJUSTMENT = 1.5


def empty_state() -> dict:
    return {"version": STATE_VERSION, "models": {}, "pending": {},
            "player_models": {}, "reviews": {}, "league_aliases": {}}


def load_state(path: str | Path = DEFAULT_STATE_PATH) -> dict:
    path = Path(path)
    if not path.exists():
        return empty_state()
    state = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(state, dict)
        or state.get("version") not in (1, STATE_VERSION)
        or not isinstance(state.get("models"), dict)
        or not isinstance(state.get("pending"), dict)
    ):
        raise ValueError(f"Unsupported or malformed learning state at {path}")
    state['version'] = STATE_VERSION
    for key in ('player_models', 'reviews', 'league_aliases'):
        state.setdefault(key, {})
        if not isinstance(state[key], dict):
            raise ValueError(f'Malformed {key} in learning state')
    return state


def bind_league(state, legacy_key, stable_key):
    """Migrate the old positional key once; later secret reordering is harmless."""
    previous = state['league_aliases'].get(legacy_key)
    if previous is None:
        for section in ('models', 'pending', 'player_models', 'reviews'):
            if legacy_key in state[section] and stable_key not in state[section]:
                state[section][stable_key] = state[section].pop(legacy_key)
        state['league_aliases'][legacy_key] = stable_key
    return stable_key


def save_state(state: dict, path: str | Path = DEFAULT_STATE_PATH) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(state, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def pending_rounds(state: dict, league_key: str, latest_completed: int) -> list[int]:
    league_pending = state["pending"].get(league_key, {})
    rounds = []
    for raw_round in league_pending:
        try:
            round_number = int(raw_round)
        except (TypeError, ValueError):
            continue
        if round_number <= latest_completed:
            rounds.append(round_number)
    return sorted(rounds)


def record_pending(
    state: dict,
    league_key: str,
    round_number: int,
    picked,
    scores: dict[str, float],
    saved_at: datetime | None = None,
    *, roster=None, allowed_formations=None, bench_roles=None, context=None, base_scores=None,
) -> None:
    """Snapshot the complete owned roster only after a verified submission."""
    prediction = {
        role: {"count": 0, "score_sum": 0.0}
        for role in ROLES
    }
    for player in picked.starters:
        if player.role not in prediction:
            raise ValueError(f"Unsupported player role {player.role!r}")
        score = float(scores[player.name])
        if not math.isfinite(score):
            raise ValueError(f"Non-finite selected-player score for role {player.role}")
        prediction[player.role]["count"] += 1
        prediction[player.role]["score_sum"] += score
    for values in prediction.values():
        values["score_sum"] = round(values["score_sum"], 4)

    if saved_at is None:
        saved_at = datetime.now(timezone.utc)
    if saved_at.tzinfo is None:
        raise ValueError("saved_at must include a timezone")
    pending_record = {
        "prediction_by_role": prediction,
        "saved_at": saved_at.astimezone(timezone.utc).isoformat(),
    }
    if roster is not None:
        if not context or not all(key in context for key in ('season', 'season_id', 'source', 'bonuses')):
            raise ValueError('Whole-roster feedback requires season and league scoring context')
        selected = {p.external_id for p in picked.starters}
        bench = {p.external_id for p in picked.bench}
        records = {}
        for player in roster:
            if player.external_id is None or str(player.external_id) in records:
                raise ValueError('Whole-roster snapshot requires unique player IDs')
            expected = expected_contribution(player)
            base = float((base_scores or scores)[player.name])
            score = float(scores[player.name])
            if not all(math.isfinite(value) for value in (base, score, expected)):
                raise ValueError('Non-finite player snapshot')
            records[str(player.external_id)] = {
                'name': player.name, 'role': player.role,
                'group': 'starter' if player.external_id in selected else 'bench' if player.external_id in bench else 'excluded',
                'score': score, 'expected_contribution': expected,
                'scale': min(1.0, max(0.0, base / expected)) if expected > 0 else 0.0,
            }
        if not selected.isdisjoint(bench) or selected | bench != {int(pid) for pid, p in records.items() if p['group'] != 'excluded'}:
            raise ValueError('Submitted squad does not match the roster snapshot')
        pending_record.update(
            roster=records, formation=picked.formation,
            starter_ids=[p.external_id for p in picked.starters],
            bench_ids=[p.external_id for p in picked.bench],
            allowed_formations=list(allowed_formations or [picked.formation]),
            bench_roles=list(bench_roles or []), context=json.loads(json.dumps(context, allow_nan=False)),
        )
    league_pending = state['pending'].setdefault(league_key, {})
    league_pending[str(int(round_number))] = pending_record
    # Retain a few recent rounds in case a league calculates results late.
    for old_round in sorted(league_pending, key=lambda item: int(item))[:-6]:
        del league_pending[old_round]


def apply_round_outcome(
    state: dict,
    league_key: str,
    round_number: int,
    actual_by_role: dict[str, list[float]],
    submitted_at: datetime,
    timestamp_tolerance_seconds: int = 600,
) -> tuple[bool, str]:
    """Update role residuals once, only if the result matches the saved XI."""
    league_pending = state["pending"].get(league_key, {})
    key = str(int(round_number))
    pending = league_pending.get(key)
    if pending is None:
        return False, "no pending prediction for this round"

    saved_at = datetime.fromisoformat(pending["saved_at"])
    if saved_at.tzinfo is None or submitted_at.tzinfo is None:
        return False, "lineup timestamp has no timezone"
    difference = abs((saved_at - submitted_at).total_seconds())
    if difference > timestamp_tolerance_seconds:
        del league_pending[key]
        if not league_pending:
            state["pending"].pop(league_key, None)
        return False, "the submitted lineup timestamp does not match the bot save"

    prediction = pending["prediction_by_role"]
    samples = {}
    for role in ROLES:
        values = actual_by_role.get(role, [])
        expected = prediction.get(role, {})
        expected_count = int(expected.get("count", 0))
        if len(values) != expected_count:
            return False, f"{role} player count changed; feedback skipped"
        if expected_count:
            actual_total = sum(float(value) for value in values)
            expected_total = float(expected.get("score_sum", 0.0))
            residual = (actual_total - expected_total) / expected_count
            if not math.isfinite(residual):
                return False, f"{role} result was not finite"
            samples[role] = residual

    model = state["models"].setdefault(league_key, {})
    for role, residual in samples.items():
        record = model.setdefault(role, {"rounds": 0, "mean_residual": 0.0})
        rounds = int(record.get("rounds", 0))
        old_mean = float(record.get("mean_residual", 0.0))
        alpha = 1.0 / (rounds + 1) if rounds < 5 else 0.2
        record["mean_residual"] = round(old_mean + alpha * (residual - old_mean), 6)
        record["rounds"] = rounds + 1

    del league_pending[key]
    if not league_pending:
        state["pending"].pop(league_key, None)
    return True, "feedback recorded"


def role_adjustment(state: dict, league_key: str, role: str) -> float:
    record = state["models"].get(league_key, {}).get(role, {})
    rounds = int(record.get("rounds", 0))
    if rounds < MIN_ROUNDS_TO_APPLY:
        return 0.0
    mean_residual = float(record.get("mean_residual", 0.0))
    if not math.isfinite(mean_residual):
        return 0.0
    shrinkage = min(1.0, rounds / SHRINKAGE_ROUNDS)
    return max(-MAX_ABS_ADJUSTMENT, min(MAX_ABS_ADJUSTMENT, mean_residual)) * shrinkage


def expected_contribution(player):
    if player.unavailable or player.start_probability == 0:
        return 0.0
    rating = sum(player.votes[-5:]) / len(player.votes[-5:]) if player.votes else 6.0
    chance = player.start_probability if player.start_probability is not None else .5
    return max(0.0, rating * chance)


def player_adjustment(state, league_key, player, base, season):
    model = state['player_models'].get(league_key, {})
    if not season or model.get('season') != season:
        return 0.0
    record = model.get('players', {}).get(str(player.external_id), {})
    rounds = int(record.get('rounds', 0))
    expected = expected_contribution(player)
    if rounds < MIN_ROUNDS_TO_APPLY or expected <= 0:
        return 0.0
    # Residuals are learned in fantasy-point units, then converted back to
    # ranking-score units. Do not subtract a ranking score from fantasy points.
    residual = float(record.get('mean_residual', 0))
    if not math.isfinite(residual):
        return 0.0
    residual = max(-2.0, min(2.0, residual))
    scale = min(1.0, max(0.0, base / expected))
    return residual * min(1.0, rounds / SHRINKAGE_ROUNDS) * scale


def adjust_scores(state: dict, league_key: str, players, scores: dict[str, float], season=None) -> dict[str, float]:
    """Apply bounded feedback without promoting unavailable/zero-chance players."""
    adjusted = dict(scores)
    for player in players:
        base = float(scores[player.name])
        if base <= 0 or player.unavailable:
            continue
        if player.start_probability == 0:
            continue
        offset = role_adjustment(state, league_key, player.role)
        offset += player_adjustment(state, league_key, player, base, season)
        adjusted[player.name] = round(max(0.0, base + offset), 3)
    return adjusted


def submission_error(pending, match):
    saved_at = datetime.fromisoformat(pending['saved_at'])
    if saved_at.tzinfo is None or match['submitted_at'].tzinfo is None:
        return 'submission timestamp has no timezone'
    if abs((saved_at - match['submitted_at']).total_seconds()) > 600:
        return 'submitted lineup timestamp differs from the saved decision'
    expected_ids = {int(pid) for pid, p in pending['roster'].items() if p['group'] == 'starter'}
    if expected_ids != set(match.get('submitted_starter_ids', [])):
        return 'submitted starting XI differs from the saved decision'
    return None


def apply_roster_outcome(state, league_key, round_number, actual, match):
    """Review all owned players, then learn once from a complete matched round."""
    from squad_picker import Player, pick_squad, FormationError
    key = str(round_number)
    pending = state['pending'].get(league_key, {}).get(key)
    if not pending or 'roster' not in pending:
        return False, 'no whole-roster snapshot for this round'
    error = submission_error(pending, match)
    if error:
        return False, error
    roster = pending['roster']
    if set(actual) != set(roster):
        return False, 'whole-roster result coverage is incomplete'
    for result in actual.values():
        if not math.isfinite(result['score']) or not -20 <= result['score'] <= 50 or result['goals'] < 0:
            return False, 'invalid individual result'
    players = [Player(pid, item['role'], external_id=int(pid), start_probability=1,
                      unavailable=not actual[pid].get('rated', True))
               for pid, item in roster.items()]
    scores = {pid: actual[pid]['score'] for pid in roster}
    try:
        best = pick_squad(players, scores, 'auto', pending['allowed_formations'], pending['bench_roles'])
    except FormationError:
        best = None  # Do not invent votes or prefer a non-player's zero score.
    try:
        same_formation = pick_squad(players, scores, pending['formation'], bench_roles=pending['bench_roles'])
    except FormationError:
        same_formation = None
    best_ids = [player.name for player in best.starters] if best else []
    starter_ids = [pid for pid, item in roster.items() if item['group'] == 'starter']
    chosen_total = sum(scores[pid] for pid in starter_ids)
    best_total = sum(scores[pid] for pid in best_ids) if best else None
    alternatives = [dict(player_id=int(pid), name=roster[pid]['name'], role=roster[pid]['role'],
                         group=roster[pid]['group'], **actual[pid])
                    for pid in roster if roster[pid]['group'] != 'starter'
                    and (actual[pid]['goals'] > 0 or pid in best_ids)]
    review = {
        'round': round_number, 'real_matchweek': match['real_matchweek'],
        'season': pending['context']['season'], 'formation': pending['formation'],
        'starter_points': round(chosen_total, 3), 'best_formation': best.formation if best else None,
        'same_formation_best_points': round(sum(scores[p.name] for p in same_formation.starters), 3) if same_formation else None,
        'best_points': round(best_total, 3) if best else None,
        'hindsight_gap': round(max(0.0, best_total-chosen_total), 3) if best else None,
        'bench_goals': sum(actual[pid]['goals'] for pid in roster if roster[pid]['group'] == 'bench'),
        'unused_bench_goals': sum(actual[pid]['goals'] for pid in roster
                                 if roster[pid]['group'] == 'bench' and not actual[pid]['used_substitute']),
        'excluded_goals': sum(actual[pid]['goals'] for pid in roster if roster[pid]['group'] == 'excluded'),
        'best_xi': [int(pid) for pid in best_ids], 'alternatives': alternatives,
        'results': actual, 'roster': roster,
        'basis': 'Individual fantasy scores among rated players; hindsight XI gap excludes substitutions and team/captain modifiers. '
                 'Unavailable comparisons mean there were not enough rated players for a valid XI.',
    }
    season = pending['context']['season']
    model = state['player_models'].get(league_key)
    if not model or model.get('season') != season:
        model = {'season': season, 'players': {}, 'matchweeks': []}
        state['player_models'][league_key] = model
    if match['real_matchweek'] not in model['matchweeks']:
        for pid, item in roster.items():
            residual = actual[pid]['score'] - item['expected_contribution']
            record = model['players'].setdefault(pid, {'rounds': 0, 'mean_residual': 0.0})
            count = record['rounds']
            alpha = 1/(count+1) if count < 5 else .2
            record['mean_residual'] = round(record['mean_residual'] + alpha*(residual-record['mean_residual']), 6)
            record['rounds'] = count + 1
        model['matchweeks'].append(match['real_matchweek'])
    reviews = state['reviews'].setdefault(league_key, {})
    reviews[key] = review
    for old in sorted(reviews, key=int)[:-12]:
        del reviews[old]
    del state['pending'][league_key][key]
    if not state['pending'][league_key]:
        del state['pending'][league_key]
    return True, 'whole-roster feedback recorded'
