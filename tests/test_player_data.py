from player_data import _parse_api_players, _parse_role, normalize_league_url


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
