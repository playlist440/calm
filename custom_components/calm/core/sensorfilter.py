"""Getting a usable signal out of a light sensor in a room people live in.

The raw reading is not the light level of the room. It is the light level at
one point on a wall, including whoever just walked past it, the headlights
that swept the ceiling, and — the worst case — the patch of direct sun that
lands on it for forty minutes every clear afternoon.

Three defences, in order: throw out single-sample spikes, average what is
left over minutes rather than seconds, and recognise when the sensor has
stopped describing the room at all.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from typing import Deque, Optional


@dataclass(frozen=True)
class FilterReading:
    """One filtered sample, with what the filter thinks of it.

    Two numbers come out, for two consumers that want opposite things. The
    controller wants calm: a value that ignores a cloud and a passer-by. The
    estimator wants *prompt*: it learns from the step when the lamps change,
    and a smoothed signal has not noticed that step yet. Handing the same
    heavily-averaged number to both is how you end up with a model that
    confidently concludes the lamps do almost nothing.
    """

    #: Smoothed. What the control loop steers by.
    lux: float
    #: Spike-rejected but not smoothed. What the estimator learns from.
    prompt: float
    #: Direct sun, a lamp shining into it, or anything else that pins the
    #: sensor. It is no longer measuring the room, so the loop should coast.
    saturated: bool
    #: True while the filter is still filling up and its output is provisional.
    warming_up: bool
    raw: float


class SensorFilter:
    """Median-then-average, with spike rejection and a saturation flag."""

    #: Five minutes — one sample's worth of smoothing on a sensor that speaks
    #: every five, which is about as short as is meaningful.
    #:
    #: This is the setting that decides how much of a cloud gets compensated,
    #: and it was measured rather than chosen. Against a ten-minute cloud a
    #: twenty-minute window catches 115% of it — overshooting, because it is
    #: still climbing after the cloud has gone — while five minutes catches
    #: 89% and stops. Against a five-minute cloud the difference is 18%
    #: versus 75%. Longer is not calmer here; it is late, and late turns into
    #: a correction arriving after the thing it was correcting.
    #:
    #: The median window defaults to one — off. With readings five minutes
    #: apart, a median of five spans twenty-five minutes, which does not
    #: reject a spike so much as bury every real change alongside it. Outlier
    #: rejection by ratio does that job without the delay.
    #:
    #: ``spike_patience`` is how many disagreeing readings it takes before
    #: the filter accepts that the room changed rather than that one sample
    #: was wrong. Two, because a spike is by definition a sample that goes
    #: away: a second reading agreeing with the first is the room speaking,
    #: not noise repeating itself.
    def __init__(
        self,
        median_window: int = 1,
        tau_seconds: float = 300.0,
        spike_ratio: float = 2.5,
        saturation_lux: float = 2000.0,
        spike_patience: int = 2,
    ) -> None:
        if median_window < 1 or median_window % 2 == 0:
            raise ValueError("median_window must be a positive odd number")
        self.median_window = median_window
        self.tau_seconds = tau_seconds
        self.spike_ratio = spike_ratio
        self.saturation_lux = saturation_lux
        self.spike_patience = spike_patience

        self._window: Deque[float] = deque(maxlen=median_window)
        self._average: Optional[float] = None
        self._prompt: float = 0.0
        self._rejected_in_a_row = 0

    @property
    def value(self) -> Optional[float]:
        return self._average

    def reset(self) -> None:
        self._window.clear()
        self._average = None
        self._prompt = 0.0
        self._rejected_in_a_row = 0

    def update(
        self, lux: float, dt_seconds: float, expect_change: bool = False
    ) -> FilterReading:
        """Fold in one reading.

        ``expect_change`` says the caller changed the lamps since the last
        reading, so a large jump is explained and must not be thrown away.
        Spike rejection exists for the *unexplained* kind — someone walking
        past, headlights across the ceiling. Applied to a room that has just
        been switched on it discards the single most informative measurement
        the system will ever get, and the estimator is left concluding that
        the lamps do nothing.
        """
        raw = max(0.0, lux)
        saturated = raw >= self.saturation_lux

        self._window.append(raw)
        ordered = sorted(self._window)
        median = ordered[len(ordered) // 2]

        if self._average is None:
            self._average = median
            self._prompt = median
            return FilterReading(
                lux=self._average, prompt=median, saturated=saturated,
                warming_up=True, raw=raw,
            )

        if expect_change:
            # We did this. Take it at face value and start averaging afresh
            # from here, rather than crawling towards it for a quarter of an
            # hour while the control loop waits.
            self._average = median
            self._prompt = median
            self._rejected_in_a_row = 0
            return FilterReading(
                lux=median, prompt=median, saturated=saturated,
                warming_up=False, raw=raw,
            )

        if self._is_spike(median):
            # One outlier is noise. Two in a row that agree with each other
            # and not with us are not noise — they are the room, and we are
            # the thing that is wrong.
            #
            # Two rather than three, and the difference is not academic. The
            # readings arrive five minutes apart, so each extra sample of
            # patience costs five minutes of running on a number the sensor
            # has already contradicted. Measured over three days in a real
            # living room: 139 minutes a day at three, 88 at two. One
            # evening it sat on 42 lux for thirteen minutes while the sensor
            # said twelve and then four, and called it enough daylight to
            # leave the room dark.
            #
            # At dusk a factor of two and a half between two readings five
            # minutes apart is not an anomaly. It is just how fast it gets
            # dark, and a rule that treats it as an anomaly is wrong about
            # the time of day when it matters most.
            self._rejected_in_a_row += 1
            if self._rejected_in_a_row < self.spike_patience:
                return FilterReading(
                    lux=self._average, prompt=self._prompt, saturated=saturated,
                    warming_up=False, raw=raw,
                )
            self._average = median
            self._prompt = median
            self._rejected_in_a_row = 0
            return FilterReading(
                lux=self._average, prompt=median, saturated=saturated,
                warming_up=False, raw=raw,
            )

        self._rejected_in_a_row = 0
        self._prompt = median
        alpha = 1.0 - math.exp(-max(dt_seconds, 0.0) / max(self.tau_seconds, 1e-6))
        self._average += alpha * (median - self._average)
        return FilterReading(
            lux=self._average,
            prompt=median,
            saturated=saturated,
            warming_up=len(self._window) < self.median_window,
            raw=raw,
        )

    def _is_spike(self, median: float) -> bool:
        """Judge the deviation as a ratio, because that is how light works.

        Twenty lux of difference is a different event at 30 lux than at 800,
        and an absolute threshold would be far too twitchy at one end and
        useless at the other.
        """
        reference = max(self._average or 0.0, 1.0)
        ratio = max(median, 1.0) / reference
        return ratio > self.spike_ratio or ratio < 1.0 / self.spike_ratio
