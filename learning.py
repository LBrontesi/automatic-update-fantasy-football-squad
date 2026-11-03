"""Small, persistent role-level feedback model for past lineup results."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import math
from pathlib import Path

ROLES = ("G", "D", "C", "A")
STATE_VERSION = 1
DEFAULT_STATE_PATH = Path("data/learning_state.json")
MIN_ROUNDS_TO_APPLY = 3
SHRINKAGE_ROUNDS = 6
MAX_ABS_ADJUSTMENT = 1.5


def empty_state() -> dict:
    return {"version": STATE_VERSION, "models": {}, "pending": {}}


def load_state(path: str | Path = DEFAULT_STATE_PATH) -> dict:
    path = Path(path)
    if not path.exists():
        return empty_state()
    state = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(state, dict)
        or state.get("version") != STATE_VERSION
        or not isinstance(state.get("models"), dict)
        or not isinstance(state.get("pending"), dict)
    ):
        raise ValueError(f"Unsupported or malformed learning state at {path}")
    return state


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
) -> None:
    """Store only predicted score totals and player counts by role."""
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
    league_pending = state["pending"].setdefault(league_key, {})
    league_pending[str(int(round_number))] = {
        "prediction_by_role": prediction,
        "saved_at": saved_at.astimezone(timezone.utc).isoformat(),
    }
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


def adjust_scores(state: dict, league_key: str, players, scores: dict[str, float]) -> dict[str, float]:
    """Apply learned role offsets while keeping unavailable players at zero."""
    adjusted = dict(scores)
    for player in players:
        base = float(scores[player.name])
        if base <= 0 or player.unavailable:
            continue
        offset = role_adjustment(state, league_key, player.role)
        adjusted[player.name] = round(max(0.0, base + offset), 3)
    return adjusted
