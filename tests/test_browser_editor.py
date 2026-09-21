"""Real Chrome checks against a local synthetic editor; no account access."""
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest
from selenium import webdriver

import lineup_editor as editor
from player_data import LineupUIError
from predictions import HistoricalSource
from squad_picker import pick_squad

pytestmark = pytest.mark.skipif(os.environ.get('FANTA_BROWSER_TESTS') != 'true', reason='Enable FANTA_BROWSER_TESTS=true for local Chrome contract tests')


@pytest.fixture(scope='module')
def browser():
    html = (Path(__file__).parent / 'fixtures' / 'editor.html').read_bytes()
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(html)
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    options = webdriver.ChromeOptions()
    for argument in ['--headless=new', '--no-sandbox', '--disable-dev-shm-usage', '--window-size=1100,900']:
        options.add_argument(argument)
    driver = None
    try:
        driver = webdriver.Chrome(options=options)
        yield driver, f'http://127.0.0.1:{server.server_port}'
    finally:
        if driver:
            driver.quit()
        server.shutdown()
        server.server_close()
        thread.join()


def selection(driver, url):
    league = editor.fetch_league_data(driver, url)
    scores = HistoricalSource().predict(league)
    return league, pick_squad(league.players, scores, 'auto', league.allowed_formations, league.bench_roles)


def test_two_leagues_use_different_rosters_and_labelled_metrics(browser):
    driver, base = browser
    first, _ = selection(driver, base+'/first')
    second, _ = selection(driver, base+'/second')
    assert len(first.players) == len(second.players) == 25
    assert {p.external_id for p in first.players}.isdisjoint(p.external_id for p in second.players)
    assert all(p.name.startswith('Second') for p in second.players)
    assert second.players[0].votes == [6]
    assert second.players[0].start_probability == .9


def test_xi_and_seven_reserves_save_without_captain_controls(browser):
    driver, base = browser
    _, picked = selection(driver, base+'/save')
    result = editor.apply_lineup(driver, picked)
    assert result['captains'] == {}
    assert len([name for name in result['slots'].values() if name]) == 18
    assert [n for m,n in result['slots'].items() if m.startswith('-1:')] == [p.name for p in picked.bench]
    driver.refresh()
    editor.verify_lineup(result, editor.read_lineup(driver))


def test_mid_edit_failure_discards_unsaved_draft(browser, monkeypatch):
    driver, base = browser
    _, picked = selection(driver, base+'/rollback')
    original = editor.read_lineup(driver)
    def fail(*args, **kwargs):
        raise LineupUIError('simulated UI failure')
    monkeypatch.setattr(editor, '_place_player', fail)
    with pytest.raises(LineupUIError, match='simulated UI failure'):
        editor.apply_lineup(driver, picked)
    assert editor.read_lineup(driver) == original


def test_foreign_league_players_rejected_before_clearing(browser):
    driver, base = browser
    _, first_pick = selection(driver, base+'/first')
    selection(driver, base+'/second')
    original = editor.read_lineup(driver)
    with pytest.raises(LineupUIError, match='outside the current league'):
        editor.apply_lineup(driver, first_pick)
    assert editor.read_lineup(driver) == original


def test_success_banner_does_not_hide_a_save_that_did_not_persist(browser, monkeypatch):
    driver, base = browser
    _, picked = selection(driver, base+'/ignored?ignore_save')
    monkeypatch.setattr(editor, 'WAIT_TIMEOUT', 2)
    with pytest.raises(LineupUIError, match='Reloaded lineup differs'):
        editor.apply_lineup(driver, picked)


@pytest.mark.parametrize('path,roles', [
    ('occupied', ['G', 'D', 'D', 'C', 'C', 'A', 'A']),
    ('occupied-free', ['G', '*', '*', '*', '*', '*', '*']),
])
def test_occupied_reserves_expose_rules_without_saving_or_guessing(browser, path, roles):
    driver, base = browser
    url = base+'/'+path
    driver.get(url)
    original = editor.read_lineup(driver)
    assert sum(bool(name) for name in original['slots'].values()) == 18
    league, picked = selection(driver, url)
    assert league.bench_roles == roles
    assert editor.read_lineup(driver) == original
    assert driver.execute_script('return localStorage.getItem(location.pathname)') is None
    saved = editor.apply_lineup(driver, picked)
    assert [pid for marker,pid in saved['slot_ids'].items() if marker.startswith('-1:')] == [p.external_id for p in picked.bench]


def test_display_name_changes_do_not_hide_successful_player_placement(browser):
    driver, base = browser
    _, picked = selection(driver, base+'/aliased')
    taylor = next(p for p in picked.starters if p.name == 'Taylor K.')
    result = editor.apply_lineup(driver, picked)
    marker = next(marker for marker,name in result['slots'].items() if name == 'K. Taylor')
    assert result['slot_ids'][marker] == taylor.external_id
    driver.refresh()
    editor.verify_lineup(result, editor.read_lineup(driver))


def test_wrong_reserve_roles_rejected_before_placing_any_players(browser, monkeypatch):
    driver, base = browser
    _, picked = selection(driver, base+'/invalid-bench')
    picked.bench.reverse()
    original = editor.read_lineup(driver)
    monkeypatch.setattr(editor, '_place_player', lambda *a, **kw: pytest.fail('invalid bench must fail before placement'))
    with pytest.raises(LineupUIError, match='slot constraints'):
        editor.apply_lineup(driver, picked)
    assert editor.read_lineup(driver) == original
