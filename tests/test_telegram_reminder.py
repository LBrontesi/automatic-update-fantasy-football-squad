from copy import deepcopy
from datetime import timedelta
from urllib.error import HTTPError
import json
from io import BytesIO
import subprocess

import pytest

import serie_a_reminder as reminder

SEASON = 'serie-a::Football_Season::' + 'a' * 32


def calendar_payload(first='2026-11-20T19:45:00Z', number=12):
    matches = []
    for index in range(10):
        when = reminder.utc(first) + timedelta(hours=0 if index == 0 else 24 + index)
        matches.append({
            'matchId': str(index), 'seasonId': SEASON, 'matchDateUtc': when.isoformat(),
            'isUnknownKickOffTime': False, 'status': 'UPCOMING',
            'home': {'teamId': str(index*2), 'shortName': 'Home ' + str(index)},
            'away': {'teamId': str(index*2+1), 'shortName': 'Away ' + str(index)},
            'matchSet': {'name': 'Matchday ' + str(number), 'seasonId': SEASON,
                         'competitionId': reminder.COMPETITION_ID},
        })
    return {'competition': {'competitionId': reminder.COMPETITION_ID,
                           'seasonId': SEASON, 'seasonName': '2026/2027'}, 'matches': matches}


def make_plan(payload=None, now='2026-11-20T08:00:00Z'):
    return reminder.plan_from_matches(payload or calendar_payload(), SEASON, '2026/2027', reminder.utc(now))


def test_reads_current_serie_a_season_without_confusing_coppa_italia():
    def competition(id, season):
        return {'fields': {'competitionId': id}, 'tags': [{'title': '2026/2027',
                'extraData': {'isCurrent': True, 'seasonId': season}}]}
    record = [competition('different-cup', 'wrong'), competition(reminder.COMPETITION_ID, SEASON)]
    flight = 'a:' + json.dumps(record) + '\n'
    html = '<script>self.__next_f.push(' + json.dumps([1, flight]) + ')</script>'
    assert reminder.current_season(html) == (SEASON, '2026/2027')
    with pytest.raises(ValueError):
        reminder.current_season('<html>Unavailable</html>')


def test_friday_opening_has_one_deadline_for_whole_round():
    payload = calendar_payload()
    plan = make_plan(payload)
    assert plan['send_at'] == '2026-11-20T17:45:00+00:00'
    assert '18:45' in reminder.utc(plan['send_at']).astimezone(reminder.ROME).isoformat()
    payload['matches'][0]['status'] = 'FINISHED'
    assert make_plan(payload, '2026-11-21T08:00:00Z') is None


def test_two_simultaneous_openers_still_have_one_round_key():
    payload = calendar_payload()
    payload['matches'][1]['matchDateUtc'] = payload['matches'][0]['matchDateUtc']
    plan = make_plan(payload)
    assert len(plan['matches']) == 2
    assert plan['key'] == SEASON + ':12'


@pytest.mark.parametrize('first,expected', [
    ('2026-09-15T16:30:00Z', '16:30'),  # Midweek round, summer time.
    ('2026-11-21T14:00:00Z', '13:00'),  # Saturday, winter time.
])
def test_two_hour_offset_uses_correct_italian_daylight_saving(first, expected):
    payload = calendar_payload(first)
    plan = make_plan(payload, (reminder.utc(first)-timedelta(days=1)).isoformat())
    assert reminder.utc(plan['send_at']).astimezone(reminder.ROME).strftime('%H:%M') == expected


@pytest.mark.parametrize('problem', ['wrong_league', 'wrong_season', 'incomplete', 'duplicate'])
def test_bad_fixture_data_cannot_generate_a_deadline(problem):
    payload = calendar_payload()
    if problem == 'wrong_league':
        payload['competition']['competitionId'] = 'not-serie-a'
    elif problem == 'wrong_season':
        payload['competition']['seasonId'] = 'another-season'
    elif problem == 'incomplete':
        payload['matches'].pop()
    else:
        payload['matches'][1]['matchId'] = payload['matches'][0]['matchId']
    with pytest.raises(ValueError):
        make_plan(payload)


@pytest.mark.parametrize('problem', ['unknown_time', 'postponed'])
def test_uncertain_opening_is_not_guessed(problem):
    payload = calendar_payload()
    if problem == 'unknown_time':
        payload['matches'][0]['isUnknownKickOffTime'] = True
    else:
        payload['matches'][0]['status'] = 'POSTPONED'
    assert make_plan(payload) is None


def test_daily_check_is_cached_but_result_is_rechecked_before_delivery():
    state, writes, sends, fetches = reminder.empty_state(), [], [], []
    def fetch(now):
        fetches.append(now)
        return make_plan(now=now.isoformat())
    persist = lambda s: writes.append(deepcopy(s))
    sender = lambda text: sends.append(text) or 72
    for when in ['08:00', '09:00', '17:00', '17:45', '17:50']:
        reminder.run_tick(state, reminder.utc(f'2026-11-20T{when}:00Z'), persist, sender, fetch)
    assert len(fetches) == 4
    assert len(sends) == 1
    assert writes[-2]['deliveries'][SEASON+':12']['status'] == 'claimed'
    assert writes[-1]['deliveries'][SEASON+':12']['status'] == 'sent'
    assert 'RusticaTorSapienza' in sends[0] and 'IlprimoveroFanta' in sends[0]


def test_changed_kickoff_is_recomputed_and_does_not_send_early():
    state = reminder.empty_state()
    state.update(plan=make_plan(), checked_at='2026-11-20T17:40:00Z')
    later = calendar_payload('2026-11-20T21:45:00Z')
    result = reminder.run_tick(state, reminder.utc('2026-11-20T17:45:00Z'), lambda s: None,
                               lambda text: pytest.fail('Sent before the changed deadline'),
                               lambda now: make_plan(later, now.isoformat()))
    assert 'Calendar changed' in result
    assert state['plan']['send_at'] == '2026-11-20T19:45:00+00:00'


def test_midnight_deadline_can_be_on_the_day_before_kickoff():
    plan = make_plan(calendar_payload('2026-11-20T00:30:00Z'), now='2026-11-19T08:00:00Z')
    assert reminder.utc(plan['send_at']).astimezone(reminder.ROME).day == 19


def test_persistence_failure_cannot_send_an_unreserved_message():
    state = reminder.empty_state()
    def persist(state):
        if state['deliveries']:
            raise RuntimeError('storage unavailable')
    with pytest.raises(RuntimeError, match='storage unavailable'):
        reminder.run_tick(state, reminder.utc('2026-11-20T17:45:00Z'), persist,
                          lambda text: pytest.fail('Delivery was not durably reserved'),
                          lambda now: make_plan(now=now.isoformat()))


def test_uncertain_delivery_is_not_automatically_sent_twice():
    state = reminder.empty_state()
    def sender(text):
        raise RuntimeError('response timed out')
    with pytest.raises(RuntimeError, match='unconfirmed'):
        reminder.run_tick(state, reminder.utc('2026-11-20T17:45:00Z'), lambda s: None,
                          sender, lambda now: make_plan(now=now.isoformat()))
    with pytest.raises(RuntimeError, match='inspect the chat'):
        reminder.run_tick(state, reminder.utc('2026-11-20T17:50:00Z'), lambda s: None,
                          lambda text: pytest.fail('Uncertain message was retried'))
    assert state['deliveries'][SEASON+':12']['status'] == 'unconfirmed'


def test_no_message_after_the_first_match_has_started():
    state = reminder.empty_state()
    state.update(plan=make_plan(), checked_at='2026-11-20T19:44:00Z')
    reminder.run_tick(state, reminder.utc('2026-11-20T19:46:00Z'), lambda s: None,
                      lambda text: pytest.fail('Too late to submit a squad'))


def test_early_cloud_runner_waits_until_exact_target_without_real_sleep():
    now = reminder.utc('2026-11-20T17:32:00Z')
    sent = []
    def pause(seconds):
        nonlocal now
        now += timedelta(seconds=seconds)
    result = reminder.scheduled_tick(reminder.empty_state(), lambda s: None,
        lambda text: sent.append(now) or 73, wait_minutes=20,
        fetcher=lambda when: make_plan(now=when.isoformat()), clock=lambda: now, pause=pause)
    assert 'sent' in result
    assert sent == [reminder.utc('2026-11-20T17:45:00Z')]


def test_cloud_runner_does_not_wait_for_a_deadline_days_away():
    reminder.scheduled_tick(reminder.empty_state(), lambda s: None,
        lambda text: pytest.fail('Sent days before deadline'), wait_minutes=20,
        fetcher=lambda when: make_plan(now=when.isoformat()),
        clock=lambda: reminder.utc('2026-11-18T08:00:00Z'),
        pause=lambda seconds: pytest.fail('Runner tried to wait days'))


def test_pairing_requires_exact_code_and_the_users_own_private_chat():
    def update(id=123, kind='private', text='/start expected', sender=123):
        return {'message': {'chat': {'id': id, 'type': kind},
                            'from': {'id': sender, 'is_bot': False}, 'text': text}}
    assert reminder.paired_chat([update(), update(text='/start someone_else')], 'expected') == '123'
    for updates in [[update(kind='group')], [update(sender=321)], [update(text='/start wrong')],
                    [update(), update(id=321, sender=321)]]:
        with pytest.raises(ValueError):
            reminder.paired_chat(updates, 'expected')


def test_telegram_transport_errors_do_not_expose_the_bot_token(monkeypatch):
    token = '123456:never-print-this-token'
    def fail(request, timeout):
        raise HTTPError(request.full_url, 401, 'unauthorized', {}, None)
    monkeypatch.setattr(reminder, 'urlopen', fail)
    with pytest.raises(RuntimeError) as error:
        reminder.telegram_api(token, 'getMe')
    assert token not in str(error.value)
    assert 'HTTP 401' in str(error.value)


def test_negative_group_ids_cannot_be_used_for_a_personal_reminder():
    with pytest.raises(ValueError, match='personal'):
        reminder.send_message('token', '-10012345', 'text')


def test_time_confirmed_later_on_the_same_day_is_not_stuck_in_an_empty_cache():
    state = reminder.empty_state()
    state['checked_at'] = '2026-11-20T08:00:00Z'
    reminder.run_tick(state, reminder.utc('2026-11-20T10:00:00Z'), lambda s: None,
        lambda text: pytest.fail('Reminder not due'), lambda now: make_plan(now=now.isoformat()))
    assert state['plan']['round'] == 12


def test_opening_moved_earlier_is_discovered_before_the_old_three_hour_window():
    state = reminder.empty_state()
    state.update(plan=make_plan(calendar_payload('2026-11-20T21:45:00Z')),
                 checked_at='2026-11-20T08:00:00Z')
    earlier = calendar_payload('2026-11-20T16:00:00Z')
    reminder.run_tick(state, reminder.utc('2026-11-20T11:00:00Z'), lambda s: None,
        lambda text: pytest.fail('Reminder not due'), lambda now: make_plan(earlier, now.isoformat()))
    assert state['plan']['send_at'] == '2026-11-20T14:00:00+00:00'


def test_unknown_placeholder_time_is_not_treated_as_an_already_started_round():
    payload = calendar_payload('2026-11-20T00:00:00Z')
    payload['matches'][0]['isUnknownKickOffTime'] = True
    other = calendar_payload('2026-11-27T19:45:00Z', number=13)
    for match in other['matches']:
        match['matchId'] = 'next-' + match['matchId']
    payload['matches'] += other['matches']
    assert make_plan(payload, '2026-11-20T08:00:00Z') is None


def test_missing_unknown_kickoff_defers_instead_of_crashing():
    payload = calendar_payload()
    payload['matches'][0].update(isUnknownKickOffTime=True, matchDateUtc=None)
    assert make_plan(payload) is None


def test_partial_historical_round_does_not_block_a_complete_upcoming_round():
    payload = calendar_payload()
    old = calendar_payload('2026-11-13T19:45:00Z', number=11)['matches'][:9]
    for match in old:
        match['matchId'] = 'old-' + match['matchId']
        match['status'] = 'FINISHED'
    payload['matches'] += old
    assert make_plan(payload)['round'] == 12


def test_explicit_telegram_rate_limit_is_retried_but_not_before_retry_after():
    state = reminder.empty_state()
    fetcher = lambda now: make_plan(now=now.isoformat())
    def reject(text):
        raise reminder.TelegramRejected('sendMessage', 429, 120)
    with pytest.raises(reminder.TelegramRejected):
        reminder.run_tick(state, reminder.utc('2026-11-20T17:45:00Z'), lambda s: None, reject, fetcher)
    assert state['deliveries'][SEASON+':12']['status'] == 'retryable'
    reminder.run_tick(state, reminder.utc('2026-11-20T17:46:00Z'), lambda s: None,
                      lambda text: pytest.fail('Retried before Telegram allowed it'), fetcher)
    sent = []
    reminder.run_tick(state, reminder.utc('2026-11-20T17:47:00Z'), lambda s: None,
                      lambda text: sent.append(text) or 77, fetcher)
    assert len(sent) == 1
    assert state['deliveries'][SEASON+':12']['status'] == 'sent'


def test_telegram_429_response_is_recognized_without_exposing_its_url(monkeypatch):
    def limited(request, timeout):
        raise HTTPError(request.full_url, 429, 'rate limited', {},
                        BytesIO(b'{"parameters":{"retry_after":90}}'))
    monkeypatch.setattr(reminder, 'urlopen', limited)
    with pytest.raises(reminder.TelegramRejected) as error:
        reminder.telegram_api('never-log-this-token', 'sendMessage')
    assert error.value.retry_after == 90
    assert 'never-log' not in str(error.value)


def test_permanent_rejection_stays_visible_and_is_not_retried():
    state = reminder.empty_state()
    def reject(text):
        raise reminder.TelegramRejected('sendMessage', 403)
    fetcher = lambda now: make_plan(now=now.isoformat())
    with pytest.raises(reminder.TelegramRejected):
        reminder.run_tick(state, reminder.utc('2026-11-20T17:45:00Z'), lambda s: None, reject, fetcher)
    with pytest.raises(RuntimeError, match='rejected'):
        reminder.run_tick(state, reminder.utc('2026-11-20T18:00:00Z'), lambda s: None,
                          lambda text: pytest.fail('A blocked bot was retried'), fetcher)


def test_actions_summary_shows_deadline_and_unresolved_delivery(tmp_path, monkeypatch):
    path = tmp_path / 'summary.md'
    state = reminder.empty_state()
    state['plan'] = make_plan()
    state['deliveries'][SEASON+':12'] = {'status': 'unconfirmed'}
    monkeypatch.setenv('GITHUB_STEP_SUMMARY', str(path))
    monkeypatch.setattr(reminder, 'load_state', lambda: state)
    reminder.write_status_summary(error=True)
    text = path.read_text()
    assert 'Run failed' in text
    assert '18:45:00+01:00' in text
    assert '1 unresolved delivery' in text


def test_temporary_public_calendar_error_is_retried(monkeypatch):
    attempts = []
    def get(request, timeout):
        attempts.append(1)
        if len(attempts) < 3:
            raise HTTPError(request.full_url, 503, 'unavailable', {}, None)
        return BytesIO(b'calendar')
    monkeypatch.setattr(reminder, 'urlopen', get)
    monkeypatch.setattr(reminder.time, 'sleep', lambda seconds: None)
    assert reminder.public_get(reminder.CALENDAR_URL) == 'calendar'
    assert len(attempts) == 3


def test_state_push_survives_an_unrelated_commit_during_the_wait(tmp_path, monkeypatch):
    def git(cwd, *args):
        return subprocess.run(['git', '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                               *args], cwd=cwd, check=True, capture_output=True, text=True).stdout
    origin, seed, runner, updater = [tmp_path / name for name in ['origin.git', 'seed', 'runner', 'updater']]
    git(tmp_path, 'init', '--bare', str(origin))
    git(tmp_path, 'init', '-b', 'main', str(seed))
    path = seed / 'data/state.json'
    reminder.save_state(reminder.empty_state(), path)
    git(seed, 'add', '.')
    git(seed, 'commit', '-m', 'initial state')
    git(seed, 'remote', 'add', 'origin', str(origin))
    git(seed, 'push', '-u', 'origin', 'main')
    git(origin, 'symbolic-ref', 'HEAD', 'refs/heads/main')
    git(tmp_path, 'clone', str(origin), str(runner))
    git(tmp_path, 'clone', str(origin), str(updater))
    (updater / 'notes.txt').write_text('unrelated code or docs update')
    git(updater, 'add', '.')
    git(updater, 'commit', '-m', 'concurrent update')
    git(updater, 'push')
    monkeypatch.setattr(reminder, 'ROOT', runner)
    state = reminder.empty_state()
    state['checked_at'] = '2026-11-20T08:00:00Z'
    reminder.save_state(state, runner / 'data/state.json', git=True)
    saved = json.loads(git(origin, 'show', 'main:data/state.json'))
    assert saved == state
    assert (runner / 'notes.txt').read_text() == 'unrelated code or docs update'


def test_conflicting_remote_reservation_cannot_be_overwritten(tmp_path, monkeypatch):
    def git(cwd, *args):
        return subprocess.run(['git', '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                               *args], cwd=cwd, check=True, capture_output=True, text=True).stdout
    origin, seed, runner, updater = [tmp_path / name for name in ['origin.git', 'seed', 'runner', 'updater']]
    git(tmp_path, 'init', '--bare', str(origin))
    git(tmp_path, 'init', '-b', 'main', str(seed))
    reminder.save_state(reminder.empty_state(), seed / 'data/state.json')
    git(seed, 'add', '.')
    git(seed, 'commit', '-m', 'initial state')
    git(seed, 'remote', 'add', 'origin', str(origin))
    git(seed, 'push', '-u', 'origin', 'main')
    git(origin, 'symbolic-ref', 'HEAD', 'refs/heads/main')
    git(tmp_path, 'clone', str(origin), str(runner))
    git(tmp_path, 'clone', str(origin), str(updater))
    remote = reminder.empty_state()
    remote['deliveries'][SEASON+':12'] = {'status': 'claimed', 'claimed_at': '2026-11-20T17:45:00Z'}
    reminder.save_state(remote, updater / 'data/state.json')
    git(updater, 'add', '.')
    git(updater, 'commit', '-m', 'reserve delivery')
    git(updater, 'push')
    local = reminder.empty_state()
    local['deliveries'][SEASON+':12'] = {'status': 'claimed', 'claimed_at': '2026-11-20T17:45:01Z'}
    monkeypatch.setattr(reminder, 'ROOT', runner)
    with pytest.raises(RuntimeError, match='avoid a duplicate'):
        reminder.save_state(local, runner / 'data/state.json', git=True)
    assert json.loads(git(origin, 'show', 'main:data/state.json')) == remote
