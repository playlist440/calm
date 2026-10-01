from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from custom_components.calm.core.sun import solar_elevation

UTRECHT = (52.09, 5.12)


def test_midsummer_noon_is_high_and_midwinter_noon_is_low():
    summer = solar_elevation(datetime(2026, 6, 21, 11, 40, tzinfo=timezone.utc), *UTRECHT)
    winter = solar_elevation(datetime(2026, 12, 21, 11, 40, tzinfo=timezone.utc), *UTRECHT)
    assert summer == pytest.approx(61.3, abs=1.0)
    assert winter == pytest.approx(14.4, abs=1.0)


def test_the_sun_is_below_the_horizon_at_midnight():
    assert solar_elevation(datetime(2026, 3, 21, 0, 0, tzinfo=timezone.utc), *UTRECHT) < -20


def test_equinox_noon_matches_ninety_minus_latitude():
    noon = solar_elevation(datetime(2026, 3, 20, 11, 52, tzinfo=timezone.utc), *UTRECHT)
    assert noon == pytest.approx(90.0 - UTRECHT[0], abs=1.0)


def test_naive_times_are_read_as_utc():
    naive = datetime(2026, 6, 21, 12, 0)
    aware = datetime(2026, 6, 21, 12, 0, tzinfo=timezone.utc)
    assert solar_elevation(naive, *UTRECHT) == pytest.approx(
        solar_elevation(aware, *UTRECHT)
    )


def test_the_southern_hemisphere_has_its_seasons_the_other_way_round():
    sydney = (-33.87, 151.21)
    june = solar_elevation(datetime(2026, 6, 21, 2, 0, tzinfo=timezone.utc), *sydney)
    december = solar_elevation(datetime(2026, 12, 21, 2, 0, tzinfo=timezone.utc), *sydney)
    assert december > june


def test_elevation_moves_smoothly_through_the_day():
    previous = None
    when = datetime(2026, 5, 1, tzinfo=timezone.utc)
    for _ in range(24 * 6):
        current = solar_elevation(when, *UTRECHT)
        if previous is not None:
            assert abs(current - previous) < 4.0
        previous = current
        when += timedelta(minutes=10)
