from __future__ import annotations

import pytest

from predictions import HistoricalSource
from squad_picker import (
    DEFAULT_FORMATION,
    FormationError,
    Player,
    compute_score,
    parse_formation,
    pick_squad,
)

WEIGHTS = {"avg": 0.4, "trend": 0.3, "home": 0.2, "opponent": 0.1}


def make_squad_players() -> list[Player]:
    players = [
        Player("GK1", "G", [6.0, 6.0, 6.5, 6.0, 6.5]),
        Player("GK2", "G", [5.5, 5.5, 5.0, 5.5, 5.0]),
        Player("D1", "D", [6.0, 6.5, 7.0, 6.5, 7.0]),
        Player("D2", "D", [6.0, 6.0, 6.5, 6.0, 6.0]),
        Player("D3", "D", [5.5, 6.0, 6.0, 6.5, 5.5]),
        Player("D4", "D", [5.0, 5.0, 5.0, 5.0, 5.0]),
        Player("C1", "C", [6.5, 6.5, 6.5, 6.5, 6.5]),
        Player("C2", "C", [6.0, 6.5, 6.5, 6.5, 7.0]),
        Player("C3", "C", [5.5, 6.0, 6.0, 6.0, 6.0]),
        Player("C4", "C", [5.0, 5.5, 5.5, 5.5, 5.5]),
        Player("C5", "C", [4.5, 5.0, 5.0, 5.0, 5.0]),
        Player("A1", "A", [7.0, 7.0, 7.0, 7.0, 7.0]),
        Player("A2", "A", [6.5, 6.5, 6.5, 6.5, 6.5]),
        Player("A3", "A", [6.0, 6.0, 6.0, 6.0, 6.0]),
        Player("A4", "A", [5.5, 5.5, 5.5, 5.5, 5.5]),
    ]
    return players


def test_parse_formation_valid() -> None:
    assert parse_formation("3-4-3") == {"G": 1, "D": 3, "C": 4, "A": 3}
    assert parse_formation(" 5-3-2 ") == {"G": 1, "D": 5, "C": 3, "A": 2}


@pytest.mark.parametrize("formation", ["3-4", "3-4-4", "x-4-3", "0-0-0"])
def test_parse_formation_invalid(formation: str) -> None:
    with pytest.raises(FormationError):
        parse_formation(formation)


def test_pick_squad_respects_formation() -> None:
    players = make_squad_players()
    scores = {p.name: compute_score(p, WEIGHTS) for p in players}
    picked = pick_squad(players, scores, DEFAULT_FORMATION)
    counts = {role: 0 for role in ("G", "D", "C", "A")}
    for player in picked.starters:
        counts[player.role] += 1
    assert counts == {"G": 1, "D": 3, "C": 4, "A": 3}
    assert len(picked.starters) == 11
    assert len(picked.bench) == len(players) - 11


def test_pick_squad_best_players_chosen() -> None:
    players = make_squad_players()
    scores = {p.name: compute_score(p, WEIGHTS) for p in players}
    picked = pick_squad(players, scores, DEFAULT_FORMATION)
    starters = {p.name for p in picked.starters}
    assert "GK1" in starters
    assert "D1" in starters
    assert "C1" in starters
    assert "C2" in starters
    assert "A1" in starters
    assert "GK2" not in starters
    assert "D4" not in starters
    assert "C5" not in starters


def test_pick_squad_captain_is_top_scorer() -> None:
    players = make_squad_players()
    scores = {p.name: compute_score(p, WEIGHTS) for p in players}
    picked = pick_squad(players, scores, DEFAULT_FORMATION)
    assert picked.captain.name == "A1"
    assert max(picked.starters, key=lambda p: scores[p.name]).name == picked.captain.name


def test_home_advantage_and_trend_influence_score() -> None:
    base = Player("X", "C", [6.0, 6.0, 6.0, 6.0, 6.0])
    home = Player("X", "C", [6.0, 6.0, 6.0, 6.0, 6.0], home=True)
    rising = Player("X", "C", [5.0, 5.5, 6.0, 6.5, 7.0])
    assert compute_score(home, WEIGHTS) > compute_score(base, WEIGHTS)
    assert compute_score(rising, WEIGHTS) > compute_score(base, WEIGHTS)


def test_no_votes_scores_zero() -> None:
    player = Player("Y", "A")
    assert compute_score(player, WEIGHTS) == 0.0


def test_historical_source_returns_scores_for_all_players() -> None:
    from predictions import LeagueData

    league = LeagueData(name="test", url="http://example.com", players=make_squad_players())
    scores = HistoricalSource().predict(league)
    assert len(scores) == len(league.players)
    assert scores["A1"] > scores["D4"]


def test_pick_squad_not_enough_role_players() -> None:
    players = [Player("GK1", "G", [6.0]), Player("D1", "D", [6.0])]
    scores = {p.name: compute_score(p, WEIGHTS) for p in players}
    with pytest.raises(FormationError):
        pick_squad(players, scores, DEFAULT_FORMATION)
