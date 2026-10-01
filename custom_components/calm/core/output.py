"""Deciding what actually goes out over Zigbee, and how little of it.

A Hue bridge is not a fast pipe, and a Zigbee mesh is a shared one. The
published guidance is around ten light commands a second and roughly one
group command a second, but those are ceilings for a burst, not a rate to
sit at. A controller running all day in five rooms can quietly generate tens
of thousands of commands and spoil the responsiveness of every switch in the
house while doing nothing visible at all.

Three things bring that down by about two orders of magnitude, and none of
them costs anything in smoothness:

**Send to a group, not to each lamp.** This falls straight out of the fixed
dim profile: every lamp in a room is at the same level unless somebody
weighted it, so one group command does what N lamp commands would. Where
weights differ, lamps sharing a weight still share a command.

**Say nothing when nothing changed.** Which is most of the time: settled in
daylight, settled after dark, sitting inside the dead band.

**Let the lamp do the interpolation.** A command carrying ``transition`` equal
to the interval is a ramp, not a step, so the interval can be minutes rather
than seconds. The bound is how well a straight line fits the curve over that
interval, and over five minutes of a schedule this smooth it fits to well
under a percent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple


@dataclass(frozen=True)
class LampGroup:
    """Lamps that want exactly the same thing, so they travel together."""

    lamp_ids: Tuple[str, ...]
    brightness: int
    mired: int


@dataclass(frozen=True)
class Emission:
    """What to put on the wire this tick. Empty groups means: nothing."""

    groups: Tuple[LampGroup, ...]
    transition_s: float
    reason: str
    #: Lamps that were being driven and no longer are, so they have to be put
    #: out. Not addressing a lamp is not the same as switching it off — it
    #: just leaves it wherever it was, which is how a lamp deselected from an
    #: override sits there still burning while the model believes it dark.
    extinguish: Tuple[str, ...] = ()

    @property
    def commands(self) -> int:
        return len(self.groups) + (1 if self.extinguish else 0)

    def __bool__(self) -> bool:
        return bool(self.groups) or bool(self.extinguish)


@dataclass
class CommandPacer:
    """Turns a stream of desired states into as few commands as possible."""

    #: Smallest change worth a command, in the lamp's own 0–255 steps. One
    #: step is 0.4% of perceived brightness — below anything anyone can see,
    #: and not worth a radio transmission.
    min_brightness_step: int = 2
    #: Likewise for colour, in mired.
    min_mired_step: int = 3
    #: Never send more often than this.
    min_interval_s: float = 60.0
    #: Send at least this often while the lamps are on, so a lamp that missed
    #: a command or was changed behind our back comes back into line.
    resync_after_s: float = 900.0
    #: How much longer than the last gap each fade is asked to take.
    #:
    #: The single most important number in this file. A fade that finishes
    #: before the next command arrives leaves the lamp standing still until
    #: it does — move, stop, move, stop — and a change that starts and stops
    #: is enormously more noticeable than the same change made continuously.
    #: It was exactly what somebody spotted in a real room: "it seems to go
    #: in fairly abrupt steps". Overlapping slightly means the next command
    #: always arrives mid-fade, so the light never stops moving.
    overlap: float = 1.25

    _last: Dict[str, Tuple[int, int]] = field(default_factory=dict)
    _last_sent: Optional[datetime] = None
    #: Running count, for the diagnostics. The number people want when they
    #: ask what this is doing to their network.
    sent: int = 0
    suppressed: int = 0

    def reset(self) -> None:
        self._last.clear()
        self._last_sent = None

    def emit(
        self,
        now: datetime,
        brightness: Dict[str, int],
        mired: float,
        transition_s: Optional[float] = None,
        force: bool = False,
    ) -> Emission:
        """Work out the smallest set of commands that says what is wanted."""
        wanted = {
            lamp_id: (level, int(round(mired)))
            for lamp_id, level in brightness.items()
        }

        if not force and not self._due(now, wanted):
            self.suppressed += 1
            return Emission(groups=(), transition_s=0.0, reason="unchanged")

        dropped = tuple(sorted(set(self._last) - set(wanted)))
        gap = self._gap(now)
        groups = self._group(wanted)
        self._last = dict(wanted)
        self._last_sent = now
        self.sent += len(groups) + (1 if dropped else 0)
        return Emission(
            groups=groups,
            transition_s=gap if transition_s is None else transition_s,
            reason="forced" if force else "changed",
            extinguish=dropped,
        )

    # -- the decisions -------------------------------------------------

    def _due(self, now: datetime, wanted: Dict[str, Tuple[int, int]]) -> bool:
        if self._last_sent is None or set(wanted) != set(self._last):
            return True
        since = (now - self._last_sent).total_seconds()
        if since < self.min_interval_s:
            return False
        if since >= self.resync_after_s:
            return True
        for lamp_id, (level, colour) in wanted.items():
            was_level, was_colour = self._last[lamp_id]
            if abs(level - was_level) >= self.min_brightness_step:
                return True
            if abs(colour - was_colour) >= self.min_mired_step:
                return True
        return False

    def _gap(self, now: datetime) -> float:
        """How long the lamp has to interpolate over: until we next speak.

        Set to the interval that just elapsed, which is the best estimate of
        the next one. Get it wrong and the lamp arrives early and waits —
        a step, small but avoidable — so it tracks the real cadence rather
        than a configured guess.
        """
        if self._last_sent is None:
            return self.min_interval_s * self.overlap
        gap = max(self.min_interval_s, (now - self._last_sent).total_seconds())
        return gap * self.overlap

    @staticmethod
    def _group(wanted: Dict[str, Tuple[int, int]]) -> Tuple[LampGroup, ...]:
        buckets: Dict[Tuple[int, int], List[str]] = {}
        for lamp_id, state in sorted(wanted.items()):
            buckets.setdefault(state, []).append(lamp_id)
        return tuple(
            LampGroup(lamp_ids=tuple(ids), brightness=level, mired=colour)
            for (level, colour), ids in sorted(buckets.items())
        )
