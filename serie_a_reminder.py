"""Telegram reminders from Lega Serie A's public calendar; no fantasy account access."""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

ROME = ZoneInfo('Europe/Rome')
ROOT = Path(__file__).resolve().parent
STATE_PATH = ROOT / 'data/telegram_reminder_state.json'
LOCAL_ENV = ROOT / 'telegram.local.env'
CALENDAR_URL = 'https://www.legaseriea.it/serie-a/calendario-risultati'
SDP_BASE = 'https://api-sdp.legaseriea.it/v1/serie-a/football/seasons/'
COMPETITION_ID = 'serie-a::Football_Competition::ec93b94f74294dc98ab5bcfd67fc0d88'
REPOSITORY = 'LBrontesi/automatic-update-fantasy-football-squad'
LEAGUE_LINKS = (
    ('RusticaTorSapienza', 'https://leghe.fantacalcio.it/rusticatorsapienza/view/competition/532351/lineup'),
    ('IlprimoveroFanta', 'https://leghe.fantacalcio.it/ilprimoverofanta/view/competition/727232/lineup'),
)


def utc(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('Fixture timestamps must include a timezone')
    return parsed.astimezone(timezone.utc)


def walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def current_season(html):
    """Resolve the current Serie A season from the official calendar's page data."""
    decoder = json.JSONDecoder()
    chunks = []
    for marker in re.finditer(r'self\.__next_f\.push\(', html):
        try:
            value, _ = decoder.raw_decode(html[marker.end():])
        except ValueError:
            continue
        if isinstance(value, list) and len(value) == 2 and value[0] == 1 and isinstance(value[1], str):
            chunks.append(value[1])
    records = []
    for line in ''.join(chunks).splitlines():
        try:
            records.append(json.loads(line.split(':', 1)[1]))
        except (ValueError, IndexError):
            continue
    seasons = set()
    for node in walk(records):
        fields = node.get('fields')
        if not isinstance(fields, dict) or fields.get('competitionId') != COMPETITION_ID:
            continue
        for tag in node.get('tags', []):
            extra = tag.get('extraData', {})
            season_id = extra.get('seasonId', '')
            if extra.get('isCurrent') is True and re.fullmatch(r'serie-a::Football_Season::[a-f0-9]{32}', season_id):
                seasons.add((season_id, tag['title']))
    if len(seasons) != 1:
        raise ValueError('The official calendar did not identify one current Serie A season')
    return seasons.pop()


def public_get(url):
    request = Request(url, headers={'User-Agent': 'serie-a-personal-reminder/1.0'})
    for attempt in range(3):
        try:
            with urlopen(request, timeout=20) as response:
                return response.read(5_000_000).decode('utf-8')
        except HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise
        except (URLError, TimeoutError):
            if attempt == 2:
                raise
        time.sleep(2 ** attempt)


def plan_from_matches(payload, season_id, season_name, now):
    """Return the next unstarted round; never treat Saturday as a new Friday round."""
    competition = payload.get('competition', {})
    if (competition.get('competitionId') != COMPETITION_ID
            or competition.get('seasonId') != season_id
            or competition.get('seasonName') != season_name):
        raise ValueError('Fixture competition/season does not match the official Serie A calendar')
    rounds = defaultdict(list)
    seen = set()
    for match in payload.get('matches', []):
        day = match['matchSet']
        number = re.fullmatch(r'Matchday (\d+)', day['name'])
        if (not number or not 1 <= int(number[1]) <= 38
                or day['seasonId'] != season_id or day['competitionId'] != COMPETITION_ID
                or match['seasonId'] != season_id or match['matchId'] in seen):
            raise ValueError('Invalid or duplicate Serie A fixture')
        seen.add(match['matchId'])
        rounds[int(number[1])].append(match)
    if not rounds:
        raise ValueError('The official fixture calendar is empty')
    for number, matches in sorted(rounds.items()):
        # Even a postponed opening fixture must not create a second reminder
        # after another match in the same numbered round has already started.
        if any(m['status'].upper() in ('FINISHED', 'LIVE', 'SUSPENDED', 'ABANDONED')
               or m.get('isUnknownKickOffTime') is False and m.get('matchDateUtc')
               and utc(m['matchDateUtc']) <= now and m['status'].upper() not in ('POSTPONED', 'CANCELED')
               for m in matches):
            continue
        if len(matches) != 10 or len({m[side]['teamId'] for m in matches for side in ('home', 'away')}) != 20:
            raise ValueError(f'Giornata {number} has incomplete or conflicting fixtures')
        active = [m for m in matches if m['status'].upper() != 'CANCELED']
        if not active:
            continue
        # Do not skip an uncertain earlier round and guess a later round's deadline.
        if any(m.get('isUnknownKickOffTime') is not False or not m.get('matchDateUtc')
               or m['status'].upper() not in ('UPCOMING', 'LINEUP') for m in active):
            return None
        first = min(utc(m['matchDateUtc']) for m in active)
        opening = sorted(m['home']['shortName'] + ' – ' + m['away']['shortName']
                         for m in active if utc(m['matchDateUtc']) == first)
        return {'key': season_id + ':' + str(number), 'season': season_name,
                'round': number, 'kickoff': first.isoformat(),
                'send_at': (first - timedelta(hours=2)).isoformat(), 'matches': opening,
                'source': CALENDAR_URL}
    return None


def fetch_plan(now):
    season_id, name = current_season(public_get(CALENDAR_URL))
    payload = json.loads(public_get(SDP_BASE + quote(season_id, safe='') + '/matches?locale=it-IT'))
    return plan_from_matches(payload, season_id, name, now)


def message_text(plan):
    kickoff = utc(plan['kickoff']).astimezone(ROME)
    lines = [f"⚽ Promemoria formazione — Serie A, giornata {plan['round']}", '',
             f"Prima partita: {' / '.join(plan['matches'])}",
             f"Calcio d'inizio: {kickoff:%d/%m/%Y alle %H:%M} (ora italiana).", '',
             'Ricordati di inserire e salvare le formazioni in entrambe le leghe:']
    for name, url in LEAGUE_LINKS:
        lines += [name + ': ' + url]
    return '\n'.join(lines)


class TelegramRejected(RuntimeError):
    """An explicit API rejection confirms that this request did not send a message."""
    def __init__(self, method, code, retry_after=0):
        self.code = code
        try:
            retry_after = int(retry_after)
        except (ValueError, TypeError):
            retry_after = 60
        self.retry_after = max(1, retry_after) if code == 429 else 0
        super().__init__(f'Telegram {method} rejected the request (HTTP {code})')


def telegram_api(token, method, data=None):
    """Never include the token-bearing request URL in errors or logs."""
    request = Request(f'https://api.telegram.org/bot{token}/{method}',
                      data=json.dumps(data or {}).encode(),
                      headers={'Content-Type': 'application/json'}, method='POST')
    try:
        with urlopen(request, timeout=20) as response:
            result = json.loads(response.read(2_000_000))
    except HTTPError as exc:
        retry_after = 60
        if exc.code == 429:
            try:
                retry_after = int(json.loads(exc.read()).get('parameters', {}).get('retry_after', 60))
            except (ValueError, TypeError):
                pass
        if 400 <= exc.code < 500:
            raise TelegramRejected(method, exc.code, retry_after) from None
        raise RuntimeError(f'Telegram {method} failed (HTTP {exc.code}); delivery may be unconfirmed') from None
    except (URLError, TimeoutError, ValueError):
        raise RuntimeError(f'Telegram {method} failed; delivery may be unconfirmed') from None
    if result.get('ok') is not True:
        code = result.get('error_code')
        if isinstance(code, int) and 400 <= code < 500:
            retry_after = result.get('parameters', {}).get('retry_after', 60)
            raise TelegramRejected(method, code, retry_after)
        raise RuntimeError(f'Telegram {method} failed; delivery may be unconfirmed')
    return result['result']


def send_message(token, chat_id, text):
    if not str(chat_id).isdigit() or int(chat_id) <= 0:
        raise ValueError('Only a paired personal Telegram chat can receive this reminder')
    result = telegram_api(token, 'sendMessage', {'chat_id': int(chat_id), 'text': text,
                           'link_preview_options': {'is_disabled': True}})
    if result.get('chat', {}).get('type') != 'private' or result['chat']['id'] != int(chat_id):
        raise RuntimeError('Telegram returned an unexpected recipient')
    return result['message_id']


def paired_chat(updates, code):
    ids = set()
    for update in updates:
        msg = update.get('message', {})
        chat, sender = msg.get('chat', {}), msg.get('from', {})
        if (msg.get('text') == '/start ' + code and chat.get('type') == 'private'
                and sender.get('id') == chat.get('id') and not sender.get('is_bot')):
            ids.add(chat['id'])
    if len(ids) != 1:
        raise ValueError('Send the exact pairing /start message to your new bot from your personal Telegram chat')
    return str(ids.pop())


def empty_state():
    return {'version': 1, 'plan': None, 'checked_at': None, 'deliveries': {}}


def load_state(path=STATE_PATH):
    if not path.exists():
        return empty_state()
    state = json.loads(path.read_text())
    if state.get('version') != 1 or not isinstance(state.get('deliveries'), dict):
        raise ValueError('Unsupported reminder state; refusing to risk duplicate messages')
    return state


def save_state(state, path=STATE_PATH, git=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(state, indent=2, sort_keys=True) + '\n')
    temp.replace(path)
    if git:
        identity = ['-c', 'user.name=github-actions[bot]', '-c',
                    'user.email=41898282+github-actions[bot]@users.noreply.github.com']
        relative = str(path.relative_to(ROOT))
        changed = subprocess.run(['git', 'diff', '--quiet', '--', relative], cwd=ROOT)
        if changed.returncode not in (0, 1):
            raise RuntimeError('Could not inspect reminder persistence')
        if changed.returncode == 1:
            subprocess.run(['git', 'add', '--', relative], cwd=ROOT, check=True)
            subprocess.run(['git', *identity,
                            'commit', '--only', '-m', 'chore: persist Telegram reminder state', '--', relative],
                           cwd=ROOT, check=True, stdout=subprocess.DEVNULL)
            pushed = subprocess.run(['git', 'push', 'origin', 'HEAD:main'], cwd=ROOT,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            if pushed.returncode:
                # A code/documentation push while this runner waited must not
                # lose the reminder. Rebase only this job's state commit. A
                # conflicting reminder reservation still fails before delivery.
                subprocess.run(['git', 'fetch', 'origin', 'main'], cwd=ROOT, check=True,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                rebased = subprocess.run(['git', *identity, 'rebase', 'origin/main'], cwd=ROOT,
                                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                if rebased.returncode:
                    subprocess.run(['git', 'rebase', '--abort'], cwd=ROOT,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    raise RuntimeError('Reminder state changed remotely; delivery stopped to avoid a duplicate')
                subprocess.run(['git', 'push', 'origin', 'HEAD:main'], cwd=ROOT, check=True,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def run_tick(state, now, persist, sender, fetcher=fetch_plan):
    """Refresh daily, recheck near kickoff, and reserve durably before delivery."""
    now = now.astimezone(timezone.utc)
    checked = utc(state['checked_at']) if state.get('checked_at') else None
    plan = state.get('plan')
    # Recheck uncertain calendars and the entire opening day, so an earlier
    # kickoff or newly confirmed time cannot remain hidden in a daily cache.
    near = not plan or utc(plan['kickoff']).astimezone(ROME).date() <= now.astimezone(ROME).date()
    max_age = timedelta(minutes=30) if near else timedelta(hours=6)
    refreshed = (checked is None or checked.astimezone(ROME).date() != now.astimezone(ROME).date()
                 or now - checked >= max_age or checked > now + timedelta(minutes=5))
    if refreshed:
        state.update(plan=fetcher(now), checked_at=now.isoformat())
        persist(state)
        plan = state['plan']
    if plan is None:
        return 'No confirmed upcoming giornata; no message sent'
    previous = state['deliveries'].get(plan['key'])
    if previous:
        status = previous.get('status')
        if status == 'sent':
            return 'This giornata already has a delivery record; no duplicate sent'
        if status == 'retryable':
            if now < utc(previous['retry_at']):
                return 'Telegram rate limit: waiting until the retry time; no message sent'
        else:
            raise RuntimeError(f'Previous Telegram delivery is {status}; inspect the chat before retrying')
    if not utc(plan['send_at']) <= now < utc(plan['kickoff']):
        return f"Next reminder: giornata {plan['round']}, {utc(plan['send_at']).astimezone(ROME).isoformat()}"
    if not refreshed:
        # A daily cache must not send against an outdated kickoff.
        state.update(plan=fetcher(now), checked_at=now.isoformat())
        persist(state)
        plan = state['plan']
        if not plan or not utc(plan['send_at']) <= now < utc(plan['kickoff']):
            return 'Calendar changed; no reminder is due now'
        previous = state['deliveries'].get(plan['key'])
        if previous and previous.get('status') == 'sent':
            return 'This giornata already has a delivery record; no duplicate sent'
        if previous and previous.get('status') != 'retryable':
            raise RuntimeError('Calendar changed to a round with an existing delivery record')
        if previous and now < utc(previous['retry_at']):
            return 'Telegram rate limit: waiting until the retry time; no message sent'
    record = {'status': 'claimed', 'claimed_at': now.isoformat(), 'send_at': plan['send_at']}
    state['deliveries'][plan['key']] = record
    persist(state)  # If this fails, Telegram is never called.
    # A timeout/crash can leave this claimed. Do not retry an uncertain delivery
    # automatically: sendMessage has no idempotency key.
    try:
        message_id = sender(message_text(plan))
    except TelegramRejected as exc:
        record.update(status='retryable' if exc.code == 429 else 'rejected', error_code=exc.code)
        if exc.code == 429:
            record['retry_at'] = (now + timedelta(seconds=exc.retry_after)).isoformat()
        persist(state)
        raise
    except Exception:
        record['status'] = 'unconfirmed'
        persist(state)
        raise RuntimeError('Telegram delivery is unconfirmed; inspect the chat before retrying') from None
    record.update(status='sent', message_id=message_id, sent_at=now.isoformat())
    persist(state)
    return f"Telegram reminder sent for giornata {plan['round']}"


def scheduled_tick(state, persist, sender, wait_minutes=0, fetcher=fetch_plan,
                   clock=lambda: datetime.now(timezone.utc), pause=time.sleep):
    """An early cloud run may wait briefly for the exact target, then recheck it."""
    if not 0 <= wait_minutes <= 20:
        raise ValueError('The maximum wait must be between zero and 20 minutes')
    result = run_tick(state, clock(), persist, sender, fetcher)
    plan = state.get('plan')
    if not plan or plan['key'] in state['deliveries']:
        return result
    target = utc(plan['send_at'])
    remaining = (target-clock()).total_seconds()
    if not 0 < remaining <= wait_minutes * 60:
        return result
    print(f'Waiting on the cloud runner until {target.astimezone(ROME).isoformat()}', flush=True)
    while (remaining := (target-clock()).total_seconds()) > 0:
        pause(min(30, remaining))
    return run_tick(state, clock(), persist, sender, fetcher)


def load_credentials():
    from dotenv import load_dotenv
    load_dotenv(LOCAL_ENV)
    token = os.environ.get('TELEGRAM_BOT_TOKEN', '').strip()
    if not re.fullmatch(r'\d+:[A-Za-z0-9_-]{20,}', token):
        raise ValueError('Set TELEGRAM_BOT_TOKEN in telegram.local.env or repository secrets')
    return token, os.environ.get('TELEGRAM_CHAT_ID', '').strip()


def write_status_summary(error=None):
    """Make normal scheduled runs and delivery problems visible in Actions."""
    summary = os.environ.get('GITHUB_STEP_SUMMARY')
    if not summary:
        return
    lines = ['## Telegram formation reminder', '']
    if error:
        lines += ['**Run failed. Review the log before relying on this reminder.**', '']
    try:
        state = load_state()
        plan = state.get('plan')
        if plan:
            lines += [f"Next giornata: **{plan['round']}** ({plan['season']}).", '',
                      f"Kickoff: **{utc(plan['kickoff']).astimezone(ROME).isoformat()}**.", '',
                      f"Reminder target: **{utc(plan['send_at']).astimezone(ROME).isoformat()}**.", '']
        else:
            lines += ['No confirmed upcoming giornata is available; the calendar will be checked again.', '']
        unresolved = [key for key, item in state['deliveries'].items() if item.get('status') != 'sent']
        if unresolved:
            lines += [f'**{len(unresolved)} unresolved delivery record(s) require review.**', '']
        lines += ['Calendar last checked: ' + str(state.get('checked_at')), '']
    except (OSError, ValueError, KeyError, TypeError):
        lines += ['Reminder state could not be read.', '']
    with open(summary, 'a') as handle:
        handle.write('\n'.join(lines) + '\n')


def pair(github=False):
    token, _ = load_credentials()
    bot = telegram_api(token, 'getMe')
    if bot.get('is_bot') is not True:
        raise ValueError('The supplied token does not belong to a Telegram bot')
    if telegram_api(token, 'getWebhookInfo').get('url'):
        raise ValueError('This bot already has a webhook; create a dedicated reminder bot')
    code = (ROOT / '.telegram-pairing-code').read_text().strip()
    updates = telegram_api(token, 'getUpdates', {'allowed_updates': ['message'], 'limit': 100})
    chat_id = paired_chat(updates, code)
    chat = telegram_api(token, 'getChat', {'chat_id': int(chat_id)})
    if chat.get('type') != 'private' or chat.get('id') != int(chat_id):
        raise ValueError('Pairing did not resolve your personal chat')
    content = LOCAL_ENV.read_text()
    content = re.sub(r'^TELEGRAM_CHAT_ID=.*\n?', '', content, flags=re.M)
    LOCAL_ENV.write_text(content.rstrip() + '\nTELEGRAM_CHAT_ID=' + chat_id + '\n')
    LOCAL_ENV.chmod(0o600)
    if github:
        for name, value in [('TELEGRAM_BOT_TOKEN', token), ('TELEGRAM_CHAT_ID', chat_id)]:
            subprocess.run(['gh', 'secret', 'set', name, '--repo', REPOSITORY], input=value.encode(), check=True)
    send_message(token, chat_id, '✅ Telegram collegato per i promemoria delle formazioni. '
                 'Il promemoria sarà previsto 2 ore prima della prima partita di ogni giornata, '
                 'una volta attivata la pianificazione.')
    print('Telegram bot paired; connection test sent. No token or chat ID was logged.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['check', 'tick', 'pair', 'test'])
    parser.add_argument('--github', action='store_true', help='Store paired credentials as GitHub secrets')
    parser.add_argument('--persist-git', action='store_true', help='Persist only reminder state to main before sending')
    parser.add_argument('--wait-minutes', type=int, default=0,
                        help='Wait up to 20 minutes on an early cloud run for the exact reminder time')
    args = parser.parse_args()
    try:
        if args.command == 'check':
            now = datetime.now(timezone.utc)
            plan = fetch_plan(now)
            print(json.dumps(plan, indent=2, ensure_ascii=False))
        elif args.command == 'pair':
            pair(args.github)
        else:
            token, chat_id = load_credentials()
            if args.command == 'test':
                send_message(token, chat_id, '✅ Test del promemoria formazioni: Telegram funziona.')
                print('Test message sent')
            else:
                STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
                with (STATE_PATH.with_suffix('.lock')).open('w') as lock:
                    fcntl.flock(lock, fcntl.LOCK_EX)
                    state = load_state()
                    print(scheduled_tick(state, lambda s: save_state(s, git=args.persist_git),
                                         lambda text: send_message(token, chat_id, text),
                                         wait_minutes=args.wait_minutes))
    except (ValueError, RuntimeError, OSError, KeyError, subprocess.CalledProcessError) as exc:
        # Provider/credential errors are sanitized inside telegram_api.
        print(f'Reminder error: {exc}')
        if args.command == 'tick':
            write_status_summary(error=True)
        return 1
    if args.command == 'tick':
        write_status_summary()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
