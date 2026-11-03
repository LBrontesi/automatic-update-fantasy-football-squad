from datetime import datetime, timezone

from learning import (
    adjust_scores,
    apply_round_outcome,
    empty_state,
    load_state,
    record_pending,
    role_adjustment,
    save_state,
)
from lineup_editor import competition_base_url, parse_competition_rounds
from squad_picker import PickedSquad, Player


def make_picked():
    players = [
        Player(f"Player {index}", role, external_id=index)
        for index, role in enumerate(
            ["G", "D", "D", "D", "C", "C", "C", "C", "A", "A", "A"],
            start=1,
        )
    ]
    scores = {player.name: 6.0 + index / 10 for index, player in enumerate(players)}
    return PickedSquad("3-4-3", players, players[0], []), scores, players


def test_persisted_pending_state_contains_aggregates_not_player_data():
    state = empty_state()
    picked, scores, _ = make_picked()
    saved_at = datetime(2026, 10, 25, 17, 0, tzinfo=timezone.utc)
    record_pending(state, "league_1", 8, picked, scores, saved_at)

    serialized = str(state)
    assert "Player 1" not in serialized
    assert "external_id" not in serialized
    assert state["pending"]["league_1"]["8"]["prediction_by_role"]["D"]["count"] == 3


def test_outcome_updates_role_residual_once_and_affects_later_scores():
    state = empty_state()
    picked, scores, players = make_picked()
    saved_at = datetime(2026, 10, 25, 17, 0, tzinfo=timezone.utc)

    for round_number in (6, 7, 8):
        record_pending(state, "league_1", round_number, picked, scores, saved_at)
        actual = {role: [] for role in ("G", "D", "C", "A")}
        for player in picked.starters:
            actual[player.role].append(scores[player.name] + 1.0)
        applied, reason = apply_round_outcome(
            state, "league_1", round_number, actual, saved_at
        )
        assert applied, reason

    assert role_adjustment(state, "league_1", "D") == 0.5
    adjusted = adjust_scores(state, "league_1", players, scores)
    assert adjusted[players[1].name] == scores[players[1].name] + 0.5
    assert state["pending"] == {}
    applied, reason = apply_round_outcome(
        state, "league_1", 8, {role: [] for role in ("G", "D", "C", "A")}, saved_at
    )
    assert not applied
    assert reason == "no pending prediction for this round"


def test_timestamp_mismatch_discards_outcome_without_learning():
    state = empty_state()
    picked, scores, _ = make_picked()
    saved_at = datetime(2026, 10, 25, 17, 0, tzinfo=timezone.utc)
    record_pending(state, "league_1", 8, picked, scores, saved_at)
    actual = {role: [] for role in ("G", "D", "C", "A")}
    for player in picked.starters:
        actual[player.role].append(scores[player.name] + 2.0)

    applied, reason = apply_round_outcome(
        state,
        "league_1",
        8,
        actual,
        datetime(2026, 10, 25, 18, 0, tzinfo=timezone.utc),
    )
    assert not applied
    assert "timestamp" in reason
    assert state["models"] == {}
    assert state["pending"] == {}


def test_state_round_trips_atomically(tmp_path):
    state = empty_state()
    picked, scores, _ = make_picked()
    record_pending(
        state,
        "league_1",
        8,
        picked,
        scores,
        datetime(2026, 10, 25, 17, 0, tzinfo=timezone.utc),
    )
    target = tmp_path / "state.json"
    save_state(state, target)
    assert load_state(target) == state


def test_dashboard_round_numbers_are_parsed_from_both_sections():
    dashboard = """
    Giornata in corso
    6ª giornata 8ª giornata di Serie A
    Ultima giornata
    5ª giornata 7ª giornata di Serie A
    """
    assert parse_competition_rounds(dashboard) == (6, 5)


def test_competition_base_url_keeps_the_league_slug():
    assert competition_base_url(
        "https://leghe.fantacalcio.it/rusticatorsapienza/view/competition/532351/lineup"
    ) == "https://leghe.fantacalcio.it/rusticatorsapienza/view/competition/532351"
