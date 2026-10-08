"""The daylight gate: whether the lamps may light at all, as a state machine.

Two states. OPEN: the lamps may light; daylight above the line has to last
``off_dwell_s`` before they go out. CLOSED: the daylight does the job; the
dark has to last ``on_dwell_s`` before the lamps come back, unless somebody
is in the room, somebody asked, or the sensor has gone quiet.

Pure: no clock and no Home Assistant. The controller feeds it what it knows
each step and acts on the verdict.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class GateState(Enum):
    OPEN = "open"
    CLOSED = "closed"


class Verdict(Enum):
    #: Carry on and light the room.
    LIGHT = "light"
    #: Stay dark: the daylight is enough.
    SUFFICIENT = "sufficient"
    #: Stay dark: it is dark again, but the wait has not run out.
    WAITING = "waiting"
    #: Put the lamps out: the daylight has been enough for the whole wait.
    TURN_OFF = "turn_off"
    #: Come back now; ``reason`` and ``by_person`` say why and how fast.
    RETURN = "return"


@dataclass(frozen=True)
class GateInput:
    dark: bool
    lit: bool
    stale: bool
    person: bool
    arriving: bool
    #: The freshest raw reading is past the line (only meaningful when unlit).
    freshest_bright: bool
    dt_seconds: float


@dataclass(frozen=True)
class Decision:
    verdict: Verdict
    reason: str = ""
    by_person: bool = False
    returning_in_s: float | None = None


class DaylightGate:
    """The gate's memory between steps."""

    def __init__(self, on_dwell_s: float, off_dwell_s: float) -> None:
        self.on_dwell_s = on_dwell_s
        self.off_dwell_s = off_dwell_s
        self.reset()

    def reset(self) -> None:
        self.state = GateState.OPEN
        self.bright_for = 0.0
        self.dark_for = 0.0
        self.asked = False

    def ask(self) -> None:
        """A person asked for the ordinary room: no cloud wait for them."""
        self.asked = True

    def restart_bright_wait(self) -> None:
        self.bright_for = 0.0

    def step(self, gate_input: GateInput) -> Decision:
        if self.state is GateState.CLOSED:
            return self._closed(gate_input)
        return self._open(gate_input)

    def _closed(self, i: GateInput) -> Decision:
        if not i.dark:
            self.dark_for = 0.0
            self.asked = False
            return Decision(Verdict.SUFFICIENT, "daylight_sufficient")
        if i.stale:
            return self._return(True, "sensor_silent")
        if i.person:
            return self._return(True, "arrival" if i.arriving else "motion")
        if self.asked:
            return self._return(True, "asked")
        if i.freshest_bright:
            # Coming back unasked waits for the freshest reading to agree as
            # well as the averaged one.
            self.dark_for = 0.0
            return Decision(Verdict.WAITING, "waiting_to_return", returning_in_s=self.on_dwell_s)
        self.dark_for += i.dt_seconds
        if self.dark_for < self.on_dwell_s:
            return Decision(Verdict.WAITING, "waiting_to_return",
                            returning_in_s=self.on_dwell_s - self.dark_for)
        return self._return(False, "daylight_gone")

    def _open(self, i: GateInput) -> Decision:
        if i.dark:
            self.bright_for = 0.0
            return Decision(Verdict.LIGHT)
        if not i.lit:
            # Nothing lit yet and the daylight already does the job: the first
            # verdict is given at once; the wait only resists changes.
            self._close()
            return Decision(Verdict.SUFFICIENT, "daylight_sufficient")
        self.bright_for += i.dt_seconds
        if self.bright_for < self.off_dwell_s:
            return Decision(Verdict.LIGHT)
        self._close()
        return Decision(Verdict.TURN_OFF, "daylight_off")

    def _close(self) -> None:
        self.state = GateState.CLOSED
        self.bright_for = 0.0
        self.dark_for = 0.0

    def _return(self, by_person: bool, reason: str) -> Decision:
        self.reset()
        return Decision(Verdict.RETURN, reason, by_person=by_person)
