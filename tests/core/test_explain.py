"""The sentence that answers "why is it like this"."""

from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

import pytest

from custom_components.calm.core import Why, explain

TZ = ZoneInfo("Europe/Amsterdam")
BED = time(23, 0)


def say(diagnostics, hour=21, minute=40, lit=True, language="nl", **kwargs):
    kwargs.setdefault("morning", False)
    kwargs.setdefault("night", False)
    return explain(
        now=datetime(2026, 9, 30, hour, minute, tzinfo=TZ),
        diagnostics=diagnostics, lit=lit, kelvin=2700.0, bed=BED,
        language=language, **kwargs,
    )


def test_counting_down_does_not_claim_there_is_enough_daylight():
    """22 September: the room said "enough daylight" while sitting on a
    ten-minute clock at four lux, and was switched off and on in frustration."""
    e = say({"reason": "waiting_to_return", "rung": "daylight", "returning_in_s": 580}, lit=False)
    assert e.why is Why.RETURNING
    assert "genoeg daglicht" not in e.sentence
    assert "10 minuten" in e.sentence


def test_a_room_with_a_motion_sensor_says_walking_in_is_enough():
    e = say({"reason": "waiting_to_return", "rung": "daylight", "returning_in_s": 300},
            lit=False, has_motion_sensor=True)
    assert "Loop je binnen" in e.sentence
    e = say({"reason": "waiting_to_return", "rung": "daylight", "returning_in_s": 300}, lit=False)
    assert "Loop je binnen" not in e.sentence


def test_the_countdown_never_says_zero():
    e = say({"reason": "waiting_to_return", "rung": "daylight", "returning_in_s": 20}, lit=False)
    assert "nog even" in e.sentence
    e = say({"reason": "waiting_to_return", "rung": "daylight", "returning_in_s": 61}, lit=False)
    assert "nog 2 minuten" in e.sentence


def test_enough_daylight_says_so():
    e = say({"reason": "daylight_sufficient", "rung": "daylight"}, hour=13, lit=False)
    assert e.why is Why.DAYLIGHT
    assert "genoeg daglicht" in e.sentence


def test_calm_not_running_the_room_names_the_reason_it_was_given():
    e = say({"reason": "signal_off", "rung": "inactive",
             "inactive_because": "er is niemand thuis"}, lit=False)
    assert e.why is Why.INACTIVE
    assert e.sentence == "Uit: er is niemand thuis."


def test_calm_not_running_the_room_without_a_reason_still_says_something():
    e = say({"reason": "signal_off", "rung": "inactive"}, lit=False)
    assert "Calm regelt deze ruimte nu niet" in e.sentence


def test_a_hand_on_the_lamps_says_when_calm_takes_over_again():
    e = say({"reason": "manual", "rung": "manual", "manual_remaining_s": 3600}, hour=20, minute=15)
    assert e.why is Why.MANUAL
    assert "21:15" in e.sentence


def test_switched_off_by_hand():
    e = say({"reason": "switched_off_by_hand", "rung": "manual"}, lit=False)
    assert e.why is Why.SWITCHED_OFF


def test_an_override_is_named():
    e = say({"reason": "override", "rung": "override", "scene": "override", "mode": "Werklicht"})
    assert e.sentence == "Werklicht staat aan."
    e = say({"reason": "override_auto", "rung": "override", "scene": "override", "mode": "Eten"})
    assert e.why is Why.OVERRIDE_AUTO
    assert "vanzelf" in e.sentence


def test_standby_and_an_empty_room():
    assert say({"reason": "standby", "rung": "empty", "scene": "standby"}).why is Why.STANDBY
    assert say({"reason": "nobody_here", "rung": "empty", "scene": "off"}, lit=False).why is Why.EMPTY


def test_movement_while_calm_is_off():
    assert say({"reason": "nightlight", "rung": "inactive", "scene": "nightlight"}).why is Why.NIGHTLIGHT
    assert say({"reason": "motion_while_off", "rung": "inactive", "scene": "adaptive"}).why is Why.MOTION_WHILE_OFF


@pytest.mark.parametrize("hour,morning,night,why", [
    (7, True, False, Why.MORNING),
    (13, False, False, Why.DAYTIME),
    (21, False, False, Why.EVENING),
    (1, False, True, Why.NIGHT),
])
def test_the_ordinary_room_follows_the_time_of_day(hour, morning, night, why):
    e = say({"reason": "adapting", "rung": "occupied", "scene": "adaptive"},
            hour=hour, minute=0, morning=morning, night=night)
    assert e.why is why


def test_the_evening_names_bedtime():
    e = say({"reason": "adapting", "rung": "occupied", "scene": "adaptive"}, hour=21, minute=40)
    assert "21:40" in e.sentence and "23:00" in e.sentence


def test_a_quiet_sensor_is_owned_up_to_while_the_lamps_burn():
    e = say({"reason": "adapting", "rung": "occupied", "scene": "adaptive", "stale": True})
    assert "zegt al een tijd niets" in e.sentence


def test_no_sentence_carries_lux():
    """Plain words. The numbers live in the details."""
    for diagnostics in (
        {"reason": "daylight_sufficient", "rung": "daylight", "daylight_lux": 312.0, "threshold_lux": 25.0},
        {"reason": "adapting", "rung": "occupied", "scene": "adaptive", "daylight_lux": 3.0},
    ):
        e = say(diagnostics, lit=diagnostics["rung"] != "daylight")
        assert "lux" not in e.sentence
        assert e.detail["daylight_lux"] == diagnostics["daylight_lux"]


@pytest.mark.parametrize("why", list(Why))
def test_every_reason_has_a_sentence_in_both_languages(why):
    from custom_components.calm.core.explain import _WORDS

    assert why in _WORDS["nl"] and why in _WORDS["en"]


def test_english_reads_as_english():
    e = say({"reason": "waiting_to_return", "rung": "daylight", "returning_in_s": 580},
            lit=False, language="en")
    assert "another 10 minutes" in e.sentence
