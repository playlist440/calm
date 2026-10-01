"""How much lamp light a given amount of daylight leaves you needing.

This is the primary control law, and it is one straight line:

    lamp output = a − b × daylight

``a`` is what the room needs in the dark. ``b`` is the interesting one: how
much lamp light one lux of daylight *at the sensor* actually replaces where
people sit. The tempting answer is one — and in a small room with the sensor
in the middle it nearly is. In a long room with a wall of glass it is
nowhere near.

There, daylight at the window end is several times what reaches the back, so
a cloud that takes 180 lux off the sensor has taken maybe 36 off the part of
the room the lamps light. Compensating the sensor's loss in full overshoots
by that same factor: the lamps surge and the room did not need it.

Nobody can be asked for ``b``. It is a fact about how their house is built,
not a preference, and it has no units anyone thinks in. But it falls out of
two presses of "this is right" at different times of day — once in daylight,
once after dark. Two points, one line.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from .photometry import luminous_to_perceptual, perceptual_to_luminous


@dataclass(frozen=True)
class Anchor:
    """One "this is right, now" — a daylight level and what was wanted at it."""

    daylight_lux: float
    luminous: float


@dataclass
class DaylightResponse:
    """The line, its prior, and the anchors that have bent it."""

    #: Lamp output needed with no daylight at all, as a luminous fraction.
    dark_luminous: float = 0.48
    #: Daylight at which the lamps would no longer be needed, if nothing has
    #: been learned yet. Deliberately generous — see below.
    prior_off_at_lux: float = 480.0
    #: Most recent anchors; older ones are dropped.
    anchors: List[Anchor] = field(default_factory=list)
    max_anchors: int = 8
    #: How strongly the prior slope resists being moved by a single anchor.
    prior_weight: float = 0.6

    _slope: Optional[float] = None

    def __post_init__(self) -> None:
        if self.prior_off_at_lux <= 0:
            raise ValueError("prior_off_at_lux must be positive")

    # -- the line ------------------------------------------------------

    @property
    def slope(self) -> float:
        """Luminous output given up per lux of daylight at the sensor.

        The prior errs shallow on purpose. Backing off too slowly leaves a
        room slightly brighter than it needed to be, which nobody minds;
        backing off too quickly leaves it dim while it still looks light
        outside, which is the complaint this whole project exists to avoid.
        """
        if self._slope is None:
            return self.dark_luminous / self.prior_off_at_lux
        return self._slope

    def luminous_for(self, daylight_lux: float) -> float:
        return max(0.0, min(1.0, self.dark_luminous - self.slope * max(daylight_lux, 0.0)))

    def master_for(self, daylight_lux: float) -> float:
        return luminous_to_perceptual(self.luminous_for(daylight_lux))

    @property
    def off_at_lux(self) -> float:
        """Daylight level at which the lamps are no longer needed."""
        slope = self.slope
        return self.dark_luminous / slope if slope > 0 else float("inf")

    @property
    def learned(self) -> bool:
        return len(self.anchors) >= 2

    # -- learning ------------------------------------------------------

    def anchor(self, daylight_lux: float, master: float) -> None:
        """Record "this is right, now" and refit.

        Asking someone to set the room how they like it and press a button is
        the only question in this whole system a person can answer without
        thinking about it. Everything else here is arithmetic done on top of
        their answers.
        """
        self.anchors.append(
            Anchor(
                daylight_lux=max(0.0, daylight_lux),
                luminous=perceptual_to_luminous(max(0.0, min(1.0, master))),
            )
        )
        del self.anchors[: -self.max_anchors]
        self._fit()

    def forget(self) -> None:
        """Start over — the room changed, or they want to."""
        self.anchors.clear()
        self._slope = None

    def _fit(self) -> None:
        """Least squares through the anchors, pulled towards the prior slope.

        The pull matters. Two presses half an hour apart in almost identical
        light describe a line through two points that are nearly on top of
        each other, and the slope through them is noise. The prior keeps that
        from turning into a confident wrong answer.
        """
        if not self.anchors:
            self._slope = None
            return

        if len(self.anchors) == 1:
            only = self.anchors[0]
            # One point fixes the height of the line, not its tilt.
            self.dark_luminous = min(
                1.0, only.luminous + self.slope * only.daylight_lux
            )
            return

        prior_slope = self.dark_luminous / self.prior_off_at_lux
        xs = [a.daylight_lux for a in self.anchors]
        ys = [a.luminous for a in self.anchors]
        n = float(len(xs))
        mean_x, mean_y = sum(xs) / n, sum(ys) / n

        spread = sum((x - mean_x) ** 2 for x in xs)
        covariance = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))

        # Ridge towards the prior: with little spread in the anchors the fit
        # barely moves, and with a good range it takes over.
        weight = self.prior_weight * max(spread, 1.0) ** 0.0 * 10_000.0
        slope = (-covariance + weight * prior_slope) / (spread + weight)
        self._slope = max(0.0, slope)
        self.dark_luminous = max(0.0, min(1.0, mean_y + self._slope * mean_x))

    def snapshot(self) -> dict:
        return {
            "dark_luminous": self.dark_luminous,
            "slope": self._slope,
            "anchors": [(a.daylight_lux, a.luminous) for a in self.anchors],
        }

    def restore(self, data: dict) -> None:
        self.dark_luminous = float(data.get("dark_luminous", self.dark_luminous))
        slope = data.get("slope")
        self._slope = None if slope is None else float(slope)
        self.anchors = [Anchor(float(d), float(l)) for d, l in data.get("anchors", [])]
