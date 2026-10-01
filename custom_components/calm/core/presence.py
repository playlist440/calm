"""Whether anybody is in the room, which is not the same as whether a sensor
is reporting motion.

A motion sensor answers a narrower question than the one being asked. It says
"something moved just now"; the room wants to know "is somebody here". People
sit still — a film, a book, a meal — and every motion sensor in the world
falls silent while they do. Treating that silence as an empty room is the
classic failure of motion lighting, and it is exactly the failure this
project exists to avoid: light that changes behind you while you are sitting
in it.

So motion is held. The room stays occupied for a while after the last
movement, and the hold is long enough that stillness has to be real before it
counts. That, together with the asymmetry in how the room leaves and returns
— minutes down, one second back — is what makes a wrong guess cost nothing.

The other half of the honesty is the start. A room whose sensor happens to
read "no motion" the moment Home Assistant restarts has told us nothing about
how long that has been true, so the benefit of the doubt goes to the room
being occupied until something says otherwise. Dimming a room because a
process restarted would be indefensible.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

#: How long after the last movement the room still counts as occupied.
#: Ten minutes is a compromise with a reason: a Hue sensor clears about a
#: minute after somebody stops moving, and people sit still for far longer
#: than that, so anything much shorter empties a room that has somebody in it.
DEFAULT_HOLD_S = 600.0


class PresenceHold:
    """Motion in, occupancy out."""

    def __init__(self, hold_s: float = DEFAULT_HOLD_S) -> None:
        self.hold_s = max(0.0, hold_s)
        self._detecting = False
        self._since: Optional[datetime] = None

    def seen(self, when: datetime, detecting: bool) -> None:
        """What the sensor says, and when it started saying it.

        ``when`` is when the sensor entered this state rather than now, so a
        room whose sensor has been clear for an hour is known to have been
        clear for an hour the moment Calm starts, instead of being given a
        fresh ten minutes of grace it has not earned.
        """
        self._detecting = detecting
        self._since = when

    @property
    def detecting(self) -> bool:
        """Whether the sensor is reporting movement right this moment.

        Different from being occupied, and the difference matters for the
        rate limit: somebody moving about does not notice the lamps shifting,
        somebody sitting still through the hold very much does.
        """
        return self._detecting

    @property
    def known(self) -> bool:
        """Whether anything has been heard from a sensor at all."""
        return self._since is not None

    def occupied(self, now: datetime) -> bool:
        """Whether the room counts as having somebody in it.

        Unknown counts as occupied. A room that has told us nothing is not
        an empty room, and the cost of the two mistakes is not symmetrical:
        holding the light on a little too long is a nuisance, dimming a room
        somebody is sitting in is the thing that gets an integration deleted.
        """
        if not self.known:
            return True
        if self._detecting:
            return True
        return now - self._since < timedelta(seconds=self.hold_s)

    def empty_for(self, now: datetime) -> float:
        """Seconds since the room stopped counting as occupied. 0 while it is."""
        if self.occupied(now):
            return 0.0
        return (now - self._since).total_seconds() - self.hold_s
