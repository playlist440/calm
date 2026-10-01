"""Daylight arriving at a sensor indoors, over a day."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from datetime import datetime

from custom_components.calm.core.sun import solar_elevation


@dataclass
class DaylightModel:
    """Clear-sky daylight through a window, times a wandering cloud factor."""

    latitude: float = 52.09
    longitude: float = 5.12
    #: Indoor lux at the sensor with the sun overhead and no cloud. Stands in
    #: for window size, orientation and how far the sensor sits from it.
    peak_lux: float = 420.0
    #: How quickly cloud cover wanders, in seconds.
    cloud_tau_s: float = 900.0
    cloud_floor: float = 0.12
    seed: int = 7

    def __post_init__(self) -> None:
        self._random = random.Random(self.seed)
        self._cloud = 0.85
        self._event_remaining = 0.0
        self._event_depth = 1.0

    def clear_sky(self, when: datetime) -> float:
        elevation = solar_elevation(when, self.latitude, self.longitude)
        if elevation <= 0:
            # Twilight still carries some light, and it is the part of the day
            # the controller works hardest in, so it is worth not being zero.
            return max(0.0, self.peak_lux * 0.012 * math.exp(elevation / 3.0))
        return self.peak_lux * math.sin(math.radians(elevation)) ** 1.3

    def step(self, when: datetime, dt_seconds: float) -> float:
        """Daylight at the sensor now, cloud and all."""
        pull = 1.0 - math.exp(-dt_seconds / self.cloud_tau_s)
        self._cloud += pull * (0.85 - self._cloud)
        self._cloud += self._random.gauss(0.0, 0.05) * math.sqrt(pull)
        self._cloud = min(1.0, max(self.cloud_floor, self._cloud))

        if self._event_remaining > 0:
            self._event_remaining -= dt_seconds
            factor = self._event_depth
        else:
            factor = 1.0
            # A cloud crossing the sun is fast and deep, and it is the case
            # the damping has to survive without the lamps lurching.
            if self._random.random() < dt_seconds / 3600.0:
                self._event_remaining = self._random.uniform(120.0, 900.0)
                self._event_depth = self._random.uniform(0.25, 0.6)

        return self.clear_sky(when) * self._cloud * factor
