from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json

import pytest

from feedback_data import (parse_history, fantasy_score, parse_calculated_match,
                           collect_roster_results, captured_json, result_round_mapping)
from learning import (empty_state, record_pending, apply_roster_outcome, adjust_scores,
                      load_state, bind_league)
from player_data import ExtractionError
from run_report import render_summary
from squad_picker import Player, PickedSquad

SAVED_AT = datetime(2026, 11, 5, 15, 45, tzinfo=timezone.utc)
CONTEXT = {'season': '2026/27', 'season_id': 21, 'source': 1,
           'bonuses': {'bmgs': [3, 4, 3, 3], 'bmpsc': 3, 'bmyc': -.5}}


def decision():
    roster = [Player(f'{role}{i}', role, [6.0], external_id=offset+i, start_probability=.8)
              for role, count, offset in [('G', 3, 0), ('D', 8, 10), ('C', 8, 20), ('A', 6, 30)]
              for i in range(1, count+1)]
    by_name = {p.name: p for p in roster}
    starters = [by_name[n] for n in ['G1','D1','D2','D3','C1','C2','C3','C4','A1','A2','A3']]
    bench = [by_name[n] for n in ['G2','D4','D5','C5','C6','A4','A5']]
    return roster, PickedSquad('3-4-3', starters, starters[0], bench), {p.name: 1.92 for p in roster}


def snapshot(state, round_number=8):
    roster, picked, scores = decision()
    record_pending(state, 'league', round_number, picked, scores, SAVED_AT,
        roster=roster, allowed_formations=['3-4-3', '4-3-3', '3-5-2'],
        bench_roles=['G','D','D','C','C','A','A'], context=CONTEXT, base_scores=scores)
    return roster, picked, scores


def outcome(roster, real_week=10):
    actual = {str(p.external_id): {'score': 6.0, 'goals': 0, 'used_substitute': False} for p in roster}
    actual['14'].update(score=10, goals=1)  # unused defender on bench
    actual['34'].update(score=9, goals=1, used_substitute=True)
    actual['36'].update(score=12, goals=2)  # outside the seven reserves
    actual['27'].update(score=9, goals=1)   # excluded midfielder
    return actual, {'submitted_at': SAVED_AT, 'real_matchweek': real_week,
                    'submitted_starter_ids': [p.external_id for p in decision()[1].starters]}


def test_snapshot_remembers_every_player_and_their_selection_group():
    state = empty_state()
    roster, picked, _ = snapshot(state)
    pending = state['pending']['league']['8']
    assert len(pending['roster']) == 25
    assert pending['roster']['36']['group'] == 'excluded'
    assert pending['roster']['14']['group'] == 'bench'
    assert pending['roster']['11']['group'] == 'starter'
    assert pending['roster']['36']['expected_contribution'] == pytest.approx(4.8)
    assert pending['roster']['36']['scale'] == pytest.approx(.4)


def test_hindsight_counts_unused_bench_and_excluded_goals_separately():
    state = empty_state()
    roster, _, _ = snapshot(state)
    actual, match = outcome(roster)
    applied, reason = apply_roster_outcome(state, 'league', 8, actual, match)
    assert applied, reason
    review = state['reviews']['league']['8']
    assert review['bench_goals'] == 2
    assert review['unused_bench_goals'] == 1  # the substitute's goal was recovered
    assert review['excluded_goals'] == 3
    assert review['starter_points'] == 66
    assert review['best_points'] > 66
    assert review['hindsight_gap'] == review['best_points'] - 66
    assert 36 in review['best_xi']
    assert len(review['best_xi']) == 11
    assert any(p['player_id'] == 36 and p['group'] == 'excluded' for p in review['alternatives'])
    assert state['player_models']['league']['players']['36']['rounds'] == 1
    assert state['pending'] == {}


def test_player_history_changes_rank_after_three_rounds_without_overreacting():
    state = empty_state()
    for r in (6, 7, 8):
        roster, _, scores = snapshot(state, r)
        actual, match = outcome(roster, r+2)
        assert apply_roster_outcome(state, 'league', r, actual, match)[0]
        adjusted = adjust_scores(state, 'league', roster, scores, season='2026/27')
        if r < 8:
            assert adjusted == scores
    assert adjusted['A6'] > adjusted['A1']
    assert adjusted['A6'] - scores['A6'] == pytest.approx(.4)
    assert adjust_scores(state, 'league', roster, scores, season='2027/28') == scores
    roster[-1].unavailable = True
    scores['A6'] = 0
    assert adjust_scores(state, 'league', roster, scores, season='2026/27')['A6'] == 0


def test_completed_round_is_not_trained_twice_even_with_another_competition_round():
    state = empty_state()
    roster, _, _ = snapshot(state)
    actual, match = outcome(roster)
    assert apply_roster_outcome(state, 'league', 8, actual, match)[0]
    model = deepcopy(state['player_models'])
    assert not apply_roster_outcome(state, 'league', 8, actual, match)[0]
    snapshot(state, 9)
    assert apply_roster_outcome(state, 'league', 9, actual, match)[0]
    assert state['player_models'] == model


def test_hindsight_cannot_replace_a_negative_goalkeeper_with_a_nonplaying_zero():
    state = empty_state()
    roster, _, _ = snapshot(state)
    actual, match = outcome(roster)
    actual['1']['score'] = -2
    for pid in ('2', '3'):
        actual[pid].update(score=0, rated=False)
    assert apply_roster_outcome(state, 'league', 8, actual, match)[0]
    assert 1 in state['reviews']['league']['8']['best_xi']
    assert 2 not in state['reviews']['league']['8']['best_xi']


def test_too_few_rated_players_defers_hindsight_comparison_without_inventing_votes():
    state = empty_state()
    roster, _, _ = snapshot(state)
    actual, match = outcome(roster)
    for result in actual.values():
        result.update(score=0, rated=False, goals=0)
    assert apply_roster_outcome(state, 'league', 8, actual, match)[0]
    review = state['reviews']['league']['8']
    assert review['best_xi'] == []
    assert review['best_points'] is None
    assert review['hindsight_gap'] is None


@pytest.mark.parametrize('problem', ['missing', 'timestamp', 'nan', 'manual_swap'])
def test_unreliable_results_cannot_change_the_model(problem):
    state = empty_state()
    roster, _, _ = snapshot(state)
    actual, match = outcome(roster)
    if problem == 'missing':
        del actual['36']
    elif problem == 'timestamp':
        match['submitted_at'] += timedelta(hours=1)
    elif problem == 'nan':
        actual['36']['score'] = float('nan')
    else:
        match['submitted_starter_ids'].remove(31)
        match['submitted_starter_ids'].append(34)
    before = deepcopy(state)
    assert not apply_roster_outcome(state, 'league', 8, actual, match)[0]
    assert state == before


def test_legacy_state_migrates_without_reassigning_after_secret_reordering(tmp_path):
    path = tmp_path/'state.json'
    path.write_text(json.dumps({'version': 1, 'models': {'league_1': {'D': {'rounds': 3}}}, 'pending': {}}))
    state = load_state(path)
    assert state['version'] == 2
    bind_league(state, 'league_1', 'first_competition')
    bind_league(state, 'league_1', 'second_competition')
    assert state['models']['first_competition']['D']['rounds'] == 3
    assert 'second_competition' not in state['models']


def history_html(grade='6,5', finished=4, events=''):
    return f'''<script>var Bridge={{playerId:133,season:"2026/27"}}</script>
      <table class="player-summary-table"><tbody><tr>
      <th><span class="matchweek player-status">10</span></th>
      <td><a class="match match-status-{finished}" href="/serie-a/calendario/10/2026-27/a-b/1"></a></td>
      <td><span class="grade" data-value="{grade}"></span></td>
      <td><span class="fanta-grade" data-value="12,5"></span></td>
      <td>{events}</td></tr></tbody></table>'''


def test_labelled_history_parses_goals_and_custom_role_bonus_rules():
    events = ''.join(f'<figure data-key="{key}" data-value="{value}"></figure>'
                     for key, value in [('scoredGoals', 2), ('scoredPenalties', 1), ('yellowCards', 1)])
    season, rows = parse_history(history_html(events=events), 133)
    assert season == '2026/27'
    assert fantasy_score(rows[10], 'D', CONTEXT['bonuses']) == 17
    assert fantasy_score(rows[10], 'A', CONTEXT['bonuses']) == 15
    assert rows[10]['events']['scoredPenalties'] == 1
    with pytest.raises(ExtractionError, match='identity'):
        parse_history(history_html(), 134)


def test_finished_no_vote_is_zero_but_pending_or_unrated_events_are_not_guessed():
    _, rows = parse_history(history_html(grade=''), 133)
    assert fantasy_score(rows[10], 'G', CONTEXT['bonuses']) == 0
    _, rows = parse_history(history_html(finished=0), 133)
    with pytest.raises(ExtractionError, match='not finished'):
        fantasy_score(rows[10], 'G', CONTEXT['bonuses'])
    _, rows = parse_history(history_html(grade='', events='<figure data-key="yellowCards" data-value="1"></figure>'), 133)
    with pytest.raises(ExtractionError, match='default-vote'):
        fantasy_score(rows[10], 'G', CONTEXT['bonuses'])


def match_payload():
    entries = [{'pid': i, 'scr': 6, 'cscr': 6, 'ptype': ''} for i in range(1, 19)]
    side = {'starts': entries[:11], 'bench': entries[11:], 'ldate': '20261105154500000'}
    return {'cal': True, 'idcomp': 42, 'mday': 8, 'cmday': 10, 'home': side, 'away': {}}


def test_calculated_result_requires_exact_submitted_squad_round_and_timestamp():
    payload = match_payload()
    payload['home']['bench'][0]['ptype'] = 'E'
    payload['home']['bench'][1].update(scr=56, cscr=56)
    match = parse_calculated_match(payload, 42, 8, set(range(1, 19)))
    assert match['real_matchweek'] == 10  # competition round is different
    assert match['submitted_at'] == SAVED_AT
    assert match['players'][12]['used_substitute']
    assert match['players'][13]['score'] == 0
    with pytest.raises(ExtractionError):
        parse_calculated_match(payload, 42, 8, set(range(2, 20)))
    payload['cal'] = False
    with pytest.raises(ExtractionError):
        parse_calculated_match(payload, 42, 8, set(range(1, 19)))


def test_admin_results_need_a_calculated_competition_to_serie_a_mapping():
    sides = [match_payload()['home']]
    mapping = [{'matchDay': 8, 'championshipMatchDay': 10, 'calculated': True}]
    assert parse_calculated_match(sides, 42, 8, set(range(1, 19)), calendar=mapping)['real_matchweek'] == 10
    with pytest.raises(ExtractionError):
        parse_calculated_match(sides, 42, 8, set(range(1, 19)), calendar=[])


def test_observed_result_request_maps_only_the_requested_calculated_round():
    path = '/gaming/v1/teamLineup/42/8/10'
    assert result_round_mapping(path, 42, 8, 8) == [
        {'matchDay': 8, 'championshipMatchDay': 10, 'calculated': True}]
    assert result_round_mapping(path, 42, 8, 7) is None
    assert result_round_mapping(path, 43, 8, 8) is None
    assert result_round_mapping(path, 42, 7, 8) is None
    assert result_round_mapping(path, 42, 8, None) is None


def test_wrapped_array_responses_are_unwrapped_without_reading_auth_headers():
    class Driver:
        def execute_cdp_cmd(self, method, params):
            return {'body': json.dumps({'success': True, 'data': [{'matchDay': 8}]})}
    messages = [{'method': 'Network.responseReceived', 'params': {'requestId': '1',
                 'response': {'url': 'https://apileague.fantacalcio.it/api/league/calendar/42', 'status': 200}}}]
    assert captured_json(Driver(), messages, lambda path: path.endswith('/42')) == [[{'matchDay': 8}]]


def test_substitution_flags_reconstruct_the_original_starting_xi():
    payload = match_payload()
    outgoing = payload['home']['starts'][0]
    incoming = payload['home']['bench'][0]
    outgoing['ptype'], incoming['ptype'] = 'U', 'E'
    payload['home']['starts'][0], payload['home']['bench'][0] = incoming, outgoing
    match = parse_calculated_match(payload, 42, 8, set(range(1, 19)))
    assert match['submitted_starter_ids'] == list(range(1, 12))


def test_collection_cross_checks_official_scores_and_rejects_season_mismatch():
    pending = {'context': CONTEXT, 'roster': {'133': {'role': 'G'}}}
    _, rows = parse_history(history_html(grade='6'), 133)
    match = {'real_matchweek': 10, 'players': {133: {'score': 6, 'used_substitute': False}}}
    results = collect_roster_results(pending, match, lambda *_: ('2026/27', rows))
    assert results['133']['score'] == 6
    with pytest.raises(ExtractionError, match='season'):
        collect_roster_results(pending, match, lambda *_: ('2025/26', rows))
    match['players'][133]['score'] = 7
    with pytest.raises(ExtractionError, match='mismatch'):
        collect_roster_results(pending, match, lambda *_: ('2026/27', rows))


def test_feedback_report_exposes_bench_exclusions_and_hindsight_limits():
    state = empty_state()
    roster, _, _ = snapshot(state)
    actual, match = outcome(roster)
    apply_roster_outcome(state, 'league', 8, actual, match)
    text = render_summary([{'league': 'test', 'status': 'saved', 'reason': 'verified',
                           'feedback_review': state['reviews']['league']['8']}], False)
    assert 'Goals outside the submitted squad: 3' in text
    assert '1 from unused reserves' in text
    assert 'A6' in text
    assert 'excludes substitutions and team/captain modifiers' in text


def orchestrated_league(monkeypatch):
    import auto_clicker_fanta as bot
    from predictions import LeagueData
    roster, _, _ = decision()
    url = 'https://leghe.fantacalcio.it/example/view/competition/42/lineup'
    league = LeagueData('example', url, roster, False, False,
                       ['3-4-3','4-3-3','3-5-2'], ['G','D','D','C','C','A','A'])
    monkeypatch.setattr(bot, 'read_competition_rounds', lambda *a, **kw: (8, 7))
    monkeypatch.setattr(bot, 'fetch_league_data', lambda *a, **kw: league)
    monkeypatch.setattr(bot, 'capture_settings', lambda *_: deepcopy(CONTEXT))
    monkeypatch.setattr(bot, 'fetch_history', lambda *_: ('2026/27', {}))
    monkeypatch.setattr(bot, 'restore_lineup_page', lambda *_: None)
    cfg = dict(replace_existing_lineup=True, dry_run=False, source='historical', weights={}, formation='auto')
    return bot, league, cfg


def test_verified_save_queues_full_roster_but_failed_save_cannot(monkeypatch):
    from argparse import Namespace
    bot, league, cfg = orchestrated_league(monkeypatch)
    state = empty_state()
    monkeypatch.setattr(bot, 'apply_lineup', lambda *a, **kw: {'verified': True})
    result = bot.handle_league(None, cfg, league.url, Namespace(debug_dir=None, dry_run=False), state)
    assert result['status'] == 'saved'
    key = 'example_view_competition_42_lineup'
    assert len(state['pending'][key]['8']['roster']) == 25
    assert result['feedback_pending'] == 8
    def fail(*a, **kw):
        raise RuntimeError('save failed')
    monkeypatch.setattr(bot, 'apply_lineup', fail)
    monkeypatch.setattr(bot, 'save_snapshot', lambda *a, **kw: None)
    state = empty_state()
    with pytest.raises(RuntimeError, match='save failed'):
        bot.handle_league(None, cfg, league.url, Namespace(debug_dir=None, dry_run=False), state)
    assert state['pending'] == {}


def test_source_check_validates_all_players_without_saving_or_training(monkeypatch):
    from argparse import Namespace
    bot, league, cfg = orchestrated_league(monkeypatch)
    match = {'real_matchweek': 10, 'players': {p.external_id: {} for p in league.players[:18]}}
    monkeypatch.setattr(bot, 'read_calculated_match', lambda *a, **kw: match)
    monkeypatch.setattr(bot, 'collect_roster_results', lambda *_:
                        {str(p.external_id): {'score': 6} for p in league.players})
    monkeypatch.setattr(bot, 'apply_lineup', lambda *a, **kw: pytest.fail('source check saved a lineup'))
    state = empty_state()
    result = bot.handle_league(None, cfg, league.url,
        Namespace(debug_dir=None, dry_run=False, check_feedback_source=True), state)
    assert result['status'] == 'dry_run'
    assert result['feedback_source_check']['players'] == 25
    assert result['feedback_source_check']['official_scores_checked'] == 18
    assert not state['models'] and not state['player_models'] and not state['pending']


def test_unavailable_feedback_does_not_block_a_verified_lineup(monkeypatch):
    from argparse import Namespace
    bot, league, cfg = orchestrated_league(monkeypatch)
    def fail(*_):
        raise ExtractionError('scoring settings missing')
    monkeypatch.setattr(bot, 'capture_settings', fail)
    monkeypatch.setattr(bot, 'apply_lineup', lambda *a, **kw: {})
    state = empty_state()
    result = bot.handle_league(None, cfg, league.url, Namespace(debug_dir=None, dry_run=False), state)
    assert result['status'] == 'saved'
    assert 'scoring settings missing' in result['feedback_notes'][0]
    assert state['pending'] == {}
