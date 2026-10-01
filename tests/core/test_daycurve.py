from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from custom_components.calm.core import DayCurve, DayCurveConfig
from custom_components.calm.core.photometry import kelvin_to_mired
from custom_components.calm.core.sun import solar_elevation

UTRECHT = (52.09, 5.12)
TZ = ZoneInfo("Europe/Amsterdam")


def local(*args) -> datetime:
    """Wall-clock time here. The curve needs the offset to line up with the sun."""
    return datetime(*args, tzinfo=TZ)


def elevation(when: datetime) -> float:
    return solar_elevation(when, *UTRECHT)


#: Stands in for what a room has learned it wants. The curve is relative, so
#: the actual figure is arbitrary — that is the point of it being an anchor.
ANCHOR = 120.0


def targets_at(when: datetime, config: DayCurveConfig = None, anchor: float = ANCHOR,
               lit: float = 1.0):
    curve = DayCurve(config or DayCurveConfig())
    return curve.targets(when, elevation(when), anchor, lit_fraction=lit)


def test_midday_sits_at_whatever_the_room_asked_for():
    t = targets_at(local(2026, 6, 21, 13, 0))
    assert t.lux == pytest.approx(ANCHOR, abs=1.0)


def test_a_softly_lit_room_never_gets_daylight_white():
    """Kruithof, and it is a feature.

    Lamps at a quarter make a pleasant evening and a miserable one at 5200 K
    — cool light at a low level reads as a parking garage. So the colour
    ceiling follows how hard the lamps are working, and a room that is never
    driven hard is never cold either.

    Measured against the lamp level rather than the target in lux, and the
    change was not cosmetic: the target says what a *sensor* should read, and
    a sensor in the corner of a room reads a fraction of the room. Fifteen
    lux there is an ordinary evening, but fed to a curve anchored at thirty
    lux it pinned two real rooms at 2300 K around the clock with the whole
    schedule unable to move them.
    """
    softly_lit = targets_at(local(2026, 6, 21, 13, 0), lit=0.25)
    driven_hard = targets_at(local(2026, 6, 21, 13, 0), lit=0.95)
    assert softly_lit.kelvin < driven_hard.kelvin
    assert driven_hard.kelvin > 4800.0


def test_the_ceiling_no_longer_depends_on_what_the_sensor_should_read():
    """The bug this replaced: two rooms whose targets were honest about a
    badly placed sensor lost their entire day to the comfort curve."""
    dark_corner = targets_at(local(2026, 6, 21, 13, 0), anchor=15.0, lit=0.9)
    assert dark_corner.kelvin > 4500.0


def test_the_evening_is_warmer_first_and_dimmer_only_after():
    """Colour gives way before brightness does, which is the whole design.

    At half past ten the room is still at its full setting and has warmed
    most of the way; the dimming comes last, once the lamps are as warm as
    they go. That ordering is why the evening reads as cosy rather than as
    the lights being turned down on you.
    """
    day = targets_at(local(2026, 3, 15, 14, 0))
    evening = targets_at(local(2026, 3, 15, 22, 30))
    night = targets_at(local(2026, 3, 15, 23, 45))

    assert evening.mired > day.mired
    assert evening.lux == pytest.approx(day.lux, rel=0.05)
    assert night.lux < day.lux * 0.6
    assert evening.melanopic_edi < day.melanopic_edi


def test_a_june_sunset_does_not_keep_the_room_cold():
    """The reason the evening is anchored to bedtime and not to the sun.

    On 21 June the sun here is still up at ten. Following it would hold the
    room at daylight white an hour before bed.
    """
    late = local(2026, 6, 21, 22, 15)
    assert elevation(late) > -6.0
    assert targets_at(late).kelvin < 3000.0


def test_a_december_afternoon_is_not_treated_as_night():
    """The mirror case: the sun is long gone but the evening has not started."""
    afternoon = local(2026, 12, 8, 17, 0)
    assert elevation(afternoon) < 0
    assert targets_at(afternoon).lux > ANCHOR * 0.8


def test_kruithof_keeps_dim_light_from_being_cold():
    """Where the clock and the eye disagree, and the eye wins.

    Midday, so the schedule wants the coolest light it can get. But the room
    has an eighth of the light it was asked for, and that at 5000 K looks
    like a waiting room. A setting people dislike gets overridden, and an
    overridden setting protects nobody's sleep.
    """
    warm_enough = targets_at(local(2026, 6, 21, 13, 0), lit=0.12)
    assert warm_enough.kelvin < 2700.0
    bright_enough = targets_at(local(2026, 6, 21, 13, 0), lit=0.9)
    assert bright_enough.kelvin > 4500.0


def test_daylight_opens_the_ceiling_rather_than_closing_it():
    """The inversion that got it noticed: with the ceiling tied to the lamp
    level, the lamps backed off as daylight arrived and the room went warmer
    the brighter it got outside — the exact inverse of the schedule. Daylight
    is part of how lit a room is, so it belongs in the numerator."""
    grey = targets_at(local(2026, 6, 21, 13, 0), lit=0.3)
    bright = targets_at(local(2026, 6, 21, 13, 0), lit=3.0)
    assert bright.kelvin > grey.kelvin


@pytest.mark.parametrize("day", [(2026, 6, 21), (2026, 10, 5), (2026, 12, 21), (2026, 3, 20)])
def test_the_setpoint_never_asks_for_more_than_the_limiter_may_give(day):
    """The invariant that keeps the diagnostics meaningful.

    The wake-up ramp is excluded, and deliberately: it is the one change that
    is meant to be seen, it carries its own allowance, and no length of ramp
    brings it under the ordinary limit anyway.

    The rate limiter is the last word, so a setpoint that sweeps faster than
    it is allowed to follow can never be reached — and worse, it would leave
    the limiter saturated every morning, turning its intervention count from
    a warning sign into background noise. The curve has to stay inside the
    budget it is going to be held to.
    """
    config = DayCurveConfig()
    curve = DayCurve(config)
    when = local(*day)
    previous = None
    for _ in range(24 * 60):
        current = curve.targets(when, elevation(when), ANCHOR)
        if previous is not None and not curve.in_morning_ramp(when):
            assert abs(current.mired - previous.mired) < 8.0
        previous = current
        when += timedelta(minutes=1)


def test_the_curve_never_jumps():
    """Corners in the setpoint become corners in the light."""
    curve = DayCurve(DayCurveConfig())
    previous = None
    when = local(2026, 10, 5)
    for _ in range(24 * 30):
        current = curve.targets(when, elevation(when), ANCHOR)
        if previous is not None and not curve.in_morning_ramp(when):
            assert abs(current.lux - previous.lux) < 8.0
        previous = current
        when += timedelta(minutes=2)


def test_the_night_level_holds_after_bedtime():
    small_hours = targets_at(local(2026, 10, 5, 3, 0))
    assert small_hours.melanopic_edi == pytest.approx(1.0, abs=0.2)


def test_a_late_bedtime_stretches_the_day():
    early = DayCurveConfig(bed=time(21, 30))
    late = DayCurveConfig(bed=time(1, 0))
    when = local(2026, 10, 5, 22, 0)
    assert targets_at(when, late).lux > targets_at(when, early).lux


def test_impossible_configurations_are_refused():
    with pytest.raises(ValueError):
        DayCurveConfig(strictness=1.4)
    with pytest.raises(ValueError):
        DayCurveConfig(warm_kelvin=6000.0, cool_kelvin=3000.0)


def test_colour_stays_inside_the_configured_range():
    config = DayCurveConfig()
    warmest = kelvin_to_mired(config.warm_kelvin)
    coolest = kelvin_to_mired(config.cool_kelvin)
    for hour in range(24):
        t = targets_at(local(2026, 6, 21, hour, 0), config)
        assert coolest - 1e-6 <= t.mired <= warmest + 1e-6


def test_the_curve_scales_with_whatever_the_room_decided():
    """No lux number lives in this module, and that is the design.

    The same curve has to suit a living room at forty lux and a workshop at
    four hundred, so it states a shape and lets the room supply the scale.
    """
    when = local(2026, 10, 5, 21, 0)
    small = targets_at(when, anchor=40.0)
    large = targets_at(when, anchor=400.0)
    assert large.lux / small.lux == pytest.approx(10.0, rel=1e-6)


EVENING = DayCurveConfig(wake=time(6, 30), bed=time(21, 30),
                         warm_kelvin=2100.0, cool_kelvin=5000.0)


def test_the_evening_walks_the_colour_down_instead_of_holding_it():
    """The failure this replaced, on a real room's own settings.

    The schedule asks for a melanopic level and the solver warms only as far
    as it must to reach it. A softly lit room already delivers less than the
    evening figure at four thousand kelvin, so the solver saw nothing to do
    and the sun's cap held the colour there from half seven until half nine
    while the target fell by a factor of ten underneath it. Bedtime arrived
    with the light exactly as it had been all evening.
    """
    colours = [
        targets_at(local(2026, 10, 12, h, m), config=EVENING, anchor=20.0).kelvin
        for h, m in [(18, 30), (19, 0), (19, 30), (20, 0), (20, 30), (21, 0)]
    ]
    for earlier, later in zip(colours, colours[1:]):
        assert later < earlier, "de avond staat stil: %s" % colours
    assert colours[0] - colours[-1] > 2000


def test_and_arrives_at_its_warmest_by_bedtime_not_after_it():
    at_bed = targets_at(local(2026, 10, 12, 21, 30), config=EVENING, anchor=20.0)
    assert at_bed.kelvin <= EVENING.warm_kelvin + 50


def test_a_dim_room_warms_even_though_dimness_alone_would_pass():
    """Twenty lux at four thousand kelvin already sits under the evening
    figure, so nothing in the melanopic sum asks for warmth. Colour is the
    half people see and the half that costs no brightness, so it leads
    regardless — that ordering is the whole design."""
    curve = DayCurve(EVENING)
    when = local(2026, 10, 12, 20, 30)
    assert curve.melanopic_target(when) < 30.0, "de avondknijp is nog niet begonnen"
    assert targets_at(when, config=EVENING, anchor=20.0).kelvin < 2800


def test_the_middle_of_the_day_is_left_alone_by_all_of_this():
    """The first attempt clamped noon to the after-dark cap."""
    noon = targets_at(local(2026, 10, 12, 12, 0), config=EVENING, anchor=20.0)
    assert noon.kelvin > 4800


def test_the_evening_brightness_comes_after_the_colour():
    """30 September: bright by day, too bright in the evening. Colour still
    goes first: the lamps warm across the three hours before bed, and only
    the second half of that brings the brightness down, reaching the evening
    setting at bedtime. Back up within half an hour of waking, because the
    morning is when bright light counts."""
    curve = DayCurve(DayCurveConfig(wake=time(6, 30), bed=time(22, 0)))
    assert curve.day_fraction(local(2026, 9, 30, 14, 0)) == 1.0
    assert curve.day_fraction(local(2026, 9, 30, 20, 0)) == 1.0, "de helderheid ging voor de kleur"
    assert 0.0 < curve.day_fraction(local(2026, 9, 30, 21, 15)) < 1.0
    assert curve.day_fraction(local(2026, 9, 30, 22, 0)) == 0.0
    assert curve.day_fraction(local(2026, 10, 1, 3, 0)) == 0.0
    assert curve.day_fraction(local(2026, 10, 1, 6, 30)) == 0.0
    assert curve.day_fraction(local(2026, 10, 1, 7, 0)) == 1.0
