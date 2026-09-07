"""Prevent lineup changes after the first Serie A kickoff of the day."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from html.parser import HTMLParser
import logging
import re
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

DEFAULT_SCHEDULE_URL = "https://www.fantacalcio.it/serie-a/calendario"
DEFAULT_TIMEZONE = "Europe/Rome"
DEFAULT_RUN_TIME = "18:00"
RUN_WINDOW_MINUTES = 15

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Fixture:
    kickoff: datetime


@dataclass(frozen=True)
class ScheduleDecision:
    allowed: bool
    reason: str
    first_kickoff: datetime | None = None


class _CalendarParser(HTMLParser):
    """Extract fixture dates from the match cards in Fantacalcio's HTML."""

    def __init__(self) -> None:
        super().__init__()
        self.fixtures: list[tuple[str, str]] = []
        self._match_depth = 0
        self._start_date: str | None = None
        self._hours: list[str] = []
        self._in_hours = False

    @staticmethod
    def _classes(attrs: list[tuple[str, str | None]]) -> set[str]:
        value = dict(attrs).get("class") or ""
        return set(value.split())

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        classes = self._classes(attrs)
        if tag == "div" and "match-pill" in classes and not self._match_depth:
            self._match_depth = 1
            self._start_date = None
            self._hours = []
            self._in_hours = False
            return

        if not self._match_depth:
            return

        if tag == "div":
            self._match_depth += 1
        if tag == "meta" and dict(attrs).get("itemprop") == "startDate":
            self._start_date = dict(attrs).get("content")
        if tag == "span" and "hours" in classes:
            self._in_hours = True

    def handle_endtag(self, tag: str) -> None:
        if not self._match_depth:
            return
        if tag == "span" and self._in_hours:
            self._in_hours = False
        if tag == "div":
            self._match_depth -= 1
            if self._match_depth == 0:
                if self._start_date and self._hours:
                    self.fixtures.append((self._start_date, "".join(self._hours).strip()))
                self._start_date = None
                self._hours = []

    def handle_data(self, data: str) -> None:
        if self._match_depth and self._in_hours:
            self._hours.append(data)


def _parse_fixture_datetime(
    start_date: str,
    hours: str,
    timezone: ZoneInfo,
) -> datetime:
    start_date = start_date.strip()
    if "T" in start_date:
        parsed = datetime.fromisoformat(start_date.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone)

    parsed_date = date.fromisoformat(start_date[:10])
    match = re.search(r"\b(\d{1,2}):(\d{2})\b", hours)
    if not match:
        raise ValueError(f"Fixture has no kickoff time: {start_date!r} {hours!r}")
    kickoff_time = time(int(match.group(1)), int(match.group(2)))
    return datetime.combine(parsed_date, kickoff_time, tzinfo=timezone)


def parse_run_time(value: str = DEFAULT_RUN_TIME) -> time:
    try:
        hour, minute = (int(part) for part in value.strip().split(":", 1))
        return time(hour, minute)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"Invalid local run time: {value!r}; expected HH:MM") from exc


def parse_fixtures(html: str, timezone_name: str = DEFAULT_TIMEZONE) -> list[Fixture]:
    """Parse fixture kickoff times from a Fantacalcio calendar response."""

    timezone = ZoneInfo(timezone_name)
    parser = _CalendarParser()
    parser.feed(html)
    fixtures: list[Fixture] = []
    for start_date, hours in parser.fixtures:
        try:
            fixtures.append(Fixture(_parse_fixture_datetime(start_date, hours, timezone)))
        except ValueError:
            log.warning("Ignoring malformed fixture date: %s %s", start_date, hours)
    return fixtures


def fetch_fixtures(
    url: str = DEFAULT_SCHEDULE_URL,
    timezone_name: str = DEFAULT_TIMEZONE,
    timeout: int = 15,
) -> list[Fixture]:
    request = Request(
        url,
        headers={"User-Agent": "automatic-fantasy-squad/1.0"},
    )
    with urlopen(request, timeout=timeout) as response:
        html = response.read().decode("utf-8", errors="replace")
    return parse_fixtures(html, timezone_name)


def evaluate_schedule(
    fixtures: list[Fixture],
    now: datetime | None = None,
    timezone_name: str = DEFAULT_TIMEZONE,
    run_time: str = DEFAULT_RUN_TIME,
) -> ScheduleDecision:
    """Allow one scheduled run at 18:00 or one hour before an earlier game."""

    timezone = ZoneInfo(timezone_name)
    configured_run_time = parse_run_time(run_time)
    current = now or datetime.now(timezone)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone)
    else:
        current = current.astimezone(timezone)

    today = current.date()
    today_kickoffs = sorted(
        fixture.kickoff.astimezone(timezone)
        for fixture in fixtures
        if fixture.kickoff.astimezone(timezone).date() == today
    )
    if not today_kickoffs:
        target = datetime.combine(today, configured_run_time, tzinfo=timezone)
        if target <= current < target + timedelta(minutes=RUN_WINDOW_MINUTES):
            return ScheduleDecision(True, f"scheduled run time reached: {target.isoformat()}")
        return ScheduleDecision(False, f"waiting for scheduled run time {target.isoformat()}")

    first_kickoff = today_kickoffs[0]
    regular_target = datetime.combine(today, configured_run_time, tzinfo=timezone)
    target = min(regular_target, first_kickoff - timedelta(hours=1))
    if current < target:
        return ScheduleDecision(
            False,
            f"waiting for scheduled run time {target.isoformat()}",
            first_kickoff,
        )
    if current < first_kickoff and current < target + timedelta(minutes=RUN_WINDOW_MINUTES):
        return ScheduleDecision(
            True,
            f"scheduled run time reached; first Serie A game is at {first_kickoff.isoformat()}",
            first_kickoff,
        )
    if current < first_kickoff:
        return ScheduleDecision(
            False,
            f"scheduled run window passed; first Serie A game is at {first_kickoff.isoformat()}",
            first_kickoff,
        )
    return ScheduleDecision(
        False,
        f"the first Serie A game started at {first_kickoff.isoformat()}",
        first_kickoff,
    )


def check_schedule_guard(
    url: str = DEFAULT_SCHEDULE_URL,
    timezone_name: str = DEFAULT_TIMEZONE,
    run_time: str = DEFAULT_RUN_TIME,
    now: datetime | None = None,
) -> ScheduleDecision:
    """Fetch and evaluate the schedule; fail closed if it cannot be checked."""

    try:
        fixtures = fetch_fixtures(url, timezone_name)
        return evaluate_schedule(
            fixtures,
            now=now,
            timezone_name=timezone_name,
            run_time=run_time,
        )
    except Exception as exc:
        log.warning("Could not verify today's fixture schedule: %s", exc)
        return ScheduleDecision(False, "fixture schedule could not be verified")


def main() -> int:
    parser = argparse.ArgumentParser(description="Check whether the squad should run now.")
    parser.add_argument("--url", default=DEFAULT_SCHEDULE_URL)
    parser.add_argument("--timezone", default=DEFAULT_TIMEZONE)
    parser.add_argument("--run-time", default=DEFAULT_RUN_TIME)
    args = parser.parse_args()

    decision = check_schedule_guard(
        url=args.url,
        timezone_name=args.timezone,
        run_time=args.run_time,
    )
    print(("run: " if decision.allowed else "skip: ") + decision.reason)
    return 0 if decision.allowed else 1


if __name__ == "__main__":
    raise SystemExit(main())
