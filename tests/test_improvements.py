from argparse import Namespace
from datetime import datetime

import pytest

import auto_clicker_fanta as bot
import schedule_guard
from lineup_editor import player_from_record, verify_lineup
from player_data import ExtractionError, LineupUIError
from predictions import LeagueData, HistoricalSource
from run_report import render_summary
from squad_picker import FormationError, Player, pick_squad


def record(**overrides):
    return dict(id=1, name='Player', role='P', game_type='1',
                metrics={'FM': '6,5', 'MV': '6'}, probability='90', **overrides)


def test_roster_metrics_are_one_average_not_two_sequential_votes():
    player = player_from_record(record())
    assert player.votes == [6.5]
    assert player.rating_source == 'fantasy average'
    assert player.start_probability == .9


def test_unlabelled_metrics_use_explicit_neutral_prior():
    raw = record()
    raw['metrics'] = {'FM': 'NaN', 'MV': '0'}
    player = player_from_record(raw)
    assert player.votes == [6]
    assert player.rating_source == 'neutral prior (no ratings)'


@pytest.mark.parametrize('change', [{'game_type': '2'}, {'role': 'Dd'}, {'id': None}])
def test_unknown_roles_and_missing_ids_fail_closed(change):
    raw = record()
    raw.update(change)
    with pytest.raises(ExtractionError):
        player_from_record(raw)


def pool():
    return [Player(f'{role}{i}', role, [6], external_id=j * 10 + i,
                   start_probability=.9, rating_source='fantasy average')
            for j, (role, count) in enumerate([('G', 3), ('D', 8), ('C', 8), ('A', 6)])
            for i in range(count)]


def test_starting_likelihood_beats_high_average_with_low_chance():
    players = pool()
    players[0].votes = [10]
    players[0].start_probability = .05
    league = LeagueData('x', 'x', players)
    scores = HistoricalSource().predict(league)
    picked = pick_squad(players, scores)
    assert picked.starters[0].name == 'G1'


def test_auto_formation_selects_more_high_scoring_midfielders():
    players = pool()
    scores = {p.name: 10 if p.role == 'C' else 2 for p in players}
    picked = pick_squad(players, scores, 'auto', ['3-4-3', '3-5-2'])
    assert picked.formation == '3-5-2'


def test_unavailable_players_and_zero_chance_never_start():
    players = pool()
    players[0].unavailable = True
    players[1].start_probability = 0
    scores = {p.name: 10 for p in players}
    assert pick_squad(players, scores).starters[0].name == 'G2'


def test_reserves_match_league_roles_and_do_not_duplicate_starters():
    players = pool()
    scores = {p.name: p.external_id for p in players}
    roles = ['G', 'D', 'D', 'C', 'C', 'A', 'A']
    picked = pick_squad(players, scores, bench_roles=roles)
    assert [p.role for p in picked.bench] == roles
    assert len({p.name for p in picked.starters + picked.bench}) == 18
    for i in [1, 3, 5]:
        assert scores[picked.bench[i].name] >= scores[picked.bench[i+1].name]


def test_free_bench_slot_does_not_steal_only_reserve_keeper():
    players = pool()
    players = [p for p in players if p.name != 'G2']
    scores = {p.name: 100 if p.role == 'G' else 1 for p in players}
    picked = pick_squad(players, scores, bench_roles=['*', 'G'])
    assert picked.bench[1].role == 'G'


def test_configured_formation_cannot_override_league_rules():
    with pytest.raises(FormationError):
        pick_squad(pool(), {}, '3-4-3', ['4-4-2'])


@pytest.mark.parametrize('formation', ['-1-8-3', '1-1-8', '3-7-0'])
def test_invalid_counts_are_rejected(formation):
    with pytest.raises(FormationError):
        pick_squad(pool(), {}, formation)


def test_environment_dry_run_never_saves(monkeypatch):
    league = LeagueData('x', 'x', pool(), False, False, ['3-4-3'], ['G', 'D', 'C', 'A'])
    monkeypatch.setattr(bot, 'fetch_league_data', lambda *a, **kw: league)
    monkeypatch.setattr(bot, 'apply_lineup', lambda *a, **kw: pytest.fail('dry run wrote lineup'))
    cfg = dict(replace_existing_lineup=True, dry_run=True, source='historical', weights={}, formation='auto')
    assert bot.handle_league(None, cfg, 'x', Namespace(debug_dir=None, dry_run=False))['status'] == 'dry_run'
    cfg['replace_existing_lineup'] = False
    assert bot.handle_league(None, cfg, 'x', Namespace(debug_dir=None, dry_run=False))['status'] == 'skipped'


def test_save_mismatch_is_not_reported_as_success():
    with pytest.raises(LineupUIError):
        verify_lineup({'slots': {'0:0': 'G1'}}, {'slots': {'0:0': 'G2'}})


def test_matching_names_do_not_hide_a_different_saved_player_id():
    with pytest.raises(LineupUIError):
        verify_lineup({'slots': {'0:0': 'G1'}, 'slot_ids': {'0:0': 1}},
                      {'slots': {'0:0': 'G1'}, 'slot_ids': {'0:0': 2}})


def test_empty_or_stale_calendar_blocks_live_execution(monkeypatch):
    now = datetime.fromisoformat('2026-09-19T12:00:00+02:00')
    monkeypatch.setattr(schedule_guard, 'fetch_fixtures', lambda *a: [])
    assert not schedule_guard.check_schedule_guard(now=now).allowed
    monkeypatch.setattr(schedule_guard, 'fetch_fixtures', lambda *a: [schedule_guard.Fixture(datetime.fromisoformat('2026-09-18T12:00:00+02:00'))])
    assert not schedule_guard.check_schedule_guard(now=now).allowed


def test_summary_distinguishes_skip_from_saved():
    summary = render_summary([{'league':'one','status':'skipped','reason':'locked'},
                              {'league':'two','status':'saved','reason':'verified'}], False)
    assert 'one: skipped' in summary
    assert 'two: saved' in summary
    assert 'Only **saved**' in summary
