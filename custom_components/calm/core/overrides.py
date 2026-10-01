"""Overrides: the same room, told to be something else for a while.

An override says only how it differs from normal. Everything it does not
mention keeps doing what it was doing, which is what makes one quick to set
up and what lets it keep following the day while it runs: an hour into a
film the room is still tracking the evening, just darker and warmer.

Each one answers four questions, and nothing else:

* which lamps take part (every lamp not mentioned does what it normally does);
* how bright: relative to what the room would otherwise want, or a set level;
* which colour: the day's, shifted warmer or cooler, or a set temperature;
* whether it switches itself on: never, at set times, or when some other
  device is on (the television, say).

Templates are starting points with the answers filled in, not built-in
modes. A work light in one house is a reading lamp in another, and shipping
fixed modes would mean everybody bending their room into mine.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, time
from enum import Enum
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Optional

from .naming import slugify, unique_key
from .photometry import kelvin_to_mired
from .profile import DimProfile, Lamp


class LevelMode(Enum):
    #: A factor on whatever the room would otherwise want. Keeps following
    #: the day: a film that runs past sunset still gets the sunset.
    RELATIVE = "relative"
    #: A set level, ignoring the day. For an arrangement that has one job.
    FIXED = "fixed"


class ColourMode(Enum):
    FOLLOW = "follow"
    #: Warmer or cooler than the day by a fixed amount, in mired.
    SHIFT = "shift"
    FIXED = "fixed"


class AutoMode(Enum):
    NONE = "none"
    TIME = "time"
    ENTITY = "entity"


class LampRole(Enum):
    #: Whatever the room normally does with it.
    NORMAL = "normal"
    #: Off for the duration: the spot that reflects in the television.
    OFF = "off"
    #: On for the duration, even if it normally sits the day out. This is how
    #: a worktop light that is only for work gets called in.
    ON = "on"


class Source(Enum):
    """Who switched an override on, which decides what it may override.

    A person asked for it: it wins over daylight and over Calm being off,
    because they are standing there and they asked. It switched itself on:
    it is automatic like the rest of the room, so it steps aside for
    daylight and only runs while Calm is running the room.
    """

    USER = "user"
    AUTO = "auto"


@dataclass(frozen=True)
class Override:
    """One named arrangement for one room."""

    #: What everything points at: the switch, the stored settings. Derived
    #: from the name once, at creation, and fixed after that. Renaming is
    #: removing and making again, which is honest about what it costs.
    key: str
    name: str
    lamps: Mapping[str, LampRole] = field(default_factory=dict)
    #: Relative brightness within this override, per lamp, 0..1. A lamp not
    #: listed keeps the weight the room gave it.
    weights: Mapping[str, float] = field(default_factory=dict)
    level_mode: LevelMode = LevelMode.RELATIVE
    #: For RELATIVE a factor (0.4 is a good film, 2.0 a good worktop); for
    #: FIXED a perceived level 0..1.
    level: float = 1.0
    colour_mode: ColourMode = ColourMode.FOLLOW
    #: For SHIFT, in mired. Positive is warmer.
    mired_shift: float = 0.0
    #: For FIXED.
    kelvin: float = 2700.0
    #: A person asked for this, so it may be seen. Two and a half seconds
    #: reads as deliberate; instant reads as a glitch.
    transition_s: float = 2.5
    auto: AutoMode = AutoMode.NONE
    auto_start: Optional[time] = None
    auto_end: Optional[time] = None
    auto_entity: Optional[str] = None
    #: The state of ``auto_entity`` that switches this override on.
    auto_state: str = "on"

    # -- what it does ---------------------------------------------------

    def profile_from(self, base: DimProfile) -> DimProfile:
        """This override's arrangement of lamps, built from the room's own.

        Nothing is re-normalised. Promoting whatever is left after the main
        light goes off would turn a deliberate accent into the brightest
        thing in the room: the opposite of what a film was asked for.
        """
        lamps = []
        for lamp in base.lamps:
            role = self.lamps.get(lamp.id, LampRole.NORMAL)
            if role is LampRole.OFF:
                enabled = False
            elif role is LampRole.ON:
                enabled = True
            else:
                enabled = lamp.enabled
            weight = self.weights.get(lamp.id)
            lamps.append(
                Lamp(
                    id=lamp.id,
                    weight=lamp.weight if weight is None else max(0.0, float(weight)),
                    floor=lamp.floor,
                    ceiling=lamp.ceiling,
                    full_contribution=lamp.full_contribution,
                    enabled=enabled,
                )
            )
        return DimProfile(lamps=lamps)

    def target_lux(self, adaptive_lux: float) -> Optional[float]:
        """What this override wants in lux. ``None`` means a set level."""
        if self.level_mode is LevelMode.FIXED:
            return None
        return adaptive_lux * max(self.level, 0.0)

    def target_mired(self, adaptive_mired: float) -> float:
        if self.colour_mode is ColourMode.FIXED:
            return kelvin_to_mired(self.kelvin)
        if self.colour_mode is ColourMode.SHIFT:
            return adaptive_mired + self.mired_shift
        return adaptive_mired

    @property
    def fixed_level(self) -> Optional[float]:
        if self.level_mode is LevelMode.FIXED:
            return min(max(self.level, 0.0), 1.0)
        return None

    # -- whether it switches itself on ---------------------------------

    def wants_on(self, now: datetime, entity_state: Optional[str] = None) -> bool:
        """Whether this override would switch itself on right now.

        ``entity_state`` is the current state of ``auto_entity``, looked up by
        whoever calls this; the core does not know how to read a house.
        """
        if self.auto is AutoMode.TIME:
            if self.auto_start is None or self.auto_end is None:
                return False
            return _within(now.time(), self.auto_start, self.auto_end)
        if self.auto is AutoMode.ENTITY:
            # A template picked but no device chosen yet: nothing to follow,
            # so it never switches itself on.
            if not self.auto_entity or entity_state is None:
                return False
            return entity_state == self.auto_state
        return False

    # -- storage --------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "name": self.name,
            "lamps": {k: v.value for k, v in self.lamps.items()},
            "weights": dict(self.weights),
            "level_mode": self.level_mode.value,
            "level": self.level,
            "colour_mode": self.colour_mode.value,
            "mired_shift": self.mired_shift,
            "kelvin": self.kelvin,
            "transition_s": self.transition_s,
            "auto": self.auto.value,
            "auto_start": self.auto_start.isoformat() if self.auto_start else None,
            "auto_end": self.auto_end.isoformat() if self.auto_end else None,
            "auto_entity": self.auto_entity,
            "auto_state": self.auto_state,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Override":
        """Read one back, tolerating anything that is missing.

        Settings written by an older version, or edited by hand, should
        produce a working override with defaults rather than an exception in
        somebody's setup.
        """
        name = str(data.get("name") or "").strip()
        key = str(data.get("key") or "") or slugify(name)
        if not key:
            raise ValueError("an override needs a name that yields a key")
        return cls(
            key=key,
            name=name or key,
            lamps={
                str(k): _enum(LampRole, v, LampRole.NORMAL)
                for k, v in (data.get("lamps") or {}).items()
            },
            weights={str(k): float(v) for k, v in (data.get("weights") or {}).items()},
            level_mode=_enum(LevelMode, data.get("level_mode"), LevelMode.RELATIVE),
            level=float(data.get("level", 1.0)),
            colour_mode=_enum(ColourMode, data.get("colour_mode"), ColourMode.FOLLOW),
            mired_shift=float(data.get("mired_shift", 0.0)),
            kelvin=float(data.get("kelvin", 2700.0)),
            transition_s=float(data.get("transition_s", 2.5)),
            auto=_enum(AutoMode, data.get("auto"), AutoMode.NONE),
            auto_start=_time(data.get("auto_start")),
            auto_end=_time(data.get("auto_end")),
            auto_entity=data.get("auto_entity") or None,
            auto_state=str(data.get("auto_state") or "on"),
        )


# -- templates -------------------------------------------------------------

#: Starting points. Only the answers that do not depend on the room: which
#: lamp is the reading lamp or sits by the television is something only the
#: person setting it up knows, so templates leave every lamp as it is.
TEMPLATES: Dict[str, Dict[str, Any]] = {
    "tv": {
        "level_mode": "relative", "level": 0.4,
        "colour_mode": "shift", "mired_shift": 60.0,
        "auto": "entity", "auto_state": "on",
    },
    "film": {
        "level_mode": "relative", "level": 0.2,
        "colour_mode": "shift", "mired_shift": 90.0,
    },
    "work": {
        "level_mode": "relative", "level": 2.0,
        "colour_mode": "fixed", "kelvin": 4000.0,
    },
    "dinner": {
        "level_mode": "relative", "level": 0.8,
        "colour_mode": "follow",
        "auto": "time", "auto_start": "17:30:00", "auto_end": "19:00:00",
    },
    "cooking": {
        "level_mode": "relative", "level": 1.6,
        "colour_mode": "fixed", "kelvin": 3500.0,
    },
    "reading": {
        "level_mode": "relative", "level": 0.7,
        "colour_mode": "follow",
    },
    "cosy": {
        "level_mode": "relative", "level": 0.5,
        "colour_mode": "shift", "mired_shift": 80.0,
    },
    "cleaning": {
        "level_mode": "fixed", "level": 1.0,
        "colour_mode": "fixed", "kelvin": 5000.0,
    },
    "nightlight": {
        "level_mode": "fixed", "level": 0.05,
        "colour_mode": "fixed", "kelvin": 2000.0,
    },
}


def from_template(template: str, name: str, key: str) -> Override:
    """A new override with a template's answers already filled in."""
    settings = dict(TEMPLATES.get(template, {}))
    return Override.from_dict({**settings, "name": name, "key": key})


class OverrideSet:
    """Every override one room has, in the order they were made.

    Two rules. Keys are unique within a room, because two overrides sharing
    a key would share a switch and the second would quietly win. And keys do
    not change: there is deliberately no rename.
    """

    def __init__(self, overrides: Iterable[Override] = ()) -> None:
        self._items: List[Override] = []
        for override in overrides:
            self.add(override)

    def __iter__(self) -> Iterator[Override]:
        return iter(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __contains__(self, key: object) -> bool:
        return any(item.key == key for item in self._items)

    def get(self, key: Optional[str]) -> Optional[Override]:
        return next((item for item in self._items if item.key == key), None)

    def keys(self) -> List[str]:
        return [item.key for item in self._items]

    def add(self, override: Override) -> Override:
        if not override.key:
            raise ValueError("an override without a key cannot be pointed at")
        if override.key in self:
            raise ValueError("there is already an override with key %r" % override.key)
        self._items.append(override)
        return override

    def create(self, name: str, template: Optional[str] = None) -> Override:
        """The only place a name becomes a key, so collisions settle in one place."""
        key = unique_key(name, self.keys())
        if not key:
            raise ValueError("%r does not yield a usable name" % name)
        override = (
            from_template(template, name, key) if template else Override(key=key, name=name)
        )
        return self.add(override)

    def replace(self, override: Override) -> Override:
        for index, existing in enumerate(self._items):
            if existing.key == override.key:
                self._items[index] = override
                return override
        raise KeyError(override.key)

    def remove(self, key: str) -> bool:
        before = len(self._items)
        self._items = [item for item in self._items if item.key != key]
        return len(self._items) != before

    def to_list(self) -> List[Dict[str, Any]]:
        return [item.to_dict() for item in self._items]

    @classmethod
    def from_list(cls, stored: Iterable[Mapping[str, Any]]) -> "OverrideSet":
        """Every stored override that can be read. The rest are skipped."""
        result = cls()
        for data in stored or ():
            try:
                override = Override.from_dict(data)
            except (TypeError, ValueError):
                continue
            if override.key in result:
                override = replace(override, key=unique_key(override.name, result.keys()))
            result.add(override)
        return result


def _within(now: time, start: time, end: time) -> bool:
    """Whether a clock time falls in a window that may run past midnight."""
    if start <= end:
        return start <= now < end
    return now >= start or now < end


def _enum(kind, value, default):
    try:
        return kind(value)
    except ValueError:
        return default


def _time(value) -> Optional[time]:
    if value in (None, ""):
        return None
    if isinstance(value, time):
        return value
    try:
        return time.fromisoformat(str(value))
    except ValueError:
        return None
