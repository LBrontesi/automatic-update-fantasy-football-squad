from datetime import datetime

from schedule_guard import Fixture, evaluate_schedule, parse_fixtures


CALENDAR_HTML = """
<ul class="matchweek">
  <li class="match">
    <div class="match-pill theme-light match-status-1">
      <div class="match-date">
        <meta itemprop="startDate" content="2026-09-07"/>
        <span class="hours">18:30</span>
      </div>
    </div>
  </li>
  <li class="match">
    <div class="match-pill theme-light match-status-1">
      <div class="match-date">
        <meta itemprop="startDate" content="2026-09-07"/>
        <span class="hours">20:45</span>
      </div>
    </div>
  </li>
</ul>
"""


def test_parse_fixtures_extracts_dates_and_kickoff_times() -> None:
    fixtures = parse_fixtures(CALENDAR_HTML)
    assert [fixture.kickoff.isoformat() for fixture in fixtures] == [
        "2026-09-07T18:30:00+02:00",
        "2026-09-07T20:45:00+02:00",
    ]


def test_schedule_runs_before_first_game() -> None:
    fixtures = parse_fixtures(CALENDAR_HTML)
    decision = evaluate_schedule(
        fixtures,
        now=datetime.fromisoformat("2026-09-07T17:00:00+02:00"),
    )
    assert decision.allowed is True


def test_schedule_runs_at_any_time_before_first_game() -> None:
    fixtures = parse_fixtures(CALENDAR_HTML)
    decision = evaluate_schedule(
        fixtures,
        now=datetime.fromisoformat("2026-09-07T17:30:00+02:00"),
    )
    assert decision.allowed is True


def test_schedule_runs_three_hours_before_first_game() -> None:
    fixtures = [Fixture(datetime.fromisoformat("2026-09-07T20:45:00+02:00"))]
    decision = evaluate_schedule(
        fixtures,
        now=datetime.fromisoformat("2026-09-07T18:00:00+02:00"),
    )
    assert decision.allowed is True


def test_schedule_skips_after_first_game() -> None:
    fixtures = parse_fixtures(CALENDAR_HTML)
    decision = evaluate_schedule(
        fixtures,
        now=datetime.fromisoformat("2026-09-07T19:00:00+02:00"),
    )
    assert decision.allowed is False


def test_schedule_runs_when_there_are_no_games_today() -> None:
    fixture = Fixture(datetime.fromisoformat("2026-09-08T18:30:00+02:00"))
    decision = evaluate_schedule(
        [fixture],
        now=datetime.fromisoformat("2026-09-07T18:00:00+02:00"),
    )
    assert decision.allowed is True


def test_schedule_runs_on_no_game_days() -> None:
    fixture = Fixture(datetime.fromisoformat("2026-09-08T18:30:00+02:00"))
    decision = evaluate_schedule(
        [fixture],
        now=datetime.fromisoformat("2026-09-07T03:00:00+02:00"),
    )
    assert decision.allowed is True
