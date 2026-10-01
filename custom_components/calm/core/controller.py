"""One room: from a sensor reading, a movement and a switch to one command.

The room decides in a fixed order, and the order is the whole design. It is
also what the room says when asked why it is doing something, so it is kept
short enough to hold in your head:

1. **You, by hand.** Somebody set the lamps from the Hue app, a switch or a
   dashboard. Calm steps aside.
2. **An override you switched on.** It runs, whatever the daylight and
   whether or not Calm is running the room: you are standing there and you
   asked.
3. **Enough daylight.** The lamps go out. This comes before everything
   below it, standby included.
4. **Somebody in the room.** Light by the day's rhythm, and an override that
   switched itself on counts here too. Walking into a dark room brings the
   lamps on now, not after a wait.
5. **Nobody in the room.** Standby or off, as the room was told.
6. **Calm is not running the room.** Nobody home, night mode, or a switch
   somebody's own automation turned off. Off, unless movement is set to
   always bring light: a nightlight at night, ordinary light by day.

Everything else in this file is about getting from one of those to the
next without anyone seeing it happen when they should not, and seeing it
happen at once when they should.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, Optional, Set

from .daycurve import DayCurve, Targets
from .estimator import ContributionEstimator
from .explain import Explanation, explain
from .limiter import SlewLimiter
from .mapping import DaylightResponse
from .melanopic import VERTICAL_FRACTION, melanopic_edi
from .overrides import Override, OverrideSet, Source
from .photometry import kelvin_to_mired, perceptual_to_device, perceptual_to_luminous
from .presence import PresenceHold
from .profile import DimProfile
from .sensorfilter import FilterReading, SensorFilter
from .sensorprofile import SensorProfile, SensorWatcher, suggested_filter_tau
from .sun import solar_elevation

#: The nightlight may go below the room's lowest setting, down to the least
#: a lamp shows: one step of Home Assistant's 0..255.
NIGHTLIGHT_FLOOR = 1.0 / 255.0


class EmptyAction(Enum):
    """What a room with a motion sensor does once nobody has been seen."""

    OFF = "off"
    STANDBY = "standby"


@dataclass
class RoomSettings:
    """The handful of things a person chooses for a room.

    Every one of these is on the room's settings screen, in words. Anything
    that is tuning rather than choice lives in ControllerConfig instead.
    """

    #: "Lampen aan als het donkerder is dan": the daylight, in lux at the
    #: sensor, below which the lamps help out. The same all day, so the line
    #: on the slider means the same thing at noon as at nine in the evening.
    threshold_lux: float = 25.0
    #: "Hoe fel overdag": the most the lamps are asked for, 0..1. The day's
    #: rhythm goes under it; it never goes over.
    brightness: float = 1.0
    #: "Hoe fel 's avonds": the same, reached at bedtime and held through
    #: the night. Never more than the daytime setting.
    evening_brightness: float = 0.6
    #: Whether the room has a motion sensor at all. Without one, "occupied"
    #: is always true and nothing below about motion applies.
    has_motion_sensor: bool = False
    #: "Alleen licht als er iemand is".
    use_motion: bool = False
    motion_hold_s: float = 600.0
    when_empty: EmptyAction = EmptyAction.OFF
    standby_level: float = 0.15
    standby_kelvin: float = 2400.0
    #: "Beweging geeft altijd licht": movement lights the room even while
    #: Calm is not running it. The water-glass-at-night case, and the
    #: came-home-with-a-dead-phone case.
    motion_always: bool = False
    nightlight_level: float = 0.05
    nightlight_kelvin: float = 2000.0
    #: How long a hand on the lamps is respected before Calm takes over again.
    manual_hold_s: float = 7200.0


@dataclass
class ControllerConfig:
    """Tuning. Measured or reasoned once, not something to ask anybody."""

    latitude: float
    longitude: float

    #: The comfort guarantee, in perceived brightness per minute, and it is
    #: asymmetric: there is a hurry to add light and never one to remove it.
    slew_up_per_minute: float = 0.030
    slew_down_per_minute: float = 0.010
    #: Colour, in mired per minute. The eye renormalises white far faster
    #: than it adapts to a change in level, so this can be quicker than it
    #: looks. Set so the evening wind-down fits inside its three hours.
    mired_slew_per_minute: float = 8.0
    #: Multiplier on both limits while somebody is moving about: a change is
    #: least noticeable when something else just happened.
    motion_allowance: float = 4.0
    #: And during the wake-up ramp, which is the one change meant to be seen.
    morning_allowance: float = 6.0

    #: How long the lamps take to arrive when somebody walks in, or when the
    #: room lights for a nightlight. Quick enough to be there before they
    #: have crossed the room, slow enough to read as a light coming on.
    arrival_transition_s: float = 2.0
    #: How long the room takes to sink into standby. Nobody asked for it, so
    #: it does not have to hide; it only has to not flash.
    standby_fade_s: float = 90.0
    #: How long the lamps take to fade out behind somebody who left a room
    #: with no standby. Long: nobody should watch the room shut down.
    empty_fade_s: float = 300.0
    #: How long the lamps take to come back when the daylight has gone, at
    #: the level the room needs.
    return_transition_s: float = 4.0
    #: How long they take to go out when the signal says so, and when the
    #: daylight takes over.
    off_transition_s: float = 2.0
    daylight_off_transition_s: float = 30.0
    #: How long Calm takes to take the lamps back after a hand-hold expires.
    resume_transition_s: float = 60.0
    #: How often the loop recomputes, and the transition each ordinary step
    #: carries, so the lamp interpolates and a change is a ramp, not a step.
    interval_s: float = 30.0

    #: The daylight has to be past the line for this long before the lamps
    #: go out, and short of it this long before they come back unasked. The
    #: wait exists to sit out a cloud: in six days of a real house, twelve of
    #: twenty-five dips below the line came back inside ten minutes, nearly
    #: all of them with the sun up. Somebody walking in, or somebody moving
    #: the line, skips the wait, because neither is weather.
    off_dwell_s: float = 600.0
    on_dwell_s: float = 600.0

    #: Below this much change, don't act. Widened while the estimate is poor.
    deadband: float = 0.02
    max_deadband: float = 0.08
    #: How much of a correction to apply per tick, scaled by confidence.
    min_damping: float = 0.05
    max_damping: float = 0.30

    #: A sensor quieter than this, and than a few of its own intervals, has
    #: stopped being evidence. Its last word is a memory then, and a memory
    #: is never allowed to decide the room is bright.
    stale_after_s: float = 900.0
    stale_intervals: float = 3.0

    #: How much of the target the lamps must be able to deliver at the
    #: sensor before the closed loop is trusted over the daylight response.
    min_visibility: float = 0.20
    full_visibility: float = 0.50
    #: How long the lamps may sit at an end stop, still short, before that
    #: counts as "cannot be done here".
    stuck_after_s: float = 1800.0

    match_filter_to_sensor: bool = True


class Scene(Enum):
    """What the lamps are for right now."""

    OFF = "off"
    #: Hands off: somebody set them by hand.
    HANDS_OFF = "hands_off"
    #: The day's rhythm, filling up to the room's target.
    ADAPTIVE = "adaptive"
    OVERRIDE = "override"
    STANDBY = "standby"
    NIGHTLIGHT = "nightlight"


class Rung(Enum):
    """Which rule in the ladder decided. What the room gives as its reason."""

    MANUAL = "manual"
    OVERRIDE = "override"
    DAYLIGHT = "daylight"
    OCCUPIED = "occupied"
    EMPTY = "empty"
    INACTIVE = "inactive"


class Action(Enum):
    NONE = "none"
    APPLY = "apply"
    TURN_ON = "turn_on"
    TURN_OFF = "turn_off"


@dataclass(frozen=True)
class Command:
    """What the Home Assistant side should do with this tick, if anything."""

    action: Action
    master: float
    mired: float
    brightness: Dict[str, int] = field(default_factory=dict)
    transition_s: float = 0.0
    diagnostics: Dict[str, Any] = field(default_factory=dict)

    @property
    def reason(self) -> str:
        return str(self.diagnostics.get("reason", ""))


@dataclass(frozen=True)
class _Want:
    scene: Scene
    rung: Rung
    reason: str
    override: Optional[Override] = None


class RoomController:
    """One room's decisions. Fed by the integration, tested without it."""

    def __init__(
        self,
        profile: DimProfile,
        settings: RoomSettings,
        day_curve: DayCurve,
        config: ControllerConfig,
        estimator: Optional[ContributionEstimator] = None,
        sensor_filter: Optional[SensorFilter] = None,
        mapping: Optional[DaylightResponse] = None,
        overrides: Optional[OverrideSet] = None,
    ) -> None:
        self.base_profile = profile
        self.profile = profile
        self.settings = settings
        self.day_curve = day_curve
        self.config = config
        self.estimator = estimator or ContributionEstimator(profile)
        self.filter = sensor_filter or SensorFilter()
        self.mapping = mapping or DaylightResponse()
        self.overrides = overrides if overrides is not None else OverrideSet()

        self.brightness_limiter = SlewLimiter(
            config.slew_up_per_minute, config.slew_down_per_minute
        )
        self.colour_limiter = SlewLimiter(config.mired_slew_per_minute)
        self.presence = PresenceHold(settings.motion_hold_s)
        self.watcher = SensorWatcher()

        #: What the lamps are doing, as far as we asked them.
        self.master = 0.0
        self.mired: Optional[float] = None
        self.lit = False

        self._enabled = False
        self._inactive_because: Optional[str] = None
        self._user_override: Optional[str] = None
        self._auto_overrides: Set[str] = set()
        self._manual_for: Optional[float] = None
        #: The lamps were switched off by hand. Respected until the room
        #: starts again: the signal comes back, or somebody arrives after it
        #: was empty.
        self._switched_off = False

        #: The daylight gate. Separate from the scene, because it holds over
        #: whichever scene is running: a room that went dark for daylight in
        #: standby is still dark for daylight when somebody walks in.
        self._daylight_off = False
        self._bright_for = 0.0
        self._dark_for = 0.0
        #: A person asked for light: moved the line, pressed an override off,
        #: or walked in. Ends the wait before coming back, once.
        self._asked = False

        self._scene: Optional[_Want] = None
        #: The lamps were found burning at the start and taken over as they
        #: were; see assume_lit.
        self._adopted = False
        self._scene_changed = False
        #: How the next change of scene should arrive, when something other
        #: than the scene itself decides it: a person asking, Calm taking the
        #: lamps back after a hand-hold, the daylight going unasked.
        self._pending_transition: Optional[float] = None
        self._pending_reason: Optional[str] = None
        self._occupied_before = True
        self._reading: Optional[FilterReading] = None
        self._silent_for = 0.0
        self._master_at_last_reading = 0.0
        self._now: Optional[datetime] = None
        self._last: Optional[Command] = None
        self._pinned_for = 0.0
        self._allowance = 1.0
        self.unreachable: Optional[str] = None

    # -- what the world tells us --------------------------------------------

    def set_enabled(self, enabled: bool, because: Optional[str] = None) -> None:
        """Whether Calm runs this room right now, and if not, why not.

        ``because`` is only for the explanation: "nobody is home" says more
        than "the signal is off".
        """
        if enabled and not self._enabled:
            # A fresh yes clears a hand switch-off: the household's own logic
            # has spoken again, and that outranks a gesture from earlier.
            self._switched_off = False
            self._manual_for = None
        self._enabled = enabled
        self._inactive_because = None if enabled else because

    @property
    def enabled(self) -> bool:
        return self._enabled

    def motion(self, when: datetime, detecting: bool) -> None:
        """The motion sensor changed. Arrival is decided on the next step."""
        self.presence.seen(when, detecting)

    def set_override(self, key: str, active: bool) -> bool:
        """Somebody flipped an override's switch. Says whether that changed anything.

        At most one runs by hand, and turning on a second takes over from the
        first: there is no answer to what a room should do when two
        arrangements are asserted at once, and "the last one wins" is the
        only rule somebody can predict. Turning off an override that is not
        the running one does nothing; a switch speaks only for itself.
        """
        if active:
            if self.overrides.get(key) is None or self._user_override == key:
                return False
            self._user_override = key
            return True
        if self._user_override != key:
            return False
        self._user_override = None
        # Handing the room back is a person asking for the ordinary room. If
        # that room is dark and waiting out a cloud, it has waited enough.
        self._asked = True
        return True

    def set_auto_overrides(self, keys: Set[str]) -> None:
        """Which overrides currently want to switch themselves on."""
        self._auto_overrides = {key for key in keys if key in self.overrides}

    def set_threshold(self, lux: float) -> None:
        """Somebody moved the line. Answer now, not after the cloud wait.

        Only that direction: lowering it does not put the lamps out any
        faster than usual. There is never a hurry to take light away, and a
        room that goes dark the instant somebody touches a slider teaches
        them not to touch it.
        """
        self.settings.threshold_lux = max(0.5, float(lux))
        self._asked = True
        self._bright_for = 0.0

    def set_brightness(self, level: float) -> None:
        self.settings.brightness = min(1.0, max(0.05, float(level)))
        self._scene_changed = True
        self._pending_transition = self.config.arrival_transition_s

    def set_evening_brightness(self, level: float) -> None:
        self.settings.evening_brightness = min(1.0, max(0.05, float(level)))
        self._scene_changed = True
        self._pending_transition = self.config.arrival_transition_s

    @property
    def brightness_cap(self) -> float:
        """The most the lamps are asked for right now: the daytime setting
        by day, the evening one by bedtime, and a slow walk between them."""
        day = self.settings.brightness
        evening = min(self.settings.evening_brightness, day)
        if self._now is None:
            return day
        return evening + (day - evening) * self.day_curve.day_fraction(self._now)

    def notify_manual_change(self) -> None:
        """Somebody set a level by hand. Leave the room alone for a while."""
        self._manual_for = 0.0

    def notify_external_off(self) -> None:
        """The lamps went out without us asking. They stay out.

        Only when we had them on: out when they were meant to be out already
        is agreement, not a hand, however late the lamp says so. Taken as a
        hand it held the room dark until somebody walked in, and a room
        without a motion sensor would have stayed dark all evening.
        """
        if not self.lit:
            return
        self._switched_off = True
        self._manual_for = None
        self.master = 0.0
        self.lit = False

    def assume_lit(self, master: float, mired: Optional[float] = None) -> None:
        """The room's lamps were already burning when Calm started.

        Without this their light counts as daylight. 30 September, 21:07:
        after a restart the kitchen lamps gave the sensor 13 lux, and Calm
        took all of it for daylight. With the line below that, a restart at
        night reads as a room in daylight, and the lamps burn on untended.
        """
        if self.lit or master <= 0.0:
            return
        self.lit = True
        self.master = min(1.0, master)
        self._master_at_last_reading = self.master
        if mired:
            self.mired = mired
        # And carry on from there under the limit. 30 September, 21:57: the
        # first step after a restart treated the room as newly lit and put
        # the kitchen from 95 to 60 percent in two seconds.
        self._adopted = True

    def release_manual(self) -> None:
        """Take the room back now rather than when the hold runs out."""
        self._manual_for = None
        self._switched_off = False
        self._scene_changed = True
        self._pending_transition = self.config.arrival_transition_s

    # -- reading the room -------------------------------------------------------

    @property
    def manual(self) -> bool:
        return self._manual_for is not None or self._switched_off

    @property
    def manual_remaining_s(self) -> Optional[float]:
        if self._manual_for is None:
            return None
        return max(0.0, self.settings.manual_hold_s - self._manual_for)

    @property
    def active_override(self) -> Optional[Override]:
        want = self._scene
        return want.override if want and want.scene is Scene.OVERRIDE else None

    @property
    def user_override_key(self) -> Optional[str]:
        return self._user_override

    @property
    def occupied(self) -> bool:
        if not self.settings.has_motion_sensor:
            return True
        return self.presence.occupied(self._now) if self._now else True

    @property
    def daylight_lux(self) -> float:
        return self.estimator.daylight_lux

    @property
    def sensor_profile(self) -> SensorProfile:
        return self.watcher.profile()

    @property
    def sensor_stale(self) -> bool:
        """Whether the sensor has been quiet too long to be believed.

        Its last reading does not expire as a number; it expires as evidence.
        Measured against the sensor's own rhythm as well as the clock: one
        that speaks every eleven minutes in the dark is not silent at twenty.
        """
        cfg = self.config
        interval = self.sensor_profile.interval_s
        patience = max(cfg.stale_after_s, interval * cfg.stale_intervals)
        return self._silent_for > patience

    @property
    def last_command(self) -> Optional[Command]:
        return self._last

    # -- the loop ---------------------------------------------------------------

    def step(
        self, now: datetime, measured_lux: Optional[float], dt_seconds: float
    ) -> Command:
        """One tick.

        ``measured_lux`` is ``None`` when the sensor has said nothing since
        the last tick, which is the usual case. That distinction matters:
        feeding the same reading in ten times while the lamps change under it
        tells the estimator ten times over that the lamps do nothing.
        """
        self._now = now
        self._take_reading(now, measured_lux, dt_seconds)
        if self._reading is None:
            # Nothing ever heard from the sensor. Guessing the daylight would
            # be guessing; wait for the first word.
            return self._remember(Command(
                action=Action.NONE, master=self.master, mired=self.mired or 400.0,
                diagnostics={"reason": "no_reading_yet", "rung": None},
            ))

        elevation = solar_elevation(now, self.config.latitude, self.config.longitude)
        stale = self.sensor_stale
        # A quiet sensor has stopped being evidence for *how bright* as much
        # as for *whether*. Unknown is read as dark: nothing is saying the
        # target is met, so the lamps go and meet it.
        daylight = 0.0 if stale else self.estimator.daylight_lux

        arriving = self._arrival()
        if self._manual_for is not None:
            self._manual_for += dt_seconds
            if self._manual_for >= self.settings.manual_hold_s:
                # Taking the lamps back from a person is visible either way.
                # A minute reads as Calm settling back in, not as a switch.
                self._manual_for = None
                self._scene_changed = True
                self._pending_transition = self.config.resume_transition_s

        want = self._choose(now, arriving)
        self._settle(want)
        targets = self._targets(now, elevation, want)

        if want.scene in (Scene.HANDS_OFF, Scene.OFF):
            # Whatever the daylight gate knew was about a room that was
            # running. When it runs again the first verdict comes fresh, at
            # once; carrying the old wait over is how a person walked into a
            # dark kitchen on 29 September and waited ten minutes.
            self._reset_gate()
        if want.scene is Scene.HANDS_OFF:
            return self._idle(targets, daylight, want, "manual")
        if want.scene is Scene.OFF:
            return self._go_dark(targets, daylight, want)

        exempt = want.scene is Scene.OVERRIDE and self._user_override is not None
        if not exempt:
            gate = self._daylight_gate(
                targets, daylight, want, dt_seconds, arriving, stale,
                self._reading.saturated,
            )
            if gate is not None:
                return gate

        return self._light(targets, daylight, want, dt_seconds, arriving)

    # -- the ladder -------------------------------------------------------------

    def _choose(self, now: datetime, arriving: bool) -> _Want:
        if self._switched_off:
            if arriving:
                # The room emptied and somebody came back: it is starting
                # again, and a hand switch-off from before does not carry over.
                self._switched_off = False
            else:
                return _Want(Scene.OFF, Rung.MANUAL, "switched_off_by_hand")
        if self._manual_for is not None:
            return _Want(Scene.HANDS_OFF, Rung.MANUAL, "manual")

        chosen = self.overrides.get(self._user_override)
        if chosen is not None:
            return _Want(Scene.OVERRIDE, Rung.OVERRIDE, "override", chosen)

        occupied = self.occupied
        if self._enabled:
            auto = next(
                (o for o in self.overrides if o.key in self._auto_overrides), None
            )
            if auto is not None:
                return _Want(Scene.OVERRIDE, Rung.OVERRIDE, "override_auto", auto)
            if not (self.settings.has_motion_sensor and self.settings.use_motion):
                return _Want(Scene.ADAPTIVE, Rung.OCCUPIED, "adapting")
            if occupied:
                return _Want(Scene.ADAPTIVE, Rung.OCCUPIED, "adapting")
            if self.settings.when_empty is EmptyAction.STANDBY:
                return _Want(Scene.STANDBY, Rung.EMPTY, "standby")
            return _Want(Scene.OFF, Rung.EMPTY, "nobody_here")

        if (
            self.settings.motion_always
            and self.settings.has_motion_sensor
            and self.presence.known
            and occupied
        ):
            if self.day_curve.is_night(now):
                return _Want(Scene.NIGHTLIGHT, Rung.INACTIVE, "nightlight")
            return _Want(Scene.ADAPTIVE, Rung.INACTIVE, "motion_while_off")
        return _Want(Scene.OFF, Rung.INACTIVE, "signal_off")

    def _settle(self, want: _Want) -> None:
        """Note whether what the room is for has changed since last tick."""
        before = self._scene
        if before is None or before.scene is not want.scene or (
            (before.override.key if before.override else None)
            != (want.override.key if want.override else None)
        ):
            # Lamps taken over at a start are not a room being lit: a restart
            # should be invisible.
            self._scene_changed = not (before is None and self._adopted)
            if want.override is not None:
                profile = want.override.profile_from(self.base_profile)
            elif want.scene is Scene.NIGHTLIGHT:
                # Below the room's lowest setting, as low as a lamp can glow.
                profile = self.base_profile.with_floor(NIGHTLIGHT_FLOOR)
            else:
                profile = self.base_profile
            if profile is not self.profile:
                self.profile = profile
                if profile.active:
                    self.estimator.rebind(profile)
        self._scene = want

    def _arrival(self) -> bool:
        """Whether somebody just came into a room that was empty."""
        if not self.settings.has_motion_sensor or self._now is None:
            return False
        occupied = self.presence.occupied(self._now) if self.presence.known else True
        arrived = occupied and not self._occupied_before
        self._occupied_before = occupied
        return arrived

    # -- daylight ---------------------------------------------------------------

    def _dark(self, daylight: float, stale: bool) -> bool:
        """Whether the room is darker than the line on the slider.

        One test, whatever the room is doing. The line means the same thing
        in standby as in full light, so the slider can show where the room is
        against it and be right.
        """
        return stale or daylight < self.settings.threshold_lux

    def _daylight_gate(
        self,
        targets: Targets,
        daylight: float,
        want: _Want,
        dt_seconds: float,
        arriving: bool,
        stale: bool,
        saturated: bool,
    ) -> Optional[Command]:
        """Keep the lamps out while the daylight is doing the job.

        Returns a command when the gate decides, ``None`` to carry on and
        light the room.
        """
        cfg = self.config
        dark = self._dark(daylight, stale) and not saturated
        person = arriving or self._occupied_and_moving(want)
        if person and not dark and not self.lit and self._reading is not None:
            # Somebody is standing in the room, so they get the most recent
            # evidence rather than the averaged one. The filter holds back a
            # sudden drop for one reading in case it is a passer-by; that is
            # the right call for a room nobody is in, and the wrong one for
            # somebody who just walked into it after the curtains closed.
            # With the lamps out, the raw reading is the daylight.
            raw = self._reading.raw
            dark = raw < self.settings.threshold_lux and not self._reading.saturated

        if self._daylight_off:
            if not dark:
                self._dark_for = 0.0
                self._asked = False
                return self._idle(targets, daylight, want, "daylight_sufficient",
                                  rung=Rung.DAYLIGHT)
            # Nobody waits out a cloud for a person standing in the room, and
            # a sensor that has gone quiet cannot hold the room dark.
            if stale:
                self._come_back(True, "sensor_silent")
                return None
            if person:
                self._come_back(True, "arrival" if arriving else "motion")
                return None
            if self._asked:
                self._come_back(True, "asked")
                return None
            if self._freshest_says_bright():
                # Coming back unasked is the one change nobody requested, so
                # it waits for the freshest word to agree as well as the
                # averaged one. The filter holds back a sudden rise for a
                # reading just as it holds back a drop; without this, a cloud
                # that passed would finish the wait during that held-back
                # reading, and the lamps came on in a room at fifty lux.
                self._dark_for = 0.0
                return self._idle(
                    targets, daylight, want, "waiting_to_return", rung=Rung.DAYLIGHT,
                    returning_in_s=cfg.on_dwell_s,
                )
            self._dark_for += dt_seconds
            if self._dark_for < cfg.on_dwell_s:
                return self._idle(
                    targets, daylight, want, "waiting_to_return", rung=Rung.DAYLIGHT,
                    returning_in_s=cfg.on_dwell_s - self._dark_for,
                )
            self._come_back(False, "daylight_gone")
            return None

        if dark:
            self._bright_for = 0.0
            return None

        if not self.lit:
            # Nothing is lit yet, and the daylight already does the job. The
            # verdict is given now, not after ten minutes of lamps burning in
            # a bright room: the wait is there to resist a change while the
            # room is running, never to delay the first answer.
            self._daylight_off = True
            self._dark_for = 0.0
            return self._idle(targets, daylight, want, "daylight_sufficient",
                              rung=Rung.DAYLIGHT)

        self._bright_for += dt_seconds
        if self._bright_for < cfg.off_dwell_s:
            return None
        self._daylight_off = True
        self._bright_for = 0.0
        self._dark_for = 0.0
        return self._turn_off(targets, daylight, want, "daylight_off",
                              cfg.daylight_off_transition_s, rung=Rung.DAYLIGHT)

    def _freshest_says_bright(self) -> bool:
        """Whether the latest raw reading is past the line.

        Only asked while the lamps are out, when the raw reading is the
        daylight and nothing of ours is in it.
        """
        reading = self._reading
        if reading is None or self.lit or reading.saturated:
            return False
        return reading.raw >= self.settings.threshold_lux

    def _occupied_and_moving(self, want: _Want) -> bool:
        """Somebody moving about in a room that uses its motion sensor.

        Covers the person who was already there when the light went: they
        did not arrive, but they are standing in it.
        """
        return (
            self.settings.has_motion_sensor
            and self.presence.detecting
            and want.scene in (Scene.ADAPTIVE, Scene.OVERRIDE)
        )

    def _come_back(self, by_person: bool, reason: str) -> None:
        cfg = self.config
        self._reset_gate()
        self._scene_changed = True
        self._pending_reason = reason
        # Somebody is waiting for it: quick. The daylight went on its own:
        # a little slower, a light coming on rather than a switch.
        self._pending_transition = (
            cfg.arrival_transition_s if by_person else cfg.return_transition_s
        )

    def _reset_gate(self) -> None:
        self._daylight_off = False
        self._dark_for = 0.0
        self._bright_for = 0.0
        self._asked = False

    # -- lighting ---------------------------------------------------------------

    def _light(
        self,
        targets: Targets,
        daylight: float,
        want: _Want,
        dt_seconds: float,
        arriving: bool,
    ) -> Command:
        cfg = self.config
        level, mired, fixed = self._aim(targets, daylight, want)
        self._watch_for_stuck(targets, level, dt_seconds, fixed)

        if not self.lit or self._scene_changed:
            transition = self._entry_transition(want)
            reason = self._pending_reason or _reason(want, arriving, self.lit)
            self._scene_changed = False
            self._pending_transition = None
            self._pending_reason = None
            self.master = level
            self.mired = mired
            was_lit, self.lit = self.lit, True
            action = Action.APPLY if was_lit else Action.TURN_ON
            return self._command(action, targets, daylight, want, reason,
                                 transition=transition)

        if fixed:
            # A set level has nothing to adapt: only the colour of a scene
            # that follows the day moves, and that under the limit.
            self.master = level
            self.mired = self._slew_colour(mired, dt_seconds, 1.0)
            return self._command(Action.APPLY, targets, daylight, want, want.reason)

        confidence = self.estimator.confidence
        deadband = cfg.deadband + (cfg.max_deadband - cfg.deadband) * (1.0 - confidence)
        desired = level if abs(level - self.master) >= deadband else self.master
        damping = cfg.min_damping + (cfg.max_damping - cfg.min_damping) * confidence
        proposed = self.master + damping * (desired - self.master)
        if want.scene is Scene.ADAPTIVE:
            # "Hoe fel" is a ceiling, not a reading to be doubted: the dead
            # band that keeps the lamps from chasing sensor noise left them
            # seven percent above the evening setting all night. Above it,
            # the lamps come down to it, at the rate limit and no faster.
            proposed = min(proposed, max(self.profile.group_floor, self.brightness_cap))

        allowance = 1.0
        if self.settings.has_motion_sensor and self.presence.detecting:
            allowance = cfg.motion_allowance
        if self.day_curve.in_morning_ramp(self._now):
            allowance = max(allowance, cfg.morning_allowance)
        self._allowance = allowance
        self.master = self.brightness_limiter.step(
            self.master, proposed, dt_seconds, allowance
        )
        self.mired = self._slew_colour(mired, dt_seconds, allowance)
        return self._command(Action.APPLY, targets, daylight, want, want.reason)

    def _slew_colour(self, wanted: float, dt_seconds: float, allowance: float) -> float:
        if self.mired is None:
            return wanted
        return self.colour_limiter.step(self.mired, wanted, dt_seconds, allowance)

    def _aim(self, targets: Targets, daylight: float, want: _Want):
        """The level and colour this scene wants, and whether the level is set."""
        floor = self.profile.group_floor
        if want.scene is Scene.STANDBY:
            level = self.settings.standby_level
            return max(floor, level), kelvin_to_mired(self.settings.standby_kelvin), True
        if want.scene is Scene.NIGHTLIGHT:
            level = self.settings.nightlight_level
            return max(floor, level), kelvin_to_mired(self.settings.nightlight_kelvin), True
        if want.scene is Scene.OVERRIDE and want.override.fixed_level is not None:
            return max(floor, want.override.fixed_level), targets.mired, True

        wanted = self._desired_master(targets, daylight)
        if want.scene is Scene.ADAPTIVE:
            wanted = min(wanted, self.brightness_cap)
        return max(floor, min(1.0, wanted)), targets.mired, False

    def _entry_transition(self, want: _Want) -> float:
        """How long the lamps take to arrive at a new scene.

        Decided by why the scene changed as much as by what it changed to.
        Somebody walking in, asking, or switching something: quick. The room
        sinking into standby because it emptied: slow, because nobody asked.
        """
        cfg = self.config
        if want.scene is Scene.STANDBY:
            return cfg.standby_fade_s
        if want.scene is Scene.OVERRIDE:
            return want.override.transition_s
        if self._pending_transition is not None:
            return self._pending_transition
        return cfg.arrival_transition_s

    def _desired_master(self, targets: Targets, daylight: float) -> float:
        """Both control laws, blended by how well the sensor sees the lamps.

        Which one is right is a fact about where the sensor hangs, and the
        estimator already knows how much light the lamps put on it.
        """
        closed = self.estimator.master_for(max(targets.lux - daylight, 0.0))
        closed = 0.0 if closed is None else min(1.0, closed)
        if not self.mapping.learned:
            # A prior is not an opinion. Until the response line has been
            # taught something, the closed loop runs alone, and when the
            # target is beyond the lamps it answers what a person would: all
            # the way up.
            return closed
        open_loop = self.mapping.master_for(daylight)
        cfg = self.config
        span = max(cfg.full_visibility - cfg.min_visibility, 1e-6)
        weight = (self._visibility(targets.lux) - cfg.min_visibility) / span
        weight = max(0.0, min(1.0, weight))
        weight = weight * weight * (3.0 - 2.0 * weight)
        return weight * closed + (1.0 - weight) * open_loop

    def _visibility(self, target_lux: float) -> float:
        if target_lux <= 0:
            return 1.0
        return self.estimator.full_contribution / target_lux

    def _targets(self, now: datetime, elevation: float, want: _Want) -> Targets:
        """The day's setpoint, bent by an override if one runs.

        The day curve's lux is the fill-up level, which the evening lowers;
        the on/off line is the slider and stays put.
        """
        adaptive = self.day_curve.targets(
            now, elevation, self.settings.threshold_lux, lit_fraction=self._lit_fraction()
        )
        override = want.override if want.scene is Scene.OVERRIDE else None
        if override is None:
            return adaptive
        lux = override.target_lux(adaptive.lux)
        lux = adaptive.lux if lux is None else lux
        mired = override.target_mired(adaptive.mired)
        return Targets(
            lux=lux, mired=mired,
            melanopic_edi=melanopic_edi(lux, 1_000_000.0 / mired) * VERTICAL_FRACTION,
        )

    def _lit_fraction(self) -> float:
        """How lit the room is against its line, daylight and all.

        A ratio, so the sensor's scale cancels; daylight in the numerator, so
        a bright morning opens the comfort ceiling rather than closing it.
        """
        lit = self.estimator.daylight_lux + (
            self.estimator.full_contribution * perceptual_to_luminous(self.master)
        )
        return lit / max(self.settings.threshold_lux, 1e-6)

    # -- going dark -------------------------------------------------------------

    def _go_dark(self, targets: Targets, daylight: float, want: _Want) -> Command:
        if not self.lit:
            return self._idle(targets, daylight, want, want.reason)
        if want.reason == "switched_off_by_hand":
            # They are already off; we are only catching up.
            self.lit = False
            self.master = 0.0
            return self._idle(targets, daylight, want, want.reason)
        transition = (
            self.config.empty_fade_s if want.reason == "nobody_here"
            else self.config.off_transition_s
        )
        return self._turn_off(targets, daylight, want, want.reason, transition)

    def _turn_off(
        self, targets: Targets, daylight: float, want: _Want, reason: str,
        transition: float, rung: Optional[Rung] = None,
    ) -> Command:
        self.lit = False
        self.master = 0.0
        self._scene_changed = True
        return self._remember(Command(
            action=Action.TURN_OFF,
            master=0.0,
            mired=self.mired or targets.mired,
            transition_s=transition,
            diagnostics=self._diagnostics(targets, daylight, want, reason, rung),
        ))

    # -- noticing the room cannot be done ---------------------------------------

    def _watch_for_stuck(
        self, targets: Targets, level: float, dt_seconds: float, fixed: bool
    ) -> None:
        """Say when the lamps have run out of room, and which way.

        Two different failures. Either the lamps sit at an end stop and the
        room is still wrong, or they barely reach this sensor at all, whatever
        the target. The second means the sensor is in the wrong place for
        steering by, and whoever set the room up should hear it.
        """
        if fixed:
            self._pinned_for = 0.0
            return
        if (
            self.estimator.observations >= 6
            and self._visibility(targets.lux) < self.config.min_visibility
        ):
            self.unreachable = "no_authority"
            return
        floor = self.profile.group_floor
        at_top = self.master >= 0.995 and level >= 0.999
        at_bottom = self.master <= floor + 1e-6 and level <= floor + 1e-6
        if not (at_top or at_bottom):
            self._pinned_for = 0.0
            self.unreachable = None
            return
        self._pinned_for += dt_seconds
        if self._pinned_for >= self.config.stuck_after_s:
            self.unreachable = "too_dim" if at_top else "too_bright"

    # -- the sensor -------------------------------------------------------------

    def _take_reading(
        self, now: datetime, measured_lux: Optional[float], dt_seconds: float
    ) -> None:
        if measured_lux is None:
            self._silent_for += dt_seconds
            return
        # A change we made ourselves is not an outlier, and it is the one
        # sample worth most to the estimator.
        expect_change = abs(self.master - self._master_at_last_reading) > 0.05
        self._reading = self.filter.update(
            measured_lux, dt_seconds + self._silent_for, expect_change=expect_change
        )
        self.estimator.update(
            measured_lux=self._reading.prompt,
            smoothed_lux=self._reading.lux,
            master=self.master,
            lights_on=self.lit,
            dt_seconds=dt_seconds + self._silent_for,
            trustworthy=not self._reading.saturated,
            now=now,
        )
        self._master_at_last_reading = self.master
        self._silent_for = 0.0
        self.watcher.observe(now, measured_lux)
        if self.config.match_filter_to_sensor and self.watcher.ready:
            wanted = suggested_filter_tau(self.sensor_profile)
            if abs(wanted - self.filter.tau_seconds) > 30.0:
                self.filter.tau_seconds = wanted

    # -- commands ---------------------------------------------------------------

    def _command(
        self, action: Action, targets: Targets, daylight: float, want: _Want,
        reason: str, transition: Optional[float] = None,
    ) -> Command:
        levels = self.profile.levels(self.master)
        return self._remember(Command(
            action=action,
            master=self.master,
            mired=self.mired if self.mired is not None else targets.mired,
            brightness={
                lamp_id: perceptual_to_device(level) for lamp_id, level in levels.items()
            },
            transition_s=transition if transition is not None else self.config.interval_s,
            diagnostics=self._diagnostics(targets, daylight, want, reason),
        ))

    def _idle(
        self, targets: Targets, daylight: float, want: _Want, reason: str,
        rung: Optional[Rung] = None, **extra: Any,
    ) -> Command:
        diagnostics = self._diagnostics(targets, daylight, want, reason, rung)
        diagnostics.update(extra)
        return self._remember(Command(
            action=Action.NONE,
            master=self.master,
            mired=self.mired if self.mired is not None else targets.mired,
            diagnostics=diagnostics,
        ))

    def _diagnostics(
        self, targets: Targets, daylight: float, want: _Want, reason: str,
        rung: Optional[Rung] = None,
    ) -> Dict[str, Any]:
        return {
            "reason": reason,
            "rung": (rung or want.rung).value,
            "scene": want.scene.value,
            "mode": want.override.name if want.override else None,
            "target_lux": targets.lux,
            "threshold_lux": self.settings.threshold_lux,
            "target_mired": targets.mired,
            "daylight_lux": daylight,
            "melanopic_edi": targets.melanopic_edi,
            "full_contribution": self.estimator.full_contribution,
            "confidence": self.estimator.confidence,
            "visibility": self._visibility(targets.lux),
            "allowance": self._allowance,
            "unreachable": self.unreachable,
            "stale": self.sensor_stale,
            "occupied": self.occupied,
            "manual_remaining_s": self.manual_remaining_s,
            "inactive_because": self._inactive_because,
        }

    def _remember(self, command: Command) -> Command:
        self._last = command
        return command

    # -- explaining ------------------------------------------------------------

    def explanation(self, language: str = "nl") -> Explanation:
        """Why the light is the way it is, in a sentence."""
        command = self._last
        diagnostics = dict(command.diagnostics) if command else {}
        now = self._now or datetime.now()
        return explain(
            now=now,
            diagnostics=diagnostics,
            lit=self.lit,
            kelvin=1_000_000.0 / (self.mired or 400.0),
            bed=self.day_curve.config.bed,
            morning=self.day_curve.in_morning_ramp(now) if self._now else False,
            night=self.day_curve.is_night(now) if self._now else False,
            has_motion_sensor=self.settings.has_motion_sensor,
            standby_level=self.settings.standby_level,
            language=language,
        )


def _reason(want: _Want, arriving: bool, was_lit: bool) -> str:
    if want.scene is Scene.ADAPTIVE and arriving:
        return "arrival"
    if not was_lit and want.scene is Scene.ADAPTIVE:
        return "on"
    return want.reason
