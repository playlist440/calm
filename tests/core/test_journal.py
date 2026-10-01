"""The room's own record of its day."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from custom_components.calm.core import journal_columns, journal_row

CEST = timezone(timedelta(hours=2))
WHEN = datetime(2026, 9, 29, 19, 29, 44, tzinfo=CEST)


@dataclass
class FakeCommand:
    master: float = 0.75
    mired: Optional[float] = 403.0
    diagnostics: Dict[str, Any] = field(default_factory=dict)


def line(**diagnostics) -> Dict[str, str]:
    return dict(zip(journal_columns(), journal_row(WHEN, 4.0, FakeCommand(diagnostics=diagnostics), 1)))


def test_a_line_has_one_value_for_every_column():
    assert len(journal_row(WHEN, 4.0, FakeCommand(), 0)) == len(journal_columns())


def test_the_line_says_which_rung_decided():
    """The first thing anybody reading the file back wants to know. On 29
    September it took an afternoon to work out that standby had been
    blocking the kitchen; with the rung on every line it is one column."""
    row = line(rung="daylight", scene="standby", reason="waiting_to_return")
    assert row["rung"] == "daylight"
    assert row["scene"] == "standby"
    assert row["reason"] == "waiting_to_return"


def test_the_two_numbers_the_slider_shows_are_side_by_side():
    row = line(daylight_lux=2.8, threshold_lux=25.0)
    assert row["daylight_lux"] == "2.8"
    assert row["threshold_lux"] == "25.0"


def test_the_time_is_written_with_its_offset():
    """Without it a file read next winter shifts by an hour, and every
    conclusion about the evening moves with it."""
    assert line()["time"] == "2026-09-29T19:29:44+02:00"


def test_a_line_without_a_fresh_reading_leaves_the_reading_empty():
    """Rather than repeating the last one, which would read as a sensor that
    kept speaking: the opposite of what the line is there to show."""
    row = dict(zip(journal_columns(), journal_row(WHEN, None, FakeCommand(), 0)))
    assert row["measured_lux"] == ""


def test_colour_is_written_as_kelvin():
    assert line()["kelvin"] == "2481"


def test_a_stale_sensor_is_marked():
    assert line(stale=True)["stale"] == "1"
    assert line(stale=False)["stale"] == "0"


def test_empty_is_not_the_same_as_no():
    """A room without a motion sensor leaves motion empty; a room whose
    sensor said "nothing" writes 0. A reader has to be able to tell them apart."""
    without = dict(zip(journal_columns(), journal_row(WHEN, 4.0, FakeCommand(), 0)))
    quiet = dict(zip(journal_columns(), journal_row(
        WHEN, 4.0, FakeCommand(), 0, motion=False, occupied=False)))
    assert without["motion"] == "" and quiet["motion"] == "0"


def test_missing_diagnostics_leave_blanks_rather_than_breaking_the_line():
    row = line()
    assert row["target_lux"] == ""
    assert row["rung"] == ""


def test_the_header_is_not_translated():
    assert journal_columns()[0] == "time"
    assert "measured_lux" in journal_columns()
