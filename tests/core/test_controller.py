"""The room's decisions, rung by rung.

Each test is either one rule of the ladder or one evening in a real house
where a rule turned out to be missing. The docstrings say which, because a
test nobody can connect to a lamp is a test somebody will delete.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from custom_components.calm.core import (
    Action,
    ControllerConfig,
    DayCurve,
    DayCurveConfig,
    DimProfile,
    EmptyAction,
    Lamp,
    LevelMode,
    Override,
    OverrideSet,
    RoomController,
    RoomSettings,
    Scene,
    Why,
)
from custom_components.calm.core.overrides import AutoMode, ColourMode
from custom_components.calm.core.photometry import perceptual_to_device

from .helpers import UTRECHT, make_profile

TZ = ZoneInfo("Europe/Amsterdam")
STEP = 30.0


def at(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 9, day, hour, minute, tzinfo=TZ)


def living_room_profile() -> DimProfile:
    """Twelve spots that barely reach the sensor: about a lux between them,
    like the real living room. What the sensor reads is the daylight."""
    return DimProfile.from_lamps(
        [Lamp(id="spot_%d" % i, full_contribution=0.1) for i in range(12)]
    )


def kitchen_profile() -> DimProfile:
    """Two lamps the sensor does see: about fourteen lux at full."""
    return DimProfile.from_lamps(
        [Lamp(id="lamp_%d" % i, full_contribution=7.0) for i in range(2)]
    )


def room(
    profile: DimProfile = None,
    overrides=(),
    **settings,
) -> RoomController:
    settings.setdefault("threshold_lux", 25.0)
    return RoomController(
        profile=profile or living_room_profile(),
        settings=RoomSettings(**settings),
        day_curve=DayCurve(DayCurveConfig()),
        config=ControllerConfig(latitude=UTRECHT[0], longitude=UTRECHT[1]),
        overrides=OverrideSet(overrides),
    )


def kitchen(**settings) -> RoomController:
    """His kitchen: lamps that reach the sensor, a motion sensor, standby at 20%."""
    settings.setdefault("has_motion_sensor", True)
    settings.setdefault("use_motion", True)
    settings.setdefault("when_empty", EmptyAction.STANDBY)
    settings.setdefault("standby_level", 0.20)
    return room(profile=kitchen_profile(), **settings)


class Clock:
    """Steps a controller through time, feeding a reading when there is one."""

    def __init__(self, controller: RoomController, start: datetime) -> None:
        self.c = controller
        self.now = start
        self.commands = []

    def tick(self, lux=None, seconds: float = STEP):
        command = self.c.step(self.now, lux, seconds)
        self.commands.append((self.now, command))
        self.now += timedelta(seconds=seconds)
        return command

    def run(self, minutes: float, lux=None, every_minutes: float = 5.0):
        """Run for a while, with the sensor speaking every few minutes."""
        last = None
        ticks = int(minutes * 60 / STEP)
        per_reading = max(1, int(every_minutes * 60 / STEP))
        for i in range(ticks):
            reading = lux if (lux is not None and i % per_reading == 0) else None
            last = self.tick(reading)
        return last

    def move(self, detecting: bool = True):
        self.c.motion(self.now, detecting)

    def leave(self, minutes_after: float):
        """Somebody walks out: the sensor clears, and the hold runs down."""
        self.c.motion(self.now, False)
        return self.run(minutes_after)

    def turned_on(self):
        return [c for _, c in self.commands if c.action is Action.TURN_ON]

    def turned_off(self):
        return [c for _, c in self.commands if c.action is Action.TURN_OFF]


def settled_empty(clock: Clock, lux: float, minutes: float = 30.0):
    """A motion room somebody left a while ago."""
    clock.move(True)
    clock.tick(lux)
    clock.move(False)
    clock.run(minutes, lux=lux)


# -- the ordinary room ------------------------------------------------------


def test_a_dark_room_calm_runs_is_lit():
    c = room()
    c.set_enabled(True)
    clock = Clock(c, at(22, 20))
    command = clock.tick(3.0)
    assert command.action is Action.TURN_ON
    assert c.lit
    assert command.diagnostics["scene"] == "adaptive"


def test_enough_daylight_keeps_the_lamps_out_from_the_first_moment():
    """17 September, 17:25: the kitchen switch came on at 81 lux against a
    target of 30. The lamps burned at the floor for two and three quarter
    minutes, went out, and came back again. The wait before switching off is
    there to resist a change while the room runs; it must never delay the
    first answer."""
    c = room()
    c.set_enabled(True)
    clock = Clock(c, at(17, 13))
    clock.run(20, lux=300.0)
    assert not clock.turned_on(), "de lampen gingen aan in een lichte kamer"
    assert c.last_command.diagnostics["reason"] == "daylight_sufficient"


def test_lit_lamps_go_out_for_daylight_only_after_the_wait():
    c = room()
    c.set_enabled(True)
    clock = Clock(c, at(22, 9))
    clock.run(5, lux=3.0)
    assert c.lit
    clock.run(8, lux=400.0)
    assert c.lit, "uit binnen de wachttijd: een wolk die wegtrekt zet nu al het licht uit"
    clock.run(4, lux=400.0)
    assert not c.lit
    assert clock.turned_off()[-1].diagnostics["reason"] == "daylight_off"


def test_the_room_comes_back_after_the_wait_when_nobody_is_there():
    """Without a person in the room the wait still sits out a cloud: twelve
    of twenty-five dips below the line in a real week came back inside ten
    minutes."""
    c = room()
    c.set_enabled(True)
    clock = Clock(c, at(22, 13))
    clock.run(10, lux=300.0)
    assert not c.lit
    # A drop from 300 to 5 is held back one reading in case it was somebody
    # walking past the sensor; the wait starts once the second one agrees.
    clock.run(5, lux=5.0)
    clock.run(8, lux=5.0)
    assert not c.lit, "terug binnen de wachttijd, zonder dat iemand erom vroeg"
    assert c.last_command.diagnostics["reason"] == "waiting_to_return"
    clock.run(4, lux=5.0)
    assert c.lit
    assert clock.turned_on()[-1].diagnostics["reason"] == "daylight_gone"


def test_a_passing_cloud_does_not_finish_the_wait_behind_the_filter_s_back():
    """Found in the day simulation. A cloud dipped the light, the wait
    began, the cloud passed, and the first bright reading was held back by
    the filter as a possible spike. The wait ran out inside that held-back
    reading, and the lamps came on in a room at fifty lux. Coming back unasked
    now needs the freshest reading to agree that it is dark."""
    c = room()
    c.set_enabled(True)
    clock = Clock(c, at(12, 13))
    clock.run(10, lux=300.0)
    assert not c.lit
    clock.run(5, lux=5.0)
    clock.run(8, lux=5.0)
    assert c.last_command.diagnostics["reason"] == "waiting_to_return"
    clock.tick(300.0)
    clock.run(4, lux=None)
    assert not c.lit, "de lampen gingen aan terwijl de laatste meting fel licht zei"


def test_while_waiting_it_says_how_long():
    """A room counting down and a room that has given up look identical from
    a sofa. The countdown is in the diagnostics and in the sentence."""
    c = room()
    c.set_enabled(True)
    clock = Clock(c, at(22, 13))
    clock.run(10, lux=300.0)
    clock.run(8, lux=5.0)
    left = c.last_command.diagnostics.get("returning_in_s")
    assert left is not None and 0 < left < c.config.on_dwell_s
    assert c.explanation("nl").why is Why.RETURNING


def test_a_room_switched_on_in_the_dark_answers_at_once():
    """Even if it went out for daylight earlier in the day. Whatever the
    gate knew belonged to a room that was running; carrying its wait over a
    stretch with Calm switched off is how somebody ends up waiting ten
    minutes in a room that was never lit."""
    c = room()
    c.set_enabled(True)
    clock = Clock(c, at(22, 12))
    clock.run(10, lux=300.0)
    assert not c.lit
    c.set_enabled(False)
    clock.run(6 * 60, lux=300.0)
    clock.run(60, lux=3.0)
    c.set_enabled(True)
    command = clock.tick(3.0)
    assert command.action is Action.TURN_ON, "na het aanzetten in het donker bleef het wachten"


def test_the_line_is_the_same_all_day():
    """The slider shows the daylight against a line. If the line quietly
    moved with the clock, the marker would lie exactly when it matters: at
    half past ten at night the day's own setpoint is a fraction of the line,
    and so it is early in the morning ramp."""
    for hour, minute in ((7, 15), (13, 0), (22, 30)):
        c = room(threshold_lux=25.0)
        c.set_enabled(True)
        clock = Clock(c, at(22, hour, minute))
        clock.run(5, lux=20.0)
        assert c.lit, "om %02d:%02d bleef het uit bij 20 lux onder een lijn van 25" % (hour, minute)
        c2 = room(threshold_lux=25.0)
        c2.set_enabled(True)
        clock2 = Clock(c2, at(22, hour, minute))
        clock2.run(5, lux=30.0)
        assert not c2.lit, "om %02d:%02d ging het aan bij 30 lux boven een lijn van 25" % (hour, minute)


def test_how_bright_caps_the_level():
    c = room(brightness=0.5)
    c.set_enabled(True)
    clock = Clock(c, at(22, 12))
    clock.run(30, lux=1.0)
    assert c.master <= 0.5 + 1e-9


def test_the_evening_setting_is_reached_by_bedtime():
    """30 September: in the kitchen he wanted it brighter by day and found
    it too bright in the evening. The day's setting holds until the colour
    has had its turn, then walks down to the evening one by bedtime."""
    c = room(brightness=0.95, evening_brightness=0.6)
    c.set_enabled(True)
    clock = Clock(c, at(30, 14))
    clock.run(30, lux=1.0)
    assert c.master == pytest.approx(0.95, abs=0.01)
    clock = Clock(c, at(30, 21))
    clock.run(25, lux=1.0)
    assert c.master == pytest.approx(0.95, abs=0.01), "het dimmen begon voor de kleur"
    clock.run(95, lux=1.0)
    assert c.master == pytest.approx(0.6, abs=0.02)


def test_a_restart_carries_on_from_where_the_lamps_are():
    """30 September, 21:57: the first step after a restart treated the
    kitchen as newly lit and took it from 95 to 60 percent in two seconds.
    Found burning, the lamps glide from where they are instead."""
    c = room(brightness=0.95, evening_brightness=0.6)
    c.set_enabled(True)
    c.assume_lit(0.95)
    clock = Clock(c, at(30, 22, 57))
    first = clock.tick(1.0)
    assert first.action is Action.APPLY
    assert c.master > 0.9, "de herstart sprong naar de avondstand"
    clock.run(60, lux=1.0)
    assert c.master == pytest.approx(0.6, abs=0.02)


def test_the_evening_is_never_brighter_than_the_day():
    c = room(brightness=0.5, evening_brightness=0.9)
    c.set_enabled(True)
    clock = Clock(c, at(30, 22, 50))
    clock.run(30, lux=1.0)
    assert c.master <= 0.5 + 1e-9


def test_the_ordinary_step_stays_under_the_limit():
    """The guarantee the whole thing started from: while the room adapts,
    nobody sees it move. Arrivals and switches are allowed to be seen;
    ordinary steps are not."""
    c = room()
    c.set_enabled(True)
    clock = Clock(c, at(22, 14))
    clock.run(10, lux=5.0)
    before = c.master
    for lux in (6, 9, 12, 16, 20, 22, 15, 10, 6):
        command = clock.run(5, lux=float(lux))
        budget = c.config.slew_up_per_minute * 5 * c.config.morning_allowance
        assert abs(c.master - before) <= budget + 1e-9
        before = c.master


# -- nobody in the room -------------------------------------------------------


def test_movement_beats_standby():
    c = kitchen()
    c.set_enabled(True)
    clock = Clock(c, at(29, 20))
    settled_empty(clock, 2.0)
    assert c.master == pytest.approx(0.20)
    clock.move(True)
    command = clock.tick(None)
    assert command.diagnostics["scene"] == "adaptive"
    assert command.transition_s == c.config.arrival_transition_s
    assert c.master > 0.20


def test_an_empty_room_without_standby_fades_out_slowly():
    c = kitchen(when_empty=EmptyAction.OFF)
    c.set_enabled(True)
    clock = Clock(c, at(29, 20))
    settled_empty(clock, 2.0)
    off = clock.turned_off()
    assert off and off[-1].diagnostics["reason"] == "nobody_here"
    assert off[-1].transition_s == c.config.empty_fade_s


def test_standby_waits_for_the_daylight_like_everything_else():
    """Too much light comes before standby: a glow nobody asked for is no
    reason to burn through a bright afternoon."""
    c = kitchen()
    c.set_enabled(True)
    clock = Clock(c, at(29, 13))
    settled_empty(clock, 300.0)
    assert not c.lit


def test_standby_comes_back_on_its_own_when_it_gets_dark():
    """29 September, 18:50 to 19:29. The kitchen was in standby, the sun
    went down, and the glow never came back: the return was decided by
    comparing the standby level with a threshold, which has nothing to do
    with the light. Forty minutes at two lux, with nobody told why."""
    c = kitchen()
    c.set_enabled(True)
    clock = Clock(c, at(29, 17))
    settled_empty(clock, 139.0, minutes=60)
    assert not c.lit
    clock.run(20, lux=2.0)
    assert c.lit, "de standby-gloed kwam niet terug toen het donker werd"
    assert c.master == pytest.approx(0.20)


def test_walking_into_a_dark_room_brings_the_light_now():
    """29 September, 19:29. He walked into a dark kitchen and the lamps came
    on at 19:39, ten minutes later, because the room was sitting out a wait
    meant for clouds. Movement in the dark is the trigger, and it is answered
    in the same moment."""
    c = kitchen(when_empty=EmptyAction.OFF)
    c.set_enabled(True)
    clock = Clock(c, at(29, 17))
    settled_empty(clock, 139.0, minutes=60)
    clock.run(3, lux=2.0)
    assert not c.lit
    clock.move(True)
    command = clock.tick(None)
    assert command.action is Action.TURN_ON, "binnenlopen in het donker gaf geen licht"
    assert command.transition_s == c.config.arrival_transition_s
    assert command.diagnostics["reason"] == "arrival"


def test_walking_in_during_the_wait_ends_it():
    """The other half of 29 September. The kitchen was in standby, the
    daylight had just gone, and the room was sitting out its cloud wait when
    he walked in. Arrival ends the wait on the spot, whatever the room was
    doing before."""
    c = kitchen()
    c.set_enabled(True)
    clock = Clock(c, at(29, 17))
    settled_empty(clock, 139.0, minutes=60)
    assert not c.lit
    clock.run(10, lux=2.0)
    assert c.last_command.diagnostics["reason"] == "waiting_to_return"
    clock.move(True)
    command = clock.tick(None)
    assert command.action is Action.TURN_ON
    assert command.diagnostics["reason"] == "arrival"
    assert command.diagnostics["scene"] == "adaptive"


def test_a_movement_too_short_to_see_still_counts_as_walking_in():
    """The sensor can go on and off again between two ticks: somebody stepped
    into the kitchen for a second. By the time the room looks, nothing is
    moving any more, but somebody did arrive, and the room is theirs for the
    hold that follows."""
    c = kitchen()
    c.set_enabled(True)
    clock = Clock(c, at(29, 17))
    settled_empty(clock, 139.0, minutes=60)
    clock.run(10, lux=2.0)
    assert not c.lit
    clock.move(True)
    clock.move(False)
    command = clock.tick(None)
    assert command.action is Action.TURN_ON, "een korte beweging telde niet als binnenkomen"


def test_somebody_already_in_the_room_gets_the_light_when_the_sun_goes():
    """Cooking at sunset: nobody arrives, they are already there. Moving
    about in a room that has gone dark counts as asking."""
    c = kitchen()
    c.set_enabled(True)
    clock = Clock(c, at(29, 18))
    clock.move(True)
    clock.run(20, lux=300.0)
    assert not c.lit
    clock.move(True)
    clock.tick(4.0)
    clock.move(True)
    command = clock.tick(None)
    assert c.lit
    assert clock.turned_on()[-1].diagnostics["reason"] in ("motion", "arrival")


# -- Calm not running the room ---------------------------------------------


def test_calm_off_means_off():
    c = room()
    c.set_enabled(True)
    clock = Clock(c, at(22, 21))
    clock.run(5, lux=3.0)
    assert c.lit
    c.set_enabled(False, because="er is niemand thuis")
    command = clock.tick(None)
    assert command.action is Action.TURN_OFF
    assert command.diagnostics["rung"] == "inactive"
    assert "niemand thuis" in c.explanation("nl").sentence


def test_movement_always_gives_a_nightlight_at_night():
    """Everybody asleep, somebody fetches a glass of water."""
    c = kitchen(motion_always=True, nightlight_level=0.05, nightlight_kelvin=2000.0)
    c.set_enabled(False, because="de nachtmodus staat aan")
    clock = Clock(c, at(29, 2))
    settled_empty(clock, 0.0)
    assert not c.lit
    clock.move(True)
    command = clock.tick(None)
    assert command.action is Action.TURN_ON
    assert command.diagnostics["scene"] == "nightlight"
    assert c.master == pytest.approx(max(0.05, c.profile.group_floor))
    assert 1_000_000.0 / c.mired == pytest.approx(2000.0, rel=0.01)


def test_the_nightlight_goes_below_the_room_s_lowest_setting():
    """30 September: the kitchen's lowest setting is 20%. The nightlight at
    5% is meant to be below it; held up to 20% it is a lamp at three in the
    morning."""
    profile = DimProfile.from_lamps(
        [Lamp(id="lamp_%d" % i, full_contribution=7.0, floor=0.20) for i in range(2)]
    )
    c = room(profile=profile, has_motion_sensor=True, use_motion=True,
             motion_always=True, nightlight_level=0.05)
    c.set_enabled(False, because="de nachtmodus staat aan")
    clock = Clock(c, at(29, 2))
    settled_empty(clock, 0.0)
    clock.move(True)
    command = clock.tick(None)
    assert command.diagnostics["scene"] == "nightlight"
    assert set(command.brightness.values()) == {perceptual_to_device(0.05)}
    assert c.base_profile.group_floor == pytest.approx(0.20), "de gewone ondergrens verdween"


def test_movement_always_gives_ordinary_light_by_day():
    """Home with a flat phone: Home Assistant thinks nobody is in, but
    somebody is standing in the dark kitchen at eight in the evening."""
    c = kitchen(motion_always=True)
    c.set_enabled(False, because="er is niemand thuis")
    clock = Clock(c, at(29, 20))
    settled_empty(clock, 2.0)
    clock.move(True)
    command = clock.tick(None)
    assert c.lit
    assert command.diagnostics["scene"] == "adaptive"


def test_movement_always_still_respects_the_daylight():
    c = kitchen(motion_always=True)
    c.set_enabled(False)
    clock = Clock(c, at(29, 13))
    settled_empty(clock, 300.0)
    clock.move(True)
    clock.tick(300.0)
    assert not c.lit


def test_without_the_setting_movement_does_nothing_while_calm_is_off():
    c = kitchen(motion_always=False)
    c.set_enabled(False)
    clock = Clock(c, at(29, 2))
    settled_empty(clock, 0.0)
    clock.move(True)
    clock.tick(None)
    assert not c.lit


# -- overrides ----------------------------------------------------------------


WORK = Override(key="werklicht", name="Werklicht", level_mode=LevelMode.FIXED,
                level=0.9, colour_mode=ColourMode.FIXED, kelvin=4000.0)
DINNER = Override(key="eten", name="Eten", auto=AutoMode.TIME,
                  auto_start=time(17, 30), auto_end=time(19, 0), level=0.8)


def test_an_override_you_switched_on_wins_over_daylight():
    c = room(overrides=[WORK])
    c.set_enabled(True)
    clock = Clock(c, at(22, 13))
    clock.run(10, lux=400.0)
    assert not c.lit
    c.set_override("werklicht", True)
    command = clock.tick(None)
    assert c.lit
    assert c.master == pytest.approx(0.9)
    assert command.transition_s == WORK.transition_s


def test_an_override_you_switched_on_works_when_calm_is_off():
    c = room(overrides=[WORK])
    c.set_enabled(False)
    clock = Clock(c, at(22, 21))
    clock.run(5, lux=2.0)
    c.set_override("werklicht", True)
    clock.tick(None)
    assert c.lit


def test_an_override_that_switched_itself_on_steps_aside_for_daylight():
    c = room(overrides=[DINNER])
    c.set_enabled(True)
    c.set_auto_overrides({"eten"})
    clock = Clock(c, at(22, 18))
    clock.run(10, lux=400.0)
    assert not c.lit


def test_an_override_that_switched_itself_on_only_runs_while_calm_runs_the_room():
    c = room(overrides=[DINNER])
    c.set_enabled(False)
    c.set_auto_overrides({"eten"})
    clock = Clock(c, at(22, 18))
    clock.run(5, lux=2.0)
    assert not c.lit


def test_only_one_override_runs_by_hand_and_the_last_one_wins():
    film = Override(key="film", name="Film", level=0.2)
    c = room(overrides=[WORK, film])
    c.set_enabled(True)
    clock = Clock(c, at(22, 21))
    c.set_override("werklicht", True)
    clock.tick(2.0)
    c.set_override("film", True)
    clock.tick(None)
    assert c.active_override.key == "film"
    assert not c.set_override("werklicht", False), "een schakelaar sprak voor een ander"


def test_handing_the_room_back_skips_the_cloud_wait():
    c = room(overrides=[WORK])
    c.set_enabled(True)
    clock = Clock(c, at(22, 13))
    clock.run(10, lux=400.0)
    c.set_override("werklicht", True)
    clock.tick(None)
    clock.run(5, lux=5.0)
    c.set_override("werklicht", False)
    clock.tick(5.0)
    clock.tick(None)
    assert c.lit


# -- a hand on the lamps ------------------------------------------------------


def test_a_hand_on_the_lamps_is_left_alone_for_the_set_time():
    c = room(manual_hold_s=7200.0)
    c.set_enabled(True)
    clock = Clock(c, at(22, 20))
    clock.run(5, lux=3.0)
    c.notify_manual_change()
    clock.run(119, lux=3.0)
    assert all(
        cmd.action is Action.NONE for _, cmd in clock.commands[-200:]
    ), "Calm greep in terwijl iemand de lampen zelf had ingesteld"
    clock.run(2, lux=3.0)
    resumed = [cmd for _, cmd in clock.commands[-5:] if cmd.action is not Action.NONE]
    assert resumed and resumed[0].transition_s == c.config.resume_transition_s


def test_the_hold_can_be_shorter():
    c = room(manual_hold_s=600.0)
    c.set_enabled(True)
    clock = Clock(c, at(22, 20))
    clock.run(5, lux=3.0)
    c.notify_manual_change()
    clock.run(11, lux=3.0)
    assert not c.manual


def test_switched_off_by_hand_stays_off_until_the_room_starts_again():
    c = kitchen()
    c.set_enabled(True)
    clock = Clock(c, at(29, 21))
    clock.move(True)
    clock.run(5, lux=2.0)
    c.notify_external_off()
    clock.move(True)
    clock.run(5, lux=2.0)
    assert not c.lit, "met de hand uitgezet, en toch weer aan terwijl iemand er nog was"
    clock.leave(30)
    clock.move(True)
    clock.tick(None)
    assert c.lit, "na leeglopen en terugkomen bleef het uit"



def test_a_lamp_that_reports_off_after_calm_put_it_out_is_not_a_hand():
    """30 September: Calm put the kitchen out for daylight at 08:34:36, and
    one lamp reported itself off at 08:37:10. Taken as a hand, the kitchen
    was held dark until somebody walked in."""
    c = room()
    c.set_enabled(True)
    clock = Clock(c, at(30, 8))
    clock.run(5, lux=3.0)
    clock.run(13, lux=400.0)
    assert not c.lit
    c.notify_external_off()
    assert not c.manual, "een late melding van de lamp werd een hand"
    clock.run(20, lux=3.0)
    assert c.lit, "het werd weer donker en de lampen bleven uit"


def test_lamps_already_burning_at_a_restart_are_not_taken_for_daylight():
    """30 September, 21:07: after a restart the kitchen lamps were burning
    and Calm counted their light as daylight. With the line below what the
    lamps give the sensor, that reads as a room in daylight at ten at night,
    and the lamps burn on untended."""
    c = room(profile=kitchen_profile(), threshold_lux=5.0)
    c.set_enabled(True)
    lamps = c.estimator.room_output(0.8, True)
    assert lamps > 5.0
    c.assume_lit(0.8)
    clock = Clock(c, at(30, 22))
    clock.tick(lamps)
    clock.run(5)
    assert c.lit, "de lampen werden voor daglicht aangezien"
    assert c.daylight_lux < 1.0

# -- the sensor ---------------------------------------------------------------


def test_a_silent_sensor_counts_as_dark_for_how_bright_as_well():
    """29 September, 06:30. The sensor last spoke at 23:06 and its 0 lux was
    thrown out as a spike, so the kitchen spent the night believing 12 lux.
    Walking in at half six set the lamps from that 12 against a target of
    ten: the floor, in a pitch-dark room, for nineteen minutes."""
    c = kitchen(threshold_lux=10.0)
    c.set_enabled(True)
    clock = Clock(c, at(28, 22))
    settled_empty(clock, 12.0, minutes=60)
    clock.run(7 * 60, lux=None)
    assert c.sensor_stale
    clock.move(True)
    command = clock.tick(None)
    assert c.lit
    assert c.master > c.profile.group_floor * 2, (
        "een zwijgende sensor bepaalde nog steeds hoe fel: de lampen bleven op de ondergrens"
    )


def test_a_silent_sensor_never_holds_a_room_dark():
    c = room()
    c.set_enabled(True)
    clock = Clock(c, at(22, 13))
    clock.run(10, lux=300.0)
    assert not c.lit
    clock.run(60, lux=None)
    assert c.lit
    assert clock.turned_on()[-1].diagnostics["reason"] == "sensor_silent"


# -- the line on the slider ---------------------------------------------------


def test_moving_the_line_is_answered_now():
    c = room(threshold_lux=25.0)
    c.set_enabled(True)
    clock = Clock(c, at(22, 17))
    clock.run(10, lux=40.0)
    assert not c.lit
    c.set_threshold(60.0)
    clock.tick(40.0)
    command = clock.tick(None)
    assert c.lit, "het doel verzetten liet de kamer alsnog wachten"


def test_lowering_the_line_does_not_put_the_room_out_any_faster():
    c = room(threshold_lux=100.0)
    c.set_enabled(True)
    clock = Clock(c, at(22, 13))
    clock.run(5, lux=50.0)
    assert c.lit
    clock.run(8, lux=150.0)
    assert c.lit
    c.set_threshold(10.0)
    clock.run(8, lux=150.0)
    assert c.lit, "de lampen gingen uit omdat iemand aan de schuif zat"
