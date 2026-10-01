"""A light sensor that behaves like the ones in a real house.

Written from what the real ones did, as recorded in a month of day files:
whole lux, a report only when the value has changed, and at most one report
every five minutes (the median gap was 5.0 minutes, the 90th percentile
too). In a dark room that means silence for hours, because nought stays
nought. That silence is the single most important thing to simulate: every
serious bug in September came from a room believing a number the sensor had
stopped standing behind.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class HueLightSensor:
    #: The shortest gap between two reports.
    min_interval_s: float = 300.0
    #: Measurement noise, relative. Enough to make a steady room report now
    #: and then, not enough to make a dark one talk.
    relative_noise: float = 0.02
    seed: int = 11
    _random: random.Random = field(init=False, repr=False)
    _since: float = field(init=False, default=0.0)
    _last: Optional[int] = field(init=False, default=None)

    def __post_init__(self) -> None:
        self._random = random.Random(self.seed)
        # The first reading arrives at once, as it does when Home Assistant starts.
        self._since = self.min_interval_s

    def observe(self, true_lux: float, dt_seconds: float) -> Optional[int]:
        """What the sensor reports this tick, or ``None`` if it stays quiet."""
        self._since += dt_seconds
        if self._since < self.min_interval_s:
            return None
        noisy = true_lux * (1.0 + self._random.gauss(0.0, self.relative_noise))
        value = max(0, int(round(noisy)))
        if value == self._last:
            return None
        self._last = value
        self._since = 0.0
        return value
