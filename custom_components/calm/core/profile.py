"""The dim profile: one level for the whole room.

A controller that only has to hit a lux target has as many free variables as
there are lamps, and exactly one equation. Left alone it solves that the
cheapest way: lamps near the sensor go down, lamps far away go up, and any
lamp it does not need goes off. That is optimal on paper and a patchwork in
a living room.

So the ratio between lamps is fixed. Every lamp is equally bright unless
someone deliberately weights it, and then that weighting holds too. The
controller is left with a single knob: how high the whole profile sits.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional

from .photometry import perceptual_to_luminous


@dataclass
class Lamp:
    """One light, as the control core sees it."""

    id: str
    #: Relative brightness within the room. Equal for every lamp by default;
    #: the slider exists for the exception, not the rule.
    weight: float = 1.0
    #: Below this perceived level the lamp drops out instead of dimming.
    #: Model-dependent, so it is measured rather than assumed — a room of
    #: spots on a wall dimmer had nothing at all below fifteen percent.
    floor: float = 0.04
    #: And the other end. Some dimmers buzz or flicker at the very top, and
    #: the fix everybody arrives at by hand is to stop just short of it.
    #: There is no light worth having in that last two percent anyway.
    ceiling: float = 1.0
    #: Lux this lamp puts on the sensor at full output. Estimated, never
    #: configured — this is the only genuinely room-specific number there is.
    full_contribution: float = 0.0
    #: Lamps a mode has switched off contribute nothing and take no commands.
    enabled: bool = True


@dataclass
class DimProfile:
    """The lamps of one room and the fixed ratio between them."""

    lamps: List[Lamp] = field(default_factory=list)

    @classmethod
    def from_lamps(cls, lamps: Iterable[Lamp]) -> "DimProfile":
        """Build a profile from user configuration and normalise it once.

        Normalising scales the weights so the brightest lamp reaches full at
        master 1.0; a profile whose heaviest slider sat at 0.6 could otherwise
        never use the top of its range.

        It happens here and nowhere else. Re-normalising a derived profile —
        one a mode has taken lamps out of — would quietly promote whatever
        remains: switch off the main light for cinema mode and the accent lamp
        deliberately set to a third would jump to full. The weights are the
        user's statement about the room, and they survive a mode change intact.
        """
        selected = list(lamps)
        active = [lamp for lamp in selected if lamp.enabled and lamp.weight > 0]
        if not active:
            raise ValueError("a profile needs at least one lamp with a positive weight")
        peak = max(lamp.weight for lamp in active)
        for lamp in selected:
            lamp.weight = lamp.weight / peak
        return cls(lamps=selected)

    @property
    def active(self) -> List[Lamp]:
        """Lamps that are switched on for this mode and carry any weight."""
        return [lamp for lamp in self.lamps if lamp.enabled and lamp.weight > 0]

    @property
    def group_floor(self) -> float:
        """Lowest master level at which every lamp still holds its light.

        Lamps cut out at different points, so ten spots dimming past their
        floors would wink out one after another — the same patchwork the
        fixed profile exists to prevent. The room's floor is therefore the
        highest floor in it, and below that the whole group goes dark at once.
        """
        active = self.active
        if not active:
            return 0.0
        return min(1.0, max(lamp.floor / lamp.weight for lamp in active))

    def level_at(self, lamp: Lamp, master: float) -> float:
        """What this lamp is actually asked for at a given master level.

        Where the room's wishes meet what the hardware can do. A lamp that
        is on is never asked for less than its floor or more than its
        ceiling: below the floor the dimmer delivers nothing while the
        system believes it delivered ten percent, and above the ceiling it
        buzzes. Off is still off — the floor is the bottom of the lit range,
        not a glow that can never be put out.

        One function, used both to command the lamps and to predict what
        they will contribute, so the model cannot drift from the commands.
        """
        if master <= 0.0:
            return 0.0
        wanted = master * lamp.weight
        if wanted <= 0.0:
            return 0.0
        return min(lamp.ceiling, max(lamp.floor, min(1.0, wanted)))

    def levels(self, master: float) -> Dict[str, float]:
        """Perceived level per lamp for a given master level."""
        return {
            lamp.id: self.level_at(lamp, master)
            for lamp in self.active
        }

    def luminous_at(self, master: float) -> float:
        """Lux this room puts on the sensor at ``master``.

        The sum that makes the whole design work: light adds up, so the room's
        contribution is just each lamp's full output scaled by its own curve.
        """
        total = 0.0
        for lamp in self.active:
            total += lamp.full_contribution * perceptual_to_luminous(
                self.level_at(lamp, master)
            )
        return total

    @property
    def full_contribution(self) -> float:
        """Lux on the sensor with the profile at full."""
        return self.luminous_at(1.0)

    def master_for_lux(self, wanted_lux: float, tolerance: float = 1e-4) -> Optional[float]:
        """Master level that produces ``wanted_lux``, or ``None`` if it can't.

        Solved by bisection rather than algebraically: with per-lamp weights
        the sum of several L* curves has no clean inverse, and a room never
        has enough lamps for the iteration cost to matter.
        """
        if wanted_lux <= 0:
            return None
        ceiling = self.full_contribution
        if ceiling <= 0:
            return None
        if wanted_lux >= ceiling:
            return 1.0

        low, high = 0.0, 1.0
        for _ in range(60):
            middle = (low + high) / 2.0
            if self.luminous_at(middle) < wanted_lux:
                low = middle
            else:
                high = middle
            if high - low < tolerance:
                break
        return (low + high) / 2.0

    def scaled(self, factor: float) -> "DimProfile":
        """A copy with every contribution scaled — used when re-seeding."""
        return DimProfile(
            lamps=[
                Lamp(
                    id=lamp.id,
                    weight=lamp.weight,
                    floor=lamp.floor,
                    ceiling=lamp.ceiling,
                    full_contribution=lamp.full_contribution * factor,
                    enabled=lamp.enabled,
                )
                for lamp in self.lamps
            ]
        )

    def with_floor(self, floor: float) -> "DimProfile":
        """A copy where every lamp may go as low as ``floor``.

        For the nightlight, which is meant to be below what the room
        normally allows: the lowest setting is for the room's ordinary light,
        and a nightlight held up to it is a lamp, not a nightlight.
        """
        return DimProfile(
            lamps=[
                Lamp(
                    id=lamp.id,
                    weight=lamp.weight,
                    floor=min(lamp.floor, floor),
                    ceiling=lamp.ceiling,
                    full_contribution=lamp.full_contribution,
                    enabled=lamp.enabled,
                )
                for lamp in self.lamps
            ]
        )

    def with_enabled(self, ids: Iterable[str]) -> "DimProfile":
        """A copy where only ``ids`` are on — how a mode reshapes the room."""
        wanted = set(ids)
        return DimProfile(
            lamps=[
                Lamp(
                    id=lamp.id,
                    weight=lamp.weight,
                    floor=lamp.floor,
                    ceiling=lamp.ceiling,
                    full_contribution=lamp.full_contribution,
                    enabled=lamp.id in wanted,
                )
                for lamp in self.lamps
            ]
        )
