from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
import math

POSITION_ORDER = ("G", "D", "C", "A")
DEFAULT_FORMATION = "3-4-3"


class FormationError(ValueError):
    pass


@dataclass
class Player:
    name: str
    role: str
    votes: list[float] = field(default_factory=list)
    home: bool = False
    opponent_weakness: float = 0.0
    external_id: int | None = None
    start_probability: float | None = None
    unavailable: bool = False
    rating_source: str = "historical votes"


@dataclass
class PickedSquad:
    formation: str
    starters: list[Player]
    captain: Player
    bench: list[Player]
    vice: Player | None = None


def parse_formation(formation: str) -> dict[str, int]:
    raw = formation.strip()
    parts = raw.split("-")
    if len(parts) != 3:
        raise FormationError(f"Invalid formation {raw!r}: expected 'D-C-A' style like '3-4-3'")
    try:
        counts = dict(zip(("D", "C", "A"), (int(p) for p in parts)))
    except ValueError:
        raise FormationError(f"Invalid formation {raw!r}: counts must be integers")
    counts["G"] = 1
    if sum(counts.values()) != 11:
        raise FormationError(f"Invalid formation {raw!r}: outfielders must sum to 10")
    if not (3 <= counts["D"] <= 5 and 3 <= counts["C"] <= 5 and 1 <= counts["A"] <= 3):
        raise FormationError(f"Unsupported Classic formation {raw!r}")
    return counts


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def compute_score(player: Player, weights: dict[str, float]) -> float:
    if player.unavailable:
        return 0.0
    votes = player.votes[-5:] if player.votes else []
    avg_last5 = mean(votes)
    avg_last3 = mean(player.votes[-3:]) if player.votes else 0.0
    score = (
        weights.get("avg", 0.0) * avg_last5
        + weights.get("trend", 0.0) * (avg_last3 - avg_last5)
        + weights.get("home", 0.0) * (1.0 if player.home else 0.0)
        + weights.get("opponent", 0.0) * player.opponent_weakness
    )
    # The percentage is the site's starting likelihood, not a guarantee of
    # minutes or an expected fantasy-points model. Unknown uses a neutral 50%.
    if player.start_probability is not None:
        if not math.isfinite(player.start_probability) or not 0 <= player.start_probability <= 1:
            raise ValueError(f"Invalid starting probability for {player.name}")
        score *= player.start_probability
    elif player.rating_source != "historical votes":
        score *= 0.5
    if not math.isfinite(score):
        raise ValueError(f"Non-finite score for {player.name}")
    return round(score, 3)


def pick_squad(
    players: list[Player], scores: dict[str, float], formation: str = DEFAULT_FORMATION,
    allowed_formations: list[str] | None = None, bench_roles: list[str] | None = None,
) -> PickedSquad:
    if len({p.name.casefold() for p in players}) != len(players):
        raise FormationError("Duplicate player names in the roster; cannot select unambiguously")
    if formation == "auto":
        if not allowed_formations:
            raise FormationError("Auto formation requires the league's allowed formations")
        candidates = []
        for candidate in sorted(set(allowed_formations)):
            try:
                candidates.append(pick_squad(players, scores, candidate, bench_roles=bench_roles))
            except FormationError:
                continue
        if not candidates:
            raise FormationError("No allowed formation can be filled with eligible players and reserves")
        return max(candidates, key=lambda squad: sum(scores.get(p.name, 0) for p in squad.starters))
    formation = formation.strip()
    if allowed_formations is not None and formation not in allowed_formations:
        raise FormationError(f"Formation {formation} is not offered by this league")
    counts = parse_formation(formation)
    by_role: dict[str, list[Player]] = defaultdict(list)
    for player in players:
        by_role[player.role].append(player)

    starters: list[Player] = []
    for role in POSITION_ORDER:
        eligible = sorted(
            [p for p in by_role[role] if not p.unavailable and p.start_probability != 0],
            key=lambda p: (-scores.get(p.name, 0.0), p.name.casefold()),
        )
        if len(eligible) < counts[role]:
            raise FormationError(
                f"Not enough {role} players: need {counts[role]}, have {len(eligible)}"
            )
        starters.extend(eligible[: counts[role]])

    ranked_starters = sorted(
        starters, key=lambda p: scores.get(p.name, 0.0), reverse=True
    )
    captain = ranked_starters[0]
    vice = ranked_starters[1] if len(ranked_starters) > 1 else None
    starter_names = {p.name for p in starters}
    remaining = sorted(
        [p for p in players if p.name not in starter_names],
        key=lambda p: (p.unavailable, -scores.get(p.name, 0.0), p.name.casefold()),
    )
    bench = remaining
    if bench_roles is not None:
        # Fill constrained slots first so a free slot cannot consume the only
        # eligible reserve goalkeeper (or other required role).
        assignments = {}
        for index in sorted(range(len(bench_roles)), key=lambda i: bench_roles[i] == "*"):
            role = bench_roles[index]
            match = next((p for p in remaining if role == "*" or p.role == role), None)
            if match is None:
                raise FormationError(f"No reserve available for bench slot {index + 1} ({role})")
            assignments[index] = match
            remaining.remove(match)
        bench = [assignments[i] for i in range(len(bench_roles))]
    return PickedSquad(
        formation=formation,
        starters=starters,
        captain=captain,
        bench=bench,
        vice=vice,
    )
