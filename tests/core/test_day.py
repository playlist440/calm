"""Whole days through a simulated room.

The unit tests pin each rule. These check that the rules together keep the
promises that only show over hours: nobody sees the lamps drift, a cloudy
afternoon is not a light switch, dusk is answered, and the evening goes warm.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from custom_components.calm.core import (
    ControllerConfig,
    DayCurve,
    DayCurveConfig,
    DimProfile,
    EmptyAction,
    Lamp,
    RoomController,
    RoomSettings,
)

from ..sim.daylight import DaylightModel
from ..sim.room import SimulatedLamp, SimulatedRoom
from ..sim.run import run_day
from ..sim.sensor import HueLightSensor
from .helpers import UTRECHT

TZ = ZoneInfo("Europe/Amsterdam")
DAY = datetime(2026, 10, 12, 6, 0, tzinfo=TZ)


def living_room(seed: int = 7):
    """Twelve spots the sensor barely sees, and a window that gives the
    sensor a few tens of lux on an autumn afternoon."""
    controller = RoomController(
        profile=DimProfile.from_lamps(
            [Lamp(id="spot_%d" % i, full_contribution=0.15) for i in range(12)]
        ),
        settings=RoomSettings(threshold_lux=30.0),
        day_curve=DayCurve(DayCurveConfig()),
        config=ControllerConfig(latitude=UTRECHT[0], longitude=UTRECHT[1]),
    )
    room = SimulatedRoom(
        lamps=[SimulatedLamp(id="spot_%d" % i, full_contribution=0.08) for i in range(12)]
    )
    return controller, room, DaylightModel(peak_lux=160.0, seed=seed), HueLightSensor(seed=seed)


def kitchen(seed: int = 3):
    controller = RoomController(
        profile=DimProfile.from_lamps(
            [Lamp(id="lamp_%d" % i, full_contribution=10.0) for i in range(2)]
        ),
        settings=RoomSettings(
            threshold_lux=25.0, has_motion_sensor=True, use_motion=True,
            when_empty=EmptyAction.STANDBY, standby_level=0.20,
        ),
        day_curve=DayCurve(DayCurveConfig()),
        config=ControllerConfig(latitude=UTRECHT[0], longitude=UTRECHT[1]),
    )
    room = SimulatedRoom(
        lamps=[SimulatedLamp(id="lamp_%d" % i, full_contribution=7.0) for i in range(2)]
    )
    return controller, room, DaylightModel(peak_lux=300.0, seed=seed), HueLightSensor(seed=seed)


def test_a_whole_cloudy_day_never_drifts_faster_than_the_limit():
    """The guarantee the project started from. Arrivals and switches may be
    seen; an ordinary step, one tick of the room adapting, may not."""
    controller, room, daylight, sensor = living_room()
    trace = run_day(controller, room, daylight, sensor, DAY, hours=18,
                    enabled=lambda now: True)
    cfg = controller.config
    ordinary = 0
    for before, row in zip(trace.rows, trace.rows[1:]):
        if not (before.lit and row.lit and row.action == "apply"):
            continue
        if row.transition_s != cfg.interval_s:
            continue  # a scene change, allowed to be seen
        ordinary += 1
        morning = controller.day_curve.in_morning_ramp(row.when)
        allowance = cfg.morning_allowance if morning else 1.0
        up = cfg.slew_up_per_minute * 0.5 * allowance
        down = cfg.slew_down_per_minute * 0.5 * allowance
        change = row.master - before.master
        assert -down - 1e-9 <= change <= up + 1e-9, (
            "om %s bewoog het licht %.3f in één tik" % (row.when.strftime("%H:%M"), change)
        )
    assert ordinary > 100, "de dag bevatte nauwelijks gewone stappen; de test meet niets"


def test_a_cloudy_afternoon_is_not_a_light_switch():
    """The wait before coming back exists for this: clouds that come and go
    on a grey afternoon must not flick the lamps on and off every time.
    Measured against several different afternoons, not one lucky one.

    What is left is the sensor's own limit: a switch-on after eight or more
    minutes of real darkness that lifted in the last few, before a sensor
    that speaks every five could say so. So each switch-on is checked for
    the darkness before it, and the count is kept low over all eight
    afternoons together. Counted per afternoon it depended on when the
    sensor happened to speak, which anything earlier in the day shifts:
    the evening setting, which does nothing in the afternoon, moved one
    afternoon from one switch-on to three and two others from one and two
    to none."""
    total = 0
    for seed in range(1, 9):
        controller, room, daylight, sensor = living_room(seed)
        threshold = controller.settings.threshold_lux
        trace = run_day(controller, room, daylight, sensor, DAY, hours=18,
                        enabled=lambda now: True)
        for on in trace.between(DAY.replace(hour=11), DAY.replace(hour=16)).switched_on():
            before = trace.between(on.when - timedelta(minutes=15), on.when).rows
            step = (before[1].when - before[0].when).total_seconds() / 60.0
            dark = sum(step for row in before if row.daylight_lux < threshold)
            assert dark >= 8.0, (
                "om %s gingen de lampen aan na %.1f minuut echte donkerte" % (
                    on.when.strftime("%H:%M"), dark)
            )
            total += 1
    assert total <= 8, "acht bewolkte middagen zetten de lampen %d keer aan" % total


def test_dusk_is_answered_without_anybody_asking():
    """Nobody in the room, nobody pressing anything: once the daylight has
    gone below the line and stays there, the lamps come on within the
    filter's one held-back reading plus the cloud wait."""
    controller, room, daylight, sensor = living_room()
    trace = run_day(controller, room, daylight, sensor, DAY, hours=18,
                    enabled=lambda now: True)
    evening = trace.between(DAY.replace(hour=15), DAY.replace(hour=22))
    below_from = None
    for row in evening.rows:
        if row.daylight_lux < controller.settings.threshold_lux:
            below_from = below_from or row.when
        else:
            below_from = None
        if row.lit and below_from is not None:
            break
    assert below_from is not None
    lit_at = next(r.when for r in evening.rows if r.when >= below_from and r.lit)
    assert lit_at - below_from <= timedelta(minutes=20), (
        "het was om %s donker genoeg en de lampen gingen pas om %s aan"
        % (below_from.strftime("%H:%M"), lit_at.strftime("%H:%M"))
    )


def test_the_evening_goes_warm():
    controller, room, daylight, sensor = living_room()
    trace = run_day(controller, room, daylight, sensor, DAY, hours=18,
                    enabled=lambda now: True)
    lit = [r for r in trace.rows if r.lit]
    afternoon = [1e6 / r.mired for r in lit if r.when.hour in (18,)]
    late = [1e6 / r.mired for r in lit if r.when.hour == 22 and r.when.minute >= 30]
    assert afternoon and late
    assert max(late) < min(afternoon), "de avond werd niet warmer"
    assert max(late) < 2700


def test_the_kitchen_evening():
    """29 September again, as a whole evening: empty after the afternoon,
    standby glowing once it gets dark, and full light the moment somebody
    walks in at half past seven."""
    controller, room, daylight, sensor = kitchen()
    walk_in = DAY.replace(hour=19, minute=30)
    trace = run_day(
        controller, room, daylight, sensor, DAY.replace(hour=14), hours=6,
        enabled=lambda now: True,
        motion=lambda now: walk_in <= now < walk_in + timedelta(minutes=3),
    )
    before = trace.between(DAY.replace(hour=19), walk_in)
    assert before.rows and all(r.scene == "standby" and r.lit for r in before.rows), (
        "vóór het binnenlopen stond de standby-gloed niet aan"
    )
    arrival = trace.between(walk_in, walk_in + timedelta(seconds=31)).rows
    assert arrival[0].scene == "adaptive"
    assert arrival[0].transition_s == controller.config.arrival_transition_s
    assert arrival[0].master > 0.20
