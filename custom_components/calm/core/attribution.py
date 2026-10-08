"""Telling our own doing apart from somebody else's hand on the dimmer.

Backing off when a person takes over is the whole reason this can be left
running. Get it wrong in one direction and it keeps overriding somebody who
clearly wanted something else; wrong in the other and it sulks for two hours
because it saw its own command come back.

The second is the one that actually happens, and it happens constantly. A
command carries a transition, so the lamp spends the next few minutes
walking to the new value and the bridge reports every step of the way. Each
step arrives as a state change that does not match what was asked for — and
the obvious check, "did this change carry our context id", does not save you:
once a Hue bridge pushes state back over its own event stream, the context
belongs to the bridge.

So attribution is a question of evidence rather than a flag, and the answer
is yes to all three of these:

* have we asked for nothing at all yet, or
* did it carry a context we issued, or
* is the lamp still inside the fade we asked for, or
* has it arrived at what we asked for?

Only a change that is none of those was somebody else.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta


@dataclass
class Expectation:
    """What we asked for, and how long the lamps have to get there."""

    brightness: dict[str, int]
    mired: float
    until: datetime
    issued_at: datetime | None = None


@dataclass
class ChangeAttributor:
    """Decides whether a reported change was ours."""

    #: How far off the mark still counts as having arrived. Eight-bit
    #: brightness plus a bridge rounding its own arithmetic means the value
    #: that comes back is rarely the exact one that went out.
    brightness_tolerance: int = 12
    mired_tolerance: float = 30.0
    #: Slack after a fade should have finished, for the lamp to report it.
    settle_margin_s: float = 30.0
    #: How many of our own context ids to keep. A ring, not a set: trimming
    #: a set keeps whichever ids it feels like, which quietly throws away
    #: the newest — exactly the ones still in flight.
    remembered_contexts: int = 64
    #: How long an earlier command still explains a report. Lamps report late
    #: and out of order: a report of the command before last is still ours.
    recent_s: float = 300.0

    _contexts: deque[str] = field(default_factory=lambda: deque(maxlen=64))
    _expectation: Expectation | None = None
    _recent: deque[Expectation] = field(default_factory=lambda: deque(maxlen=32))

    def issued(self, context_id: str) -> None:
        """Note a context id we are about to send a command under."""
        self._contexts.append(context_id)

    def expect(
        self,
        when: datetime,
        brightness: dict[str, int],
        mired: float,
        transition_s: float,
    ) -> None:
        """Record what was asked for and when the lamps should have got there."""
        self._expectation = Expectation(
            brightness=dict(brightness),
            mired=mired,
            until=when + timedelta(seconds=transition_s + self.settle_margin_s),
            issued_at=when,
        )
        self._recent.append(self._expectation)

    def forget(self) -> None:
        self._expectation = None
        self._recent.clear()

    # -- the judgement -------------------------------------------------

    def is_ours(
        self,
        when: datetime,
        entity_id: str,
        brightness: int | None,
        mired: float | None,
        context_id: str | None = None,
    ) -> bool:
        if context_id is not None and context_id in self._contexts:
            return True

        expectation = self._expectation
        if expectation is None:
            # Nothing has been asked for yet, so there is nothing this change
            # can be measured against. That is not a neutral state: it is the
            # state every reload starts in, and every settings change reloads
            # the whole entry — while the lamps are still walking towards a
            # command the previous object sent, over a transition that can
            # run to a minute or more. Judging those reports against no
            # expectation marked them as somebody's hand, and a room that
            # thinks it has been taken over stands still for two hours.
            #
            # Absence of evidence is not evidence of a hand. The cost of
            # being wrong this way is that a genuine manual change in the
            # first moments after a reload is missed, and the room takes it
            # over on its next command — which is what turning the room on
            # means anyway.
            return True

        if when <= expectation.until:
            # Mid-fade. The lamp is reporting a value on the way to the one
            # we asked for, and none of those will match.
            return True

        if brightness is None:
            return False
        # Any of our commands of the last few minutes explains it: lamps report
        # late and out of order, and the one that arrives may answer the
        # command before last.
        horizon = when - timedelta(seconds=self.recent_s)
        for past in reversed(self._recent):
            # The latest command always counts, however long ago it went out.
            if past is not expectation and past.issued_at is not None and past.issued_at < horizon:
                break
            wanted = past.brightness.get(entity_id)
            if wanted is None or abs(brightness - wanted) > self.brightness_tolerance:
                continue
            if mired is not None and past.mired is not None and abs(mired - past.mired) > self.mired_tolerance:
                continue
            return True
        return False
