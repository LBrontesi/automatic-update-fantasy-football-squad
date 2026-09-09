from player_data import (
    _parse_api_players,
    _parse_my_roster_ids,
    _parse_role,
    normalize_league_url,
)


def test_normalize_legacy_formation_url() -> None:
    old = "https://leghe.fantacalcio.it/example/area-gioco/inserisci-formazione?id=123"
    assert normalize_league_url(old) == (
        "https://leghe.fantacalcio.it/example/view/competition/123/lineup"
    )


def test_current_formation_url_is_unchanged() -> None:
    current = "https://leghe.fantacalcio.it/example/view/competition/123/lineup"
    assert normalize_league_url(current) == current


def test_current_site_roles_are_mapped_to_model_roles() -> None:
    assert _parse_role("P") == "G"
    assert _parse_role("D") == "D"
    assert _parse_role("M") == "C"
    assert _parse_role("F") == "A"


def test_api_players_are_converted_to_scoring_players() -> None:
    payload = {
        "players": [
            {"name": "Caprile", "role": "P", "lastVotes": [6, 6.5, 7]},
            {"name": "Vojvoda", "role": "D", "lastVotes": [6, 6]},
            {"name": "Basic", "role": "M", "lastVotes": [6.5]},
            {"name": "Thuram", "role": "F", "lastVotes": [7]},
        ]
    }
    players = _parse_api_players(payload)
    assert [(p.name, p.role) for p in players] == [
        ("Caprile", "G"),
        ("Vojvoda", "D"),
        ("Basic", "C"),
        ("Thuram", "A"),
    ]
    assert players[0].votes == [6.0, 6.5, 7.0]


def test_live_api_roles_and_season_average_are_supported() -> None:
    payload = {
        "players": [
            {"id": 10, "name": "Keeper", "fcrle": 1, "fagrd": 6.8},
            {"id": 20, "name": "Defender", "fcrle": 2, "fagrd": 6.4},
            {"id": 30, "name": "Midfielder", "fcrle": 3, "fagrd": 6.6},
            {"id": 40, "name": "Forward", "fcrle": 4, "fagrd": 7.1},
        ]
    }
    players = _parse_api_players(payload)
    assert [(p.external_id, p.role, p.votes) for p in players] == [
        (10, "G", [6.8]),
        (20, "D", [6.4]),
        (30, "C", [6.6]),
        (40, "A", [7.1]),
    ]


def test_zero_last_five_values_fall_back_to_average() -> None:
    players = _parse_api_players(
        {"players": [{"id": 10, "name": "Keeper", "fcrle": 1, "l5frfc": 0, "faagr": 6.9}]}
    )
    assert players[0].votes == [6.9]


def test_empty_performance_metrics_fall_back_to_quotation() -> None:
    players = _parse_api_players(
        {"players": [{"id": 10, "name": "Keeper", "fcrle": 1, "quotd": 60}]}
    )
    assert players[0].votes == [6.0]


def test_owned_roster_ids_are_extracted_from_team_api() -> None:
    assert _parse_my_roster_ids({"cal": "10;20;", "pl": [{"id": 30}]}) == {
        10,
        20,
        30,
    }
