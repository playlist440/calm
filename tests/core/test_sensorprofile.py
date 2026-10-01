"""Working out what kind of sensor somebody actually has."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from custom_components.calm.core import SensorWatcher, Speed, suggested_filter_tau

TZ = ZoneInfo("Europe/Amsterdam")
START = datetime(2026, 9, 13, 12, 0, tzinfo=TZ)


def watch(interval_s, values):
    watcher = SensorWatcher()
    when = START
    for value in values:
        watcher.observe(when, value)
        when += timedelta(seconds=interval_s)
    return watcher.profile()


def test_a_hue_sensor_is_recognised_as_slow():
    """Five minutes and whole lux, measured over thirteen real days."""
    profile = watch(300.0, [22, 20, 22, 20, 19, 18, 19, 23, 22, 21])
    assert profile.speed is Speed.SLOW
    assert profile.interval_s == pytest.approx(300.0)
    assert profile.resolution_lux == pytest.approx(1.0)


def test_an_esphome_sensor_is_recognised_as_fast():
    profile = watch(2.0, [40.12, 40.19, 40.07, 40.22, 40.31, 40.18, 40.25, 40.11])
    assert profile.speed is Speed.FAST
    assert profile.resolution_lux < 0.2


def test_the_step_it_counts_in_is_read_off_the_data():
    """Nothing configured, no list of devices to maintain."""
    assert watch(60.0, [10, 11, 10, 12, 11, 13, 12, 14]).resolution_lux == pytest.approx(1.0)
    assert watch(60.0, [10, 10.5, 10, 11, 10.5, 11.5, 11, 12]).resolution_lux == pytest.approx(0.5)


def test_it_says_how_short_a_cloud_can_be_and_still_be_seen():
    """Two readings is the least that can show something happening."""
    slow = watch(300.0, [20, 21, 20, 22, 21, 23, 22, 24])
    assert slow.shortest_cloud_s == pytest.approx(600.0)
    fast = watch(5.0, [20, 21, 20, 22, 21, 23, 22, 24])
    assert fast.shortest_cloud_s == pytest.approx(10.0)


def test_one_lux_is_excellent_at_three_hundred_and_useless_at_four():
    """Resolution only means something next to the level it is measuring."""
    bright = watch(60.0, [300, 301, 300, 302, 301, 303, 302, 304])
    dim = watch(60.0, [4, 5, 4, 6, 5, 7, 6, 8])
    assert bright.relative_resolution < 0.01
    assert dim.relative_resolution > 0.15


def test_it_admits_to_not_knowing_yet():
    watcher = SensorWatcher()
    watcher.observe(START, 20.0)
    assert not watcher.ready
    assert watcher.profile().speed is Speed.UNKNOWN


def test_the_averaging_window_follows_the_sensor():
    """A fixed window suits exactly one kind of hardware."""
    slow = suggested_filter_tau(watch(300.0, [20, 21, 20, 22, 21, 23, 22, 24]))
    fast = suggested_filter_tau(watch(2.0, [20, 21, 20, 22, 21, 23, 22, 24]))
    assert slow > fast * 4
    assert 60.0 <= fast <= 900.0
    assert 60.0 <= slow <= 900.0


def test_an_unknown_sensor_gets_a_safe_middle():
    assert suggested_filter_tau(watch(300.0, [20, 21])) == pytest.approx(300.0)
