from __future__ import annotations

import pytest

from custom_components.calm.core import SlewLimiter


def test_never_moves_faster_than_allowed():
    limiter = SlewLimiter(0.015, 0.015)
    value = 0.0
    for _ in range(200):
        moved = limiter.step(value, 1.0, dt_seconds=30.0)
        assert moved - value <= 0.015 * 0.5 + 1e-9
        value = moved


def test_small_moves_pass_straight_through():
    limiter = SlewLimiter(0.015, 0.015)
    assert limiter.step(0.5, 0.502, dt_seconds=30.0) == pytest.approx(0.502)
    assert limiter.interventions == 0


def test_it_counts_how_often_it_had_to_intervene():
    """A number that stays high means the prediction is lagging."""
    limiter = SlewLimiter(0.015, 0.015)
    for _ in range(5):
        limiter.step(0.0, 1.0, dt_seconds=30.0)
    assert limiter.interventions == 5


def test_allowance_widens_but_does_not_remove_the_limit():
    limiter = SlewLimiter(0.015, 0.015)
    masked = limiter.step(0.0, 1.0, dt_seconds=30.0, allowance=4.0)
    assert masked == pytest.approx(0.03)
    assert masked < 1.0


def test_full_range_takes_the_time_it_should():
    """1.5% a minute puts the whole range at about an hour."""
    limiter = SlewLimiter(0.015, 0.015)
    value, minutes = 0.0, 0
    while value < 1.0 and minutes < 1000:
        value = limiter.step(value, 1.0, dt_seconds=60.0)
        minutes += 1
    assert 60 <= minutes <= 70


def test_it_works_in_both_directions():
    limiter = SlewLimiter(0.015, 0.015)
    assert limiter.step(0.5, 0.0, dt_seconds=60.0) == pytest.approx(0.485)


def test_zero_elapsed_time_moves_nothing():
    limiter = SlewLimiter(0.015, 0.015)
    assert limiter.step(0.4, 1.0, dt_seconds=0.0) == 0.4


def test_a_rate_must_be_positive():
    with pytest.raises(ValueError):
        SlewLimiter(0.0)
