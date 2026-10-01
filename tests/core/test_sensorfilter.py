"""Getting a usable signal out of a light sensor in a room people live in.

Written after the filter had spent five days quietly deciding that the
sensor was wrong. It had no tests of its own at all, which is why changing
how patient it is broke nothing and therefore proved nothing.
"""

from __future__ import annotations

import pytest

from custom_components.calm.core.sensorfilter import SensorFilter

#: A Hue sensor speaks about this often, and every one of these tests turns
#: on that number: patience counted in readings is patience counted in
#: five-minute blocks.
CADENCE = 300.0


def _settle(f: SensorFilter, lux: float, times: int = 6) -> None:
    for _ in range(times):
        f.update(lux, CADENCE)


def test_a_single_outlier_is_ignored():
    """Somebody walks between the lamp and the sensor. One reading, gone by
    the next, and the room should not have moved at all."""
    f = SensorFilter()
    _settle(f, 100.0)
    assert f.update(4.0, CADENCE).lux == pytest.approx(100.0)
    assert f.update(100.0, CADENCE).lux == pytest.approx(100.0, abs=1.0)


def test_two_readings_that_agree_are_the_room_not_the_noise():
    """The one that matters, and the one that was wrong.

    A spike is by definition a sample that goes away. A second reading
    agreeing with the first is not noise repeating itself — it is the room,
    and the filter is the thing that is out of date. Holding out for a third
    costs another five minutes, and a real living room spent thirteen
    minutes on 42 lux while the sensor said twelve and then four, and called
    that enough daylight to stay dark.
    """
    f = SensorFilter()
    _settle(f, 45.0)
    assert f.update(12.0, CADENCE).lux == pytest.approx(45.0), "eerste mag nog"
    assert f.update(4.0, CADENCE).lux == pytest.approx(4.0), (
        "de tweede bevestiging is geen ruis meer"
    )


def test_and_the_same_going_up():
    """Somebody opens the curtains. It is the same event with the sign
    flipped, and a filter that is quick down and slow up would have the room
    blazing away on a bright morning."""
    f = SensorFilter()
    _settle(f, 10.0)
    assert f.update(200.0, CADENCE).lux == pytest.approx(10.0)
    assert f.update(220.0, CADENCE).lux == pytest.approx(220.0)


def test_it_still_takes_more_than_one_reading():
    """The other direction of the same trade. Accepting the first reading
    outright would make the filter nothing at all, and the passer-by would
    move the whole room."""
    f = SensorFilter()
    _settle(f, 100.0)
    held = f.update(2.0, CADENCE)
    assert held.lux == pytest.approx(100.0)


def test_an_ordinary_change_never_goes_near_the_spike_test():
    """Most of the day is not spikes. A cloud halving the light is a real
    event and goes through the average, which is what the average is for —
    the spike test only has an opinion beyond a factor of two and a half."""
    f = SensorFilter()
    _settle(f, 100.0)
    out = f.update(55.0, CADENCE)
    assert 55.0 < out.lux < 100.0, "een wolk hoort te middelen, niet te wachten"


def test_a_change_we_made_ourselves_is_believed_at_once():
    """The lamps just moved, so a large jump is explained. Rejecting it
    throws away the single most informative reading there is."""
    f = SensorFilter()
    _settle(f, 4.0)
    assert f.update(60.0, CADENCE, expect_change=True).lux == pytest.approx(60.0)


def test_the_prompt_value_is_never_the_held_one_once_it_gives_in():
    """Two signals, two jobs: the controller wants calm, the estimator wants
    prompt. When the filter accepts a change both have to see it, or the
    estimator spends the evening explaining a step it was never shown."""
    f = SensorFilter()
    _settle(f, 45.0)
    f.update(12.0, CADENCE)
    out = f.update(4.0, CADENCE)
    assert out.prompt == pytest.approx(4.0)
    assert out.lux == pytest.approx(4.0)


def test_saturation_is_reported_separately_from_all_of_this():
    f = SensorFilter()
    assert f.update(4000.0, CADENCE).saturated
    assert not f.update(40.0, CADENCE, expect_change=True).saturated
