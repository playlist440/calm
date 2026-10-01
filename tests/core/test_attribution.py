"""Our own command coming back, versus a hand on the dimmer."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from custom_components.calm.core import ChangeAttributor

TZ = ZoneInfo("Europe/Amsterdam")
NOW = datetime(2026, 9, 13, 20, 0, tzinfo=TZ)
LAMPS = {"light.spot_a": 180, "light.spot_b": 180}


def attributor(**kwargs) -> ChangeAttributor:
    a = ChangeAttributor(**kwargs)
    a.expect(NOW, LAMPS, mired=320.0, transition_s=300.0)
    return a


def test_a_change_carrying_our_context_is_ours():
    a = ChangeAttributor()
    a.issued("abc123")
    assert a.is_ours(NOW, "light.spot_a", 99, 400.0, context_id="abc123")


def test_a_lamp_part_way_through_our_fade_is_ours():
    """The one that was breaking it in a real house.

    A five-minute transition means five minutes of the bridge reporting
    values on the way to the target. Not one of them matches what was asked
    for, and treating any of them as a person would stop the loop dead for
    two hours.
    """
    a = attributor()
    for minute, brightness in ((1, 120), (2, 140), (3, 160), (4, 175)):
        assert a.is_ours(NOW + timedelta(minutes=minute), "light.spot_a", brightness, 340.0)


def test_a_lamp_that_arrived_where_we_asked_is_ours():
    a = attributor()
    later = NOW + timedelta(minutes=10)
    assert a.is_ours(later, "light.spot_a", 180, 320.0)
    assert a.is_ours(later, "light.spot_a", 174, 331.0), "kleine afronding hoort te mogen"


def test_somebody_turning_it_right_down_is_not_ours():
    a = attributor()
    later = NOW + timedelta(minutes=10)
    assert not a.is_ours(later, "light.spot_a", 40, 320.0)


def test_somebody_changing_the_colour_is_not_ours():
    a = attributor()
    later = NOW + timedelta(minutes=10)
    assert not a.is_ours(later, "light.spot_a", 180, 450.0)


def test_a_lamp_we_never_addressed_is_not_ours():
    a = attributor()
    later = NOW + timedelta(minutes=10)
    assert not a.is_ours(later, "light.iemand_anders", 180, 320.0)


def test_a_change_before_we_have_asked_for_anything_is_not_a_takeover():
    """This used to assert the opposite, and the opposite was a bug.

    Nothing asked for means nothing to measure a change against. That is not
    a neutral state: it is the state every reload begins in, and changing any
    setting reloads the whole entry — while the lamps are still walking
    towards a command the previous object sent. Judging those against no
    expectation put a room into "taken over by hand" for two hours because
    somebody moved the target slider.
    """
    assert ChangeAttributor().is_ours(NOW, "light.spot_a", 180, 320.0)


def test_the_benefit_of_the_doubt_ends_as_soon_as_there_is_evidence():
    """It lasts exactly until something has been asked for — not a moment
    longer, or a hand on the dimmer would never be noticed at all."""
    a = ChangeAttributor()
    a.expect(NOW - timedelta(minutes=5), LAMPS, mired=320.0, transition_s=2.0)
    assert not a.is_ours(NOW, "light.spot_a", 60, 320.0)


def test_a_lamp_finishing_the_previous_object_s_fade_is_not_a_person():
    """The live failure, in the shape it actually arrived in: a fresh
    attributor after a reload, and a lamp reporting a value from a fade that
    a now-discarded object started."""
    fresh = ChangeAttributor()
    assert fresh.is_ours(NOW, "light.spot_a", 143, 355.0, context_id="hue-pushed")


def test_the_newest_context_ids_are_the_ones_kept():
    """A set trimmed by slicing keeps whichever ids it feels like, and the
    ones still in flight are exactly the ones you cannot afford to lose."""
    a = ChangeAttributor(remembered_contexts=4)
    a._contexts = __import__("collections").deque(maxlen=4)
    for i in range(10):
        a.issued("ctx-%d" % i)
    # Something has been asked for, and it is neither what is being reported
    # nor still in flight, so the context id is the only thing left that can
    # make a change ours.
    a.expect(NOW - timedelta(minutes=5), LAMPS, mired=320.0, transition_s=2.0)
    assert a.is_ours(NOW, "light.spot_a", 1, 1.0, context_id="ctx-9")
    assert a.is_ours(NOW, "light.spot_a", 1, 1.0, context_id="ctx-6")
    assert not a.is_ours(NOW, "light.spot_a", 1, 1.0, context_id="ctx-0")


def test_after_a_fade_has_finished_a_stray_value_is_somebody_else():
    """The window has to close, or a hand on the dimmer never registers."""
    a = attributor()
    during = NOW + timedelta(minutes=3)
    after = NOW + timedelta(minutes=8)
    assert a.is_ours(during, "light.spot_a", 90, 320.0)
    assert not a.is_ours(after, "light.spot_a", 90, 320.0)
