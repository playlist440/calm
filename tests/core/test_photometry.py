from __future__ import annotations

import pytest

from custom_components.calm.core.photometry import (
    device_to_perceptual,
    kelvin_to_mired,
    luminous_to_perceptual,
    mired_to_kelvin,
    perceptual_to_device,
    perceptual_to_luminous,
)


def test_half_perceived_is_about_a_fifth_of_the_light():
    """The rule of thumb the whole dimming design rests on."""
    assert perceptual_to_luminous(0.5) == pytest.approx(0.184, abs=0.005)


def test_ends_are_exact():
    assert perceptual_to_luminous(0.0) == 0.0
    assert perceptual_to_luminous(1.0) == pytest.approx(1.0)


def test_perceptual_and_luminous_round_trip():
    for step in range(0, 101):
        p = step / 100.0
        assert luminous_to_perceptual(perceptual_to_luminous(p)) == pytest.approx(p, abs=1e-6)


def test_curve_has_no_kink_at_the_knee():
    """The two halves of the CIE curve must meet, or dimming stutters there."""
    below = perceptual_to_luminous(0.0799)
    above = perceptual_to_luminous(0.0801)
    assert abs(above - below) < 1e-4


def test_luminous_is_strictly_increasing():
    previous = -1.0
    for step in range(0, 1001):
        value = perceptual_to_luminous(step / 1000.0)
        assert value > previous
        previous = value


def test_device_round_trip_within_one_step():
    for device in range(0, 256):
        assert perceptual_to_device(device_to_perceptual(device)) == device


def test_mired_and_kelvin_are_inverses():
    assert mired_to_kelvin(kelvin_to_mired(2700.0)) == pytest.approx(2700.0)
    assert kelvin_to_mired(1_000_000.0) == pytest.approx(1.0)


def test_equal_mired_steps_are_more_even_than_kelvin_steps():
    """Why the colour loop works in mired: kelvin is wildly non-uniform."""
    warm_step = kelvin_to_mired(2000.0) - kelvin_to_mired(2500.0)
    cool_step = kelvin_to_mired(6000.0) - kelvin_to_mired(6500.0)
    assert warm_step > cool_step * 6
