"""What kind of light sensor is this, actually?

Two numbers decide what a room can hope for, and neither is in any spec
sheet: how often the sensor speaks, and how finely it counts. A Hue sensor
reports every five minutes in whole lux — measured over thirteen days, and
the interval is the same for a thirty-lux jump as for a one-lux one. An
ESPHome sensor on a BH1750 can report every second to two decimal places.

The difference is not a detail. With the first you can follow the day and a
front coming over; individual clouds arrive and leave between readings and
there is nothing to be done about that. With the second you can follow
almost anything. Same software, same room, entirely different promise.

So it gets measured, and said out loud. Someone who has been told their
sensor reports every five minutes will not spend three weeks wondering why
the lamps ignore a passing cloud — and someone who wants that behaviour
knows what to buy.
"""

from __future__ import annotations

import statistics
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Deque, List, Optional


class Speed(Enum):
    #: Sub-minute. Follows anything, clouds included.
    FAST = "fast"
    #: A minute or so. Follows the weather, if not every gust of it.
    MODERATE = "moderate"
    #: Several minutes. Follows the day; single clouds pass unseen.
    SLOW = "slow"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class SensorProfile:
    speed: Speed
    #: Median seconds between readings.
    interval_s: float
    #: Smallest difference the sensor ever reports — its counting step.
    resolution_lux: float
    #: Typical reading, for judging whether that step is coarse or fine.
    typical_lux: float
    samples: int

    @property
    def relative_resolution(self) -> float:
        """The counting step as a fraction of a typical reading.

        The number that matters. One lux is superb at three hundred and
        useless at four, and a sensor sitting in a dim corner is doing the
        latter however good it looked in the shop.
        """
        if self.typical_lux <= 0:
            return 1.0
        return self.resolution_lux / self.typical_lux

    @property
    def shortest_cloud_s(self) -> float:
        """Briefest dip worth trying to follow, given how often it speaks.

        Two readings are the minimum to see something happen and have it
        still be happening, so anything shorter than twice the interval is
        over before the loop hears of it.
        """
        return self.interval_s * 2.0


@dataclass
class SensorWatcher:
    """Builds the profile from the readings as they arrive."""

    window: int = 60
    _times: Deque[datetime] = field(default_factory=lambda: deque(maxlen=60))
    _values: Deque[float] = field(default_factory=lambda: deque(maxlen=60))

    def observe(self, when: datetime, lux: float) -> None:
        self._times.append(when)
        self._values.append(max(0.0, lux))

    @property
    def ready(self) -> bool:
        return len(self._times) >= 8

    def profile(self) -> SensorProfile:
        if not self.ready:
            return SensorProfile(
                speed=Speed.UNKNOWN, interval_s=0.0, resolution_lux=0.0,
                typical_lux=0.0, samples=len(self._times),
            )

        gaps = [
            (self._times[i] - self._times[i - 1]).total_seconds()
            for i in range(1, len(self._times))
        ]
        gaps = [g for g in gaps if 0 < g < 3600]
        interval = statistics.median(gaps) if gaps else 0.0

        return SensorProfile(
            speed=_speed(interval),
            interval_s=interval,
            resolution_lux=self._resolution(),
            typical_lux=statistics.median(self._values),
            samples=len(self._times),
        )

    def _resolution(self) -> float:
        """The smallest step it ever takes, which is its counting unit.

        Read off the data rather than assumed: a sensor reporting whole lux
        never produces a difference below one, and one reporting hundredths
        will. Nothing has to be configured and no device list has to be kept.
        """
        steps = sorted(
            abs(self._values[i] - self._values[i - 1])
            for i in range(1, len(self._values))
        )
        nonzero = [s for s in steps if s > 1e-9]
        return nonzero[0] if nonzero else 1.0


def _speed(interval_s: float) -> Speed:
    if interval_s <= 0:
        return Speed.UNKNOWN
    if interval_s <= 45:
        return Speed.FAST
    if interval_s <= 150:
        return Speed.MODERATE
    return Speed.SLOW


def suggested_filter_tau(profile: SensorProfile) -> float:
    """How long to average over, given how often this sensor speaks.

    One reading's worth of smoothing. Less is not smoothing at all; much
    more turns a correction into something that arrives after the event it
    was correcting. Tied to the sensor rather than fixed, because a fixed
    number is right for exactly one kind of hardware.
    """
    if profile.speed is Speed.UNKNOWN:
        return 300.0
    return max(60.0, min(900.0, profile.interval_s))
