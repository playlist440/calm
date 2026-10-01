"""The rate limit, kept deliberately stupid and deliberately last.

Everything upstream is a model that can be wrong: an estimate that has not
converged, a prediction fitted to a fortnight of weather, a gain someone
changed. The one thing that must never be wrong is the promise that you do
not see it happening. So it is not a property of the controller but a stage
every command passes through, that knows nothing about lighting and cannot
be talked out of it.

It is asymmetric, and the reason is worth stating because it looks like a
fudge. Simulating a deep room across four days of weather turned up something
counter-intuitive: **the lamps move the same amount per hour whatever the
limit is set to.** Once the controller follows a smooth predicted trajectory
instead of chasing a sensor, it hardly ever reaches the limit — so a more
generous one costs nothing in restlessness.

That makes the asymmetry free, and two separate arguments say to take it:

* The two errors do not cost the same. Too dim is uncomfortable and you
  notice; too bright is mildly wasteful and you do not. There is a hurry to
  add light and never a hurry to remove it.
* The eye forgives upwards. Adapting to brighter takes seconds, to darker it
  takes minutes, so a room slowly brightening disappears more readily than
  one slowly dimming.
"""

from __future__ import annotations

from typing import Optional


class SlewLimiter:
    """Caps how fast a value may move, separately in each direction."""

    def __init__(
        self, up_per_minute: float, down_per_minute: Optional[float] = None
    ) -> None:
        if up_per_minute <= 0:
            raise ValueError("up_per_minute must be positive")
        down = up_per_minute if down_per_minute is None else down_per_minute
        if down <= 0:
            raise ValueError("down_per_minute must be positive")
        self.up_per_minute = up_per_minute
        self.down_per_minute = down
        #: How often the limiter had to intervene. A number that stays high
        #: means the controller keeps asking for more than it may have, which
        #: says the prediction is lagging — visible in diagnostics rather
        #: than in the corner of someone's eye.
        self.interventions = 0

    def budget(self, rising: bool, dt_seconds: float, allowance: float = 1.0) -> float:
        rate = self.up_per_minute if rising else self.down_per_minute
        return rate * max(allowance, 0.0) * (max(dt_seconds, 0.0) / 60.0)

    def step(
        self,
        current: float,
        desired: float,
        dt_seconds: float,
        allowance: float = 1.0,
    ) -> float:
        """Move ``current`` towards ``desired``, no faster than allowed.

        ``allowance`` widens the limit for moments when a change is masked
        anyway — someone walking into the room, the light coming on. It is a
        multiplier on the rate, never a way around it.
        """
        if dt_seconds <= 0:
            return current
        delta = desired - current
        budget = self.budget(delta > 0, dt_seconds, allowance)
        if abs(delta) <= budget:
            return desired
        self.interventions += 1
        return current + budget * (1.0 if delta > 0 else -1.0)

    def would_exceed(
        self, before: float, after: float, dt_seconds: float, allowance: float = 1.0
    ) -> bool:
        """Whether a move breaks the limit — used by the tests as an assertion."""
        if dt_seconds <= 0:
            return abs(after - before) > 1e-9
        budget = self.budget(after > before, dt_seconds, allowance)
        return abs(after - before) > budget + 1e-9
