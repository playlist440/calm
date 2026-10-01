"""Whether anybody is in the room — which a motion sensor does not answer.

A motion sensor says "something moved just now". The room wants to know "is
somebody here", and people sit still: a film, a book, a meal. Every one of
these tests exists because the gap between those two questions is where
motion lighting normally goes wrong, and going wrong here means a room that
dims while somebody is sitting in it.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from custom_components.calm.core import PresenceHold

TZ = ZoneInfo("Europe/Amsterdam")
NOW = datetime(2026, 9, 15, 21, 0, tzinfo=TZ)


def test_a_room_that_has_told_us_nothing_counts_as_occupied():
    """The start-up case, and the asymmetry behind it. Holding the light a
    little too long is a nuisance; dimming a room somebody is sitting in
    because a process restarted is the thing that gets an integration
    deleted."""
    assert PresenceHold().occupied(NOW)
    assert not PresenceHold().known


def test_movement_means_occupied():
    hold = PresenceHold(hold_s=600)
    hold.seen(NOW, True)
    assert hold.occupied(NOW)


def test_a_sensor_that_is_still_reporting_movement_never_times_out():
    """A presence sensor holds its state for as long as somebody is there,
    and the hold must not undercut it."""
    hold = PresenceHold(hold_s=600)
    hold.seen(NOW, True)
    assert hold.occupied(NOW + timedelta(hours=3))


def test_the_room_stays_occupied_through_the_hold():
    """A Hue sensor clears about a minute after somebody stops moving, and
    people sit still for far longer than that."""
    hold = PresenceHold(hold_s=600)
    hold.seen(NOW, False)
    assert hold.occupied(NOW + timedelta(minutes=9))


def test_and_empties_once_the_hold_has_run_out():
    hold = PresenceHold(hold_s=600)
    hold.seen(NOW, False)
    assert not hold.occupied(NOW + timedelta(minutes=11))


def test_the_hold_runs_from_when_the_sensor_cleared_not_from_when_we_heard():
    """A room whose sensor has been clear for an hour is known to have been
    clear for an hour the moment Calm starts, rather than being handed a
    fresh ten minutes of grace it has not earned."""
    hold = PresenceHold(hold_s=600)
    hold.seen(NOW - timedelta(hours=1), False)
    assert not hold.occupied(NOW)


def test_any_movement_at_all_restarts_the_hold():
    """Which is what makes a wrong guess cheap: one hand in the air and the
    room has another ten minutes."""
    hold = PresenceHold(hold_s=600)
    hold.seen(NOW, False)
    assert not hold.occupied(NOW + timedelta(minutes=11))
    hold.seen(NOW + timedelta(minutes=11), True)
    hold.seen(NOW + timedelta(minutes=12), False)
    assert hold.occupied(NOW + timedelta(minutes=20))


def test_how_long_the_room_has_been_empty_is_available():
    hold = PresenceHold(hold_s=600)
    hold.seen(NOW, False)
    assert hold.empty_for(NOW + timedelta(minutes=5)) == 0.0
    assert hold.empty_for(NOW + timedelta(minutes=15)) == 300.0


def test_a_hold_of_nothing_still_behaves():
    """Somebody who wants the room to follow the sensor exactly."""
    hold = PresenceHold(hold_s=0)
    hold.seen(NOW, False)
    assert not hold.occupied(NOW + timedelta(seconds=1))
    hold.seen(NOW, True)
    assert hold.occupied(NOW + timedelta(hours=1))
