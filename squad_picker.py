from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

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


@dataclass
class PickedSquad:
    formation: str
    starters: list[Player]
    captain: Player
    bench: list[Player]


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
    return counts


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def compute_score(player: Player, weights: dict[str, float]) -> float:
    votes = player.votes[-5:] if player.votes else []
    avg_last5 = mean(votes)
    avg_last3 = mean(player.votes[-3:]) if player.votes else 0.0
    score = (
        weights.get("avg", 0.0) * avg_last5
        + weights.get("trend", 0.0) * (avg_last3 - avg_last5)
        + weights.get("home", 0.0) * (1.0 if player.home else 0.0)
        + weights.get("opponent", 0.0) * player.opponent_weakness
    )
    return round(score, 3)


def pick_squad(
    players: list[Player], scores: dict[str, float], formation: str = DEFAULT_FORMATION
) -> PickedSquad:
    counts = parse_formation(formation)
    by_role: dict[str, list[Player]] = defaultdict(list)
    for player in players:
        by_role[player.role].append(player)

    starters: list[Player] = []
    for role in POSITION_ORDER:
        eligible = sorted(
            by_role[role], key=lambda p: scores.get(p.name, 0.0), reverse=True
        )
        if len(eligible) < counts[role]:
            raise FormationError(
                f"Not enough {role} players: need {counts[role]}, have {len(eligible)}"
            )
        starters.extend(eligible[: counts[role]])

    captain = max(starters, key=lambda p: scores.get(p.name, 0.0))
    starter_names = {p.name for p in starters}
    bench = [p for p in players if p.name not in starter_names]
    return PickedSquad(formation=formation, starters=starters, captain=captain, bench=bench)
