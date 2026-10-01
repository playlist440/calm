"""What the room should be, as a function of the time of day.

Brightness and colour come out of here together, because they are not two
decisions. They are one — see :mod:`calm_core.melanopic` — and the quantity
that ties them is melanopic EDI, the light as the body clock sees it.

So the schedule is written in melanopic terms and the visible settings fall
out of it. Over the day the target walks from the daytime figure down to the
evening one and then to the night one, and at each point the solver asks for
the warmest colour that delivers it, dimming only once the lamps are as warm
as they go. The result is a room that warms through the evening and only
starts losing brightness near the end — which is what an evening actually
looks like, and is not something anyone had to draw by hand.

``sun`` earns its keep in one place: it caps how cool the lamps may be while
the sun is down. Daylight white at four in the afternoon in December matches
nothing outside the window and reads as harsh, however well it scores.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, time

from .melanopic import (
    DAYTIME_EDI,
    EVENING_EDI,
    NIGHT_EDI,
    VERTICAL_FRACTION,
    colour_then_brightness,
    melanopic_edi,
)
from .photometry import kelvin_to_mired


@dataclass(frozen=True)
class Targets:
    """What the room is aiming at right now."""

    lux: float
    mired: float
    #: The melanopic figure this pair delivers, at the eye. Carried along so
    #: it can be shown: it is the number the schedule is actually about, and
    #: the only one that explains why the evening looks as it does.
    melanopic_edi: float = 0.0

    @property
    def kelvin(self) -> float:
        return 1_000_000.0 / self.mired


@dataclass
class DayCurveConfig:
    """The rhythm, and how strictly to follow it."""

    wake: time = time(7, 0)
    bed: time = time(23, 0)
    #: How long after waking the room reaches its daytime setting.
    morning_ramp_hours: float = 2.5
    #: How long before bed the wind-down starts. Three hours is the figure
    #: in the recommendation.
    evening_ramp_hours: float = 3.0

    #: Range the lamps can reach.
    warm_kelvin: float = 2200.0
    cool_kelvin: float = 5000.0

    daytime_edi: float = DAYTIME_EDI
    evening_edi: float = EVENING_EDI
    night_edi: float = NIGHT_EDI

    #: How far to follow the recommendation, from 0 (ignore it, one setting
    #: all day) to 1 (follow it). Left at 1: the evening it produces is warm
    #: and still perfectly bright, because colour does the work before
    #: brightness is asked to.
    strictness: float = 1.0

    #: Coolest the lamps may be once the sun is well down. Faded in across
    #: the horizon rather than switched: applied as a step it moves the
    #: colour fifty mired in the minute the sun sets, which is one of the
    #: few things in this system you would genuinely see happen.
    cool_cap_after_dark_kelvin: float = 4000.0
    cap_fades_between_elevation: tuple = (3.0, -3.0)
    #: How long after bedtime the room takes to reach the night setting.
    #: Long enough that the dimming stays inside the rate limit: the drop
    #: from the evening figure to the night one is most of the range, and at
    #: one percent a minute that is an hour's worth of moving.
    night_ramp_hours: float = 1.5
    #: How long after waking the brightness climbs from the evening setting
    #: back to the daytime one. Short: the morning is when bright light does
    #: the body clock most good, and the evening setting is for eyes that
    #: have spent hours in the dark, which by breakfast they have not.
    morning_rise_hours: float = 0.5

    def __post_init__(self) -> None:
        if self.warm_kelvin >= self.cool_kelvin:
            raise ValueError("warm_kelvin must be below cool_kelvin")
        if not 0.0 <= self.strictness <= 1.0:
            raise ValueError("strictness must be between 0 and 1")
        if not self.evening_edi < self.daytime_edi:
            raise ValueError("evening_edi must be below daytime_edi")


class DayCurve:
    """Turns a clock time and a solar elevation into a setpoint."""

    def __init__(self, config: DayCurveConfig = None) -> None:
        self.config = config or DayCurveConfig()

    def targets(
        self,
        now: datetime,
        solar_elevation: float,
        lux_target: float,
        lit_fraction: float = 1.0,
    ) -> Targets:
        """The setpoint for this moment.

        ``now`` must be timezone-aware local time: wake and bed are
        wall-clock times and the sun needs UTC, and the two only agree if the
        offset comes along. ``lux_target`` is what the room should be at its
        brightest — the one number a person sets.

        ``lit_fraction`` is how much light the room actually has compared with
        what it was asked for — daylight included. It is what the comfort
        ceiling is measured against; see _kruithof_ceiling.
        """
        cfg = self.config
        cool = self._cool_limit(solar_elevation, now)

        edi_at_eye = self.melanopic_target(now)
        lux, kelvin = colour_then_brightness(
            lux_target=lux_target,
            edi_target=edi_at_eye / VERTICAL_FRACTION,
            warm_kelvin=cfg.warm_kelvin,
            cool_kelvin=cool,
        )
        kelvin = min(kelvin, self._kruithof_ceiling(lit_fraction))
        kelvin = max(kelvin, cfg.warm_kelvin)
        return Targets(
            lux=lux,
            mired=kelvin_to_mired(kelvin),
            melanopic_edi=melanopic_edi(lux, kelvin) * VERTICAL_FRACTION,
        )

    @staticmethod
    def _kruithof_ceiling(lit_fraction: float) -> float:
        """Coolest colour that still looks pleasant in a room this dim.

        The melanopic schedule and this pull in opposite directions in one
        corner, and it is worth being explicit about which wins. The clock
        wants cool light during the day. The eye finds cool light at a low
        level cold and cheerless — Kruithof's observation, and anyone who has
        stood in a dim office at 5000 K knows it without the reference.
        Comfort wins there, because a room people dislike gets overridden and
        a setting that is switched off protects nobody's sleep.

        Measured against how lit the room is *relative to what it was asked
        for*, which took two wrong answers to arrive at. Against the target in
        lux, it clamped two real rooms to 2300 K around the clock, because a
        sensor in a corner reads a fraction of the room and their targets were
        honest about it. Against the lamp level, it did something worse: the
        lamps back off as daylight arrives, so the room went *warmer as the
        day got brighter* — the exact inverse of the schedule this whole
        project exists to follow.

        A ratio has neither fault. Whatever the sensor's scale is, it cancels;
        and daylight is in the numerator, so a bright morning opens the
        ceiling rather than closing it. The cap then stops being a permanent
        clamp and becomes what Kruithof actually describes: a guard for a room
        that is genuinely short of light. A room meeting its target is free to
        follow the clock, which in the evening means warm because the schedule
        asks for warm, not because a ceiling forced it.

        Interpolated in the log of the ratio, since that is how the eye spaces
        brightness.
        """
        low, low_kelvin = 0.10, 2300.0
        high, high_kelvin = 1.00, 5500.0
        if lit_fraction <= low:
            return low_kelvin
        if lit_fraction >= high:
            return high_kelvin
        fraction = math.log(lit_fraction / low) / math.log(high / low)
        return low_kelvin + (high_kelvin - low_kelvin) * fraction

    def _cool_limit(self, solar_elevation: float, now: datetime = None) -> float:
        """Coolest the lamps may be: the sun shuts it, the evening shuts it further."""
        cfg = self.config
        high, low = cfg.cap_fades_between_elevation
        fraction = _smoothstep((solar_elevation - low) / max(high - low, 1e-6))
        capped = cfg.cool_cap_after_dark_kelvin
        cool = capped + (cfg.cool_kelvin - capped) * fraction
        if now is not None:
            cool = min(cool, self._evening_limit(now))
        return max(cool, cfg.warm_kelvin)

    def _evening_limit(self, now: datetime) -> float:
        """Coolest the lamps may be as bedtime comes on, walking down to warm.

        Without this the evening has no colour in it at all, and the reason is
        worth spelling out because it is not obvious. The schedule asks for a
        melanopic level, and the solver warms the lamps only as far as it must
        to reach it. A softly lit room — twenty lux, which is an ordinary
        living room in the evening — already delivers less than the evening
        figure at four thousand kelvin. So the solver sees nothing to do, the
        sun's cap holds the colour at four thousand, and a real room sat there
        from half seven until half past nine while its melanopic target fell
        by a factor of ten underneath it. Bedtime arrived with the light
        exactly as it had been all evening.

        Dimness meeting the target is not the same as the evening having
        happened. Colour is the half people see, it is the half that costs
        nothing in brightness, and *colour gives first* is the whole ordering
        this schedule is built on — so it has to lead whether or not the
        melanopic sum needs it to. This walks the ceiling down across the same
        wind-down the schedule uses, arriving at the warmest setting by
        bedtime rather than an hour after it.

        Interpolated in mired, which is where equal steps look equal.
        """
        cfg = self.config
        day_length = _hours_between(cfg.wake, cfg.bed)
        since_wake = _hours_between(cfg.wake, now.time())
        hours_to_bed = day_length - since_wake
        if since_wake > day_length or hours_to_bed <= 0:
            return cfg.warm_kelvin
        if hours_to_bed >= cfg.evening_ramp_hours:
            # Not the evening yet, so this imposes nothing. Returning the
            # after-dark cap here would clamp the middle of the day to it,
            # which is the opposite of the job.
            return cfg.cool_kelvin

        gone = 1.0 - _smoothstep(hours_to_bed / max(cfg.evening_ramp_hours, 1e-6))
        warm_mired = kelvin_to_mired(cfg.warm_kelvin)
        # Starts where the day leaves off, not at the after-dark cap. Anything
        # else puts a corner in the setpoint at the moment the wind-down
        # begins — fifty mired in one minute — and a corner in the setpoint
        # becomes a corner in the light. The sun's own cap still applies
        # alongside this one; they are two limits, not one.
        cool_mired = kelvin_to_mired(cfg.cool_kelvin)
        return 1_000_000.0 / (cool_mired + (warm_mired - cool_mired) * gone)

    # -- the schedule --------------------------------------------------

    def melanopic_target(self, now: datetime) -> float:
        """Melanopic EDI the room should be delivering, at the eye.

        Interpolated in log space. The span from day to night is a factor of
        250, and walking it linearly would spend almost the whole evening
        near the top and then fall off a cliff — the clock responds to
        ratios, so the schedule moves in ratios.
        """
        cfg = self.config
        day_length = _hours_between(cfg.wake, cfg.bed)
        since_wake = _hours_between(cfg.wake, now.time())

        if since_wake > day_length:
            # Past bedtime, and still moving. Stepping straight to the night
            # figure here would drop most of the range in one tick — the
            # schedule has to keep walking for as long as the rate limit
            # needs to follow it.
            after_bed = since_wake - day_length
            settled = _smoothstep(after_bed / max(cfg.night_ramp_hours, 1e-6))
            return self._scaled(_log_between(cfg.night_edi, cfg.evening_edi, 1.0 - settled))

        morning = _smoothstep(since_wake / max(cfg.morning_ramp_hours, 1e-6))
        hours_to_bed = day_length - since_wake
        evening = _smoothstep(hours_to_bed / max(cfg.evening_ramp_hours, 1e-6))

        rising = _log_between(cfg.night_edi, cfg.daytime_edi, morning)
        falling = _log_between(cfg.evening_edi, cfg.daytime_edi, evening)
        return self._scaled(min(rising, falling))

    def _scaled(self, edi: float) -> float:
        """Ease the schedule towards a flat day as strictness falls."""
        cfg = self.config
        if cfg.strictness >= 1.0:
            return edi
        return _log_between(cfg.daytime_edi, edi, cfg.strictness)

    def day_fraction(self, now: datetime) -> float:
        """How far the room stands from its evening brightness towards its
        daytime one: 1 through the day, 0 from bedtime to waking.

        Not a melanopic matter. Warm light already keeps an ordinary room
        well under the evening figure, so the schedule above never asks the
        lamps to dim. This is comfort: the same lamps feel brighter to eyes
        that have been in the dark since sunset. Colour still goes first. The
        lamps warm across the three hours before bed and only the second half
        of that brings the brightness down, arriving at the evening setting
        at bedtime, slowly enough to go unseen.
        """
        cfg = self.config
        day_length = _hours_between(cfg.wake, cfg.bed)
        since_wake = _hours_between(cfg.wake, now.time())
        if since_wake >= day_length:
            return 0.0
        hours_to_bed = day_length - since_wake
        falling = _smoothstep(hours_to_bed / max(cfg.evening_ramp_hours / 2.0, 1e-6))
        rising = _smoothstep(since_wake / max(cfg.morning_rise_hours, 1e-6))
        return min(rising, falling)

    def in_morning_ramp(self, now: datetime) -> bool:
        """Whether the wake-up ramp is running.

        The one stretch of the day the light is *meant* to be seen changing.
        Everything else in this system exists to be invisible; a wake-up ramp
        that nobody notices has failed at its job, because being noticed is
        its job — that rise from warm and dim to bright and cool is the
        signal that sets the clock for the day.

        It cannot be made to fit the ordinary rate limit either. The schedule
        walks a factor of 250 in melanopic terms between waking and midday,
        and the colour crosses its whole range inside a narrow slice of that.
        Stretching the ramp to six hours still does not bring it under the
        limit, which is the arithmetic telling us this is a different kind of
        transition rather than a badly tuned one.
        """
        cfg = self.config
        since_wake = _hours_between(cfg.wake, now.time())
        return since_wake <= cfg.morning_ramp_hours

    def is_night(self, now: datetime) -> bool:
        """Whether this is the part of the day people spend asleep.

        From bedtime to waking, by the clock the household set. Somebody
        moving about then is fetching a glass of water, not starting the day,
        and what they need is enough light to see by and as little as
        possible for the body clock: a nightlight, not the room.
        """
        cfg = self.config
        day_length = _hours_between(cfg.wake, cfg.bed)
        since_wake = _hours_between(cfg.wake, now.time())
        return since_wake >= day_length

    def shape(self, now: datetime, lux_target: float = 100.0) -> float:
        """Brightness as a fraction of the daytime setting.

        Used when adopting a room as it stands: somebody pressing the button
        at ten in the evening is describing their evening, not their daytime,
        and the difference between the two is exactly this.
        """
        return self.targets(now, 0.0, lux_target).lux / max(lux_target, 1e-6)


def _log_between(low: float, high: float, fraction: float) -> float:
    low = max(low, 1e-6)
    high = max(high, low * (1.0 + 1e-9))
    return low * (high / low) ** max(0.0, min(1.0, fraction))


def _hours_between(start: time, end: time) -> float:
    """Hours from ``start`` to ``end``, wrapping across midnight."""
    start_h = start.hour + start.minute / 60.0 + start.second / 3600.0
    end_h = end.hour + end.minute / 60.0 + end.second / 3600.0
    delta = end_h - start_h
    return delta + 24.0 if delta < 0 else delta


def _smoothstep(x: float) -> float:
    """Clamped 3x² − 2x³ — an S-curve with no corners at either end.

    Corners in the setpoint become corners in the light. Even at rates slow
    enough to be invisible, a sudden change of slope is the sort of thing the
    eye picks up.
    """
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    return x * x * (3.0 - 2.0 * x)
