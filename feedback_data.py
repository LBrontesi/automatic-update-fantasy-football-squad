"""Read completed, individual results from Fantacalcio's own data sources."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from functools import lru_cache
from html.parser import HTMLParser
import json
import math
import re
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from selenium.webdriver.support.ui import WebDriverWait
from selenium.common.exceptions import WebDriverException, TimeoutException

from player_data import ExtractionError, WAIT_TIMEOUT, _performance_messages

SOURCES = {1: 'fantacalcio', 2: 'italia', 3: 'statistico'}
BONUSES = {
    'scoredGoals': 'bmgs', 'concededGoals': 'bmgc', 'scoredPenalties': 'bmpsc',
    'missedPenalties': 'bmpns', 'savedPenalties': 'bmpsa', 'yellowCards': 'bmyc',
    'redCards': 'bmrc', 'goalContributions': 'bmcg', 'softAssists': 'bmasf',
    'assists': 'bmass', 'goldAssists': 'bmasg', 'ownGoals': 'bmog',
    'decisiveGoals': 'bmdg', 'equalisingGoals': 'bmeg', 'cleanSheets': 'bmcsh',
    'manOfTheMatch': 'motm',
}


def number(value):
    try:
        result = float(str(value).replace(',', '.'))
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def captured_json(driver, messages, predicate):
    """Read only responses made by the current league page; never persist headers."""
    results = []
    for message in messages:
        if message.get('method') != 'Network.responseReceived':
            continue
        params = message.get('params', {})
        response = params.get('response', {})
        parsed = urlparse(response.get('url', ''))
        if parsed.hostname != 'apileague.fantacalcio.it' or response.get('status') != 200:
            continue
        if not predicate(parsed.path):
            continue
        try:
            body = driver.execute_cdp_cmd('Network.getResponseBody', {'requestId': params['requestId']})
            payload = json.loads(body['body'])
            if isinstance(payload, dict) and isinstance(payload.get('data'), (dict, list)):
                payload = payload['data']
            results.append(payload)
        except (KeyError, ValueError, WebDriverException):
            continue
    return results


def capture_settings(driver):
    messages = _performance_messages(driver)
    statuses = captured_json(driver, messages, lambda path: path.endswith('/league/status'))
    settings = captured_json(driver, messages, lambda path: path.endswith('/league/settings/calculate'))
    if not statuses or not settings:
        raise ExtractionError('Current league season/scoring settings were not observed')
    status, calculation = statuses[-1], settings[-1]
    source = int(calculation.get('sourcev') or 1)
    if source not in SOURCES or not isinstance(calculation.get('bnMls'), dict):
        raise ExtractionError('Unsupported league rating source or missing bonus rules')
    if not status.get('sId'):
        raise ExtractionError('League season ID is missing')
    return {'season_id': status['sId'], 'source': source, 'bonuses': calculation['bnMls']}


class HistoryParser(HTMLParser):
    """Parse the labelled matchday rows, including explicit no-vote appearances."""
    def __init__(self):
        super().__init__()
        self.in_table = False
        self.row = None
        self.read_week = False
        self.rows = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = attrs.get('class', '').split()
        if tag == 'table' and 'player-summary-table' in classes:
            self.in_table = True
        if not self.in_table:
            return
        if tag == 'tr':
            self.row = {'events': {}, 'week_text': ''}
        if self.row is None:
            return
        if 'matchweek' in classes:
            self.read_week = True
        if tag == 'a' and 'match' in classes:
            match = re.search(r'/calendario/(\d+)/(\d{4}-\d{2})/', attrs.get('href', ''))
            if match:
                self.row.update(week=int(match[1]), season=match[2].replace('-', '/'))
            self.row['finished'] = 'match-status-4' in classes
        if 'grade' in classes:
            value = number(attrs.get('data-value'))
            self.row['grade'] = value if value is not None and 0 <= value <= 10 else None
        if 'fanta-grade' in classes:
            self.row['editorial_score'] = number(attrs.get('data-value'))
        if tag == 'figure' and attrs.get('data-key') in BONUSES:
            key = attrs['data-key']
            value = number(attrs.get('data-value'))
            if value is not None:
                self.row['events'][key] = value

    def handle_data(self, data):
        if self.read_week and self.row is not None:
            self.row['week_text'] += data

    def handle_endtag(self, tag):
        if tag == 'span':
            self.read_week = False
        if tag == 'tr' and self.row is not None:
            row, self.row = self.row, None
            if 'week' in row and row['week_text'].strip() == str(row['week']):
                self.rows[row['week']] = row
        if tag == 'table':
            self.in_table = False


def parse_history(html, player_id):
    identity = re.search(r'playerId:\s*(\d+)', html)
    season = re.search(r'season:\s*"(\d{4}/\d{2})"', html)
    if not identity or int(identity[1]) != int(player_id) or not season:
        raise ExtractionError(f'Player history identity/season mismatch for ID {player_id}')
    parser = HistoryParser()
    parser.feed(html)
    return season[1], parser.rows


@lru_cache(maxsize=512)
def fetch_history(player_id, source):
    if source not in SOURCES or int(player_id) <= 0:
        raise ExtractionError('Unsupported player history request')
    url = f'https://www.fantacalcio.it/serie-a/squadre/squadra/calciatore/{int(player_id)}/widget/{SOURCES[source]}/classic'
    request = Request(url, headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'text/html'})
    with urlopen(request, timeout=15) as response:
        html = response.read(2_000_000).decode('utf-8')
    return parse_history(html, player_id)


def fantasy_score(row, role, bonuses):
    if not row.get('finished'):
        raise ExtractionError('Match is not finished; feedback deferred')
    grade = row.get('grade')
    if grade is None:
        if any(row['events'].values()):
            raise ExtractionError('Unrated player has events requiring league-specific default-vote rules')
        return 0.0
    total = grade
    for event, count in row['events'].items():
        weight = bonuses.get(BONUSES[event])
        if isinstance(weight, list):
            index = ('G', 'D', 'C', 'A').index(role)
            if len(weight) != 4:
                raise ExtractionError('Unsupported bonus role vector')
            weight = weight[index]
        if weight is not None:
            value = number(weight)
            if value is None:
                raise ExtractionError('Unsupported bonus value')
            total += value * count
    return round(total, 3)


def parse_calculated_match(payload, competition_id, round_number, selected_ids, owned_ids=None, calendar=None):
    if isinstance(payload, list):
        rounds = [r for r in calendar or [] if r.get('matchDay') == round_number and r.get('calculated') is True]
        if len(rounds) != 1 or not rounds[0].get('championshipMatchDay'):
            raise ExtractionError('Admin results lack a calculated round mapping')
        real_matchweek = int(rounds[0]['championshipMatchDay'])
        sides = payload
    elif isinstance(payload, dict):
        if (payload.get('cal') not in (True, 1) or int(payload.get('idcomp', -1)) != competition_id
                or int(payload.get('mday', -1)) != round_number or not payload.get('cmday')):
            raise ExtractionError('Result is uncalculated or belongs to a different round')
        real_matchweek = int(payload['cmday'])
        sides = (payload.get('home'), payload.get('away'))
    else:
        raise ExtractionError('Unsupported calculated match payload')
    candidates = []
    side_shapes = []
    for side in sides:
        if not isinstance(side, dict):
            continue
        entries = side.get('starts', []) + side.get('bench', [])
        side_shapes.append({'keys': sorted(side), 'starters': len(side.get('starts', [])),
                            'reserves': len(side.get('bench', []))})
        if len(side.get('starts', [])) == 11 and all(isinstance(entry, dict) for entry in entries):
            ids = [int(entry['pid']) for entry in entries]
            matches = set(ids) == set(selected_ids) if selected_ids is not None else len(set(ids) & set(owned_ids or [])) >= 3
            if len(ids) == len(set(ids)) and matches:
                candidates.append((side, entries))
    if len(candidates) != 1:
        raise ExtractionError(f'Calculated squad does not uniquely match the bot submission; sides={side_shapes}')
    side, entries = candidates[0]
    try:
        submitted_at = datetime.strptime(str(side['ldate']), '%Y%m%d%H%M%S%f').replace(tzinfo=timezone.utc)
    except (ValueError, KeyError) as exc:
        raise ExtractionError('Calculated lineup has no valid submission timestamp') from exc
    players = {}
    submitted_starters = {int(p['pid']) for p in side['starts']}
    for entry in entries:
        score = number(entry.get('cscr'))
        grade = number(entry.get('scr'))
        # 55/56/100 are the site's no-grade/hidden sentinels, never points.
        if grade in (55, 56, 100) or grade is None:
            score = 0.0 if score in (None, 55, 56, 100) else score
        if score is None or not -20 <= score <= 50:
            raise ExtractionError('Calculated player score is unavailable')
        players[int(entry['pid'])] = {
            'score': score, 'used_substitute': 'e' in str(entry.get('ptype', '')).lower(),
        }
        if 'e' in str(entry.get('ptype', '')).lower():
            submitted_starters.discard(int(entry['pid']))
        if 'u' in str(entry.get('ptype', '')).lower():
            submitted_starters.add(int(entry['pid']))
    return {'real_matchweek': real_matchweek, 'submitted_at': submitted_at, 'players': players,
            'starter_ids': [int(p['pid']) for p in side['starts']],
            'bench_ids': [int(p['pid']) for p in side['bench']],
            'submitted_starter_ids': sorted(submitted_starters)}


def result_round_mapping(path, competition_id, round_number, calculated_through):
    match = re.search(r'/teamLineup/(\d+)/(\d+)/(\d+)(?:/|$)', path, re.I)
    if (not match or int(match[1]) != competition_id or int(match[2]) != round_number
            or calculated_through is None or not 1 <= round_number <= calculated_through
            or not 1 <= int(match[3]) <= 38):
        return None
    return [{'matchDay': round_number, 'championshipMatchDay': int(match[3]), 'calculated': True}]


def read_calculated_match(driver, url, round_number, selected_ids, owned_ids=None, calculated_through=None):
    from lineup_editor import competition_base_url
    competition_id = int(competition_base_url(url).rsplit('/', 1)[1])
    _performance_messages(driver)
    driver.execute_cdp_cmd('Network.enable', {'maxTotalBufferSize': 30_000_000, 'maxResourceBufferSize': 10_000_000})
    driver.execute_cdp_cmd('Network.setCacheDisabled', {'cacheDisabled': True})
    driver.get(f'{competition_base_url(url)}/round/{round_number}')
    messages = []
    errors = []
    shapes = []
    observed_paths = set()
    request_mapping = None
    def ready(d):
        nonlocal request_mapping
        messages.extend(_performance_messages(d))
        for message in messages:
            if message.get('method') == 'Network.responseReceived':
                response = message.get('params', {}).get('response', {})
                parsed = urlparse(response.get('url', ''))
                if parsed.hostname == 'apileague.fantacalcio.it' and any(s in parsed.path.lower() for s in ('lineup', 'calendar')):
                    route = re.search(r'/teamLineup/\d+/\d+/\d+', parsed.path, re.I)
                    observed_paths.add(route[0] if route else re.sub(r'\d+', '#', parsed.path))
                    mapping = result_round_mapping(parsed.path, competition_id, round_number, calculated_through)
                    if mapping:
                        request_mapping = mapping
        calendars = captured_json(d, messages, lambda path:
            path.endswith(f'/league/competition/calendar/{competition_id}'))
        payloads = captured_json(d, messages, lambda path:
            re.search(rf'/teamLineup/{competition_id}/{round_number}/', path, re.I) is not None)
        for payload in payloads:
            shape = ({'keys': sorted(payload), 'round': payload.get('mday'), 'real_round': payload.get('cmday'),
                      'calculated': payload.get('cal')} if isinstance(payload, dict) else type(payload).__name__)
            if shape not in shapes:
                shapes.append(shape)
            try:
                return parse_calculated_match(payload, competition_id, round_number, selected_ids, owned_ids,
                                              request_mapping or (calendars[-1] if calendars else None))
            except (ExtractionError, ValueError, TypeError, KeyError) as exc:
                if str(exc) not in errors:
                    errors.append(str(exc))
                continue
        return False
    try:
        return WebDriverWait(driver, WAIT_TIMEOUT).until(ready)
    except TimeoutException as exc:
        raise ExtractionError(f'No matching calculated result; paths={sorted(observed_paths)}; response shapes={shapes}; errors={errors}') from exc
    finally:
        driver.execute_cdp_cmd('Network.setCacheDisabled', {'cacheDisabled': False})


def collect_roster_results(pending, match, history_fetcher=fetch_history):
    context = pending['context']
    def read(item):
        player_id, player = item
        season, rows = history_fetcher(int(player_id), context['source'])
        if season != context['season']:
            raise ExtractionError('Player history season differs from the saved decision')
        row = rows.get(match['real_matchweek'])
        if not row or row.get('season') != season:
            raise ExtractionError(f'No matchday history for player ID {player_id}')
        score = fantasy_score(row, player['role'], context['bonuses'])
        official = match['players'].get(int(player_id))
        if official and abs(score - official['score']) > .01:
            raise ExtractionError(f'League/history score mismatch for player ID {player_id}')
        return player_id, {'score': score, 'goals': int(row['events'].get('scoredGoals', 0)
            + row['events'].get('scoredPenalties', 0)),
            'used_substitute': bool(official and official['used_substitute']), 'rated': row.get('grade') is not None}
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(read, item) for item in pending['roster'].items()]
        try:
            return dict(future.result() for future in as_completed(futures))
        except Exception:
            for future in futures:
                future.cancel()
            raise
