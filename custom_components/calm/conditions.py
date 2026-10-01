"""When Calm may run a room: "Wanneer mag Calm de lampen aansturen?"

Every condition that is switched on has to hold. The combination a real
house uses is "somebody is home, and night mode is off": two conditions,
both true. Movement is deliberately not one of them. It is a layer inside:
these decide whether Calm runs the room, the motion sensor then decides
between full light, standby and off.

"I do it myself" switches the whole question off. Calm then does what its
switch says and nothing else, which is how somebody with their own
automations keeps them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from typing import Any, List, Mapping, Optional, Tuple

from homeassistant.const import ATTR_RESTORED, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant

from .const import (
    CONF_END,
    CONF_ENTITY,
    CONF_ENTITY_STATE,
    CONF_HOME,
    CONF_MANUAL,
    CONF_START,
    CONF_WINDOW,
)

HOME_ZONE = "zone.home"

#: What "if this is on or off" can be asked of. A sensor can say "on" too,
#: but offering every sensor put two entities called "Nachtmodus actief" side
#: by side, and the one picked had not existed for months.
ON_OFF_DOMAINS = [
    "automation", "binary_sensor", "fan", "group", "input_boolean",
    "light", "remote", "schedule", "siren", "switch",
]

_WORDS = {
    "nl": {
        "home": "er is niemand thuis",
        "entity_on": "{name} staat aan",
        "entity_off": "{name} staat uit",
        "entity_missing": "{name} bestaat niet meer, kies bij Wanneer een andere",
        "entity_unavailable": "{name} is onbereikbaar",
        "window": "het is buiten de ingestelde tijden",
        "switch": "de Calm-schakelaar staat uit",
    },
    "en": {
        "home": "nobody is home",
        "entity_on": "{name} is on",
        "entity_off": "{name} is off",
        "entity_missing": "{name} no longer exists, choose another under When",
        "entity_unavailable": "{name} is unavailable",
        "window": "it is outside the set hours",
        "switch": "the Calm switch is off",
    },
}


@dataclass(frozen=True)
class When:
    """The conditions one room runs under."""

    home: bool = True
    entity: Optional[str] = None
    #: The state ``entity`` has to be in: "on" or "off".
    entity_state: str = "off"
    window: bool = False
    start: time = time(6, 0)
    end: time = time(23, 30)
    #: The household runs the switch itself; no condition is looked at.
    manual: bool = False

    @classmethod
    def from_dict(cls, data: Optional[Mapping[str, Any]]) -> "When":
        data = data or {}
        return cls(
            home=bool(data.get(CONF_HOME, True)),
            entity=data.get(CONF_ENTITY) or None,
            entity_state="on" if data.get(CONF_ENTITY_STATE) == "on" else "off",
            window=bool(data.get(CONF_WINDOW, False)),
            start=_time(data.get(CONF_START), time(6, 0)),
            end=_time(data.get(CONF_END), time(23, 30)),
            manual=bool(data.get(CONF_MANUAL, False)),
        )

    def to_dict(self) -> dict:
        return {
            CONF_HOME: self.home,
            CONF_ENTITY: self.entity,
            CONF_ENTITY_STATE: self.entity_state,
            CONF_WINDOW: self.window,
            CONF_START: self.start.isoformat(),
            CONF_END: self.end.isoformat(),
            CONF_MANUAL: self.manual,
        }

    def watched(self) -> List[str]:
        """Entities whose changes mean asking again."""
        if self.manual:
            return []
        watched = []
        if self.home:
            watched.append(HOME_ZONE)
        if self.entity:
            watched.append(self.entity)
        return watched

    def evaluate(
        self, hass: HomeAssistant, now: datetime, language: str = "nl"
    ) -> Tuple[bool, Optional[str]]:
        """Whether Calm may run the room, and if not, why not in a few words.

        Only meaningful when ``manual`` is off; with it on the answer comes
        from the switch, not from here.
        """
        failing, because = self.check(hass, now, language)
        return failing is None, because

    def check(
        self, hass: HomeAssistant, now: datetime, language: str = "nl"
    ) -> Tuple[Optional[str], Optional[str]]:
        """Which condition fails, if any, and the words for it.

        The first is what a room compares from one moment to the next: a
        different condition failing is a change, even though the answer is
        still no.
        """
        words = _WORDS.get(language, _WORDS["nl"])
        if self.home and not someone_home(hass):
            return "home", words["home"]
        if self.entity:
            state = hass.states.get(self.entity)
            if self.entity_missing(hass):
                # By its id: the name it had may now belong to another entity,
                # which is how the wrong one got picked.
                return "entity_missing", words["entity_missing"].format(name=self.entity)
            if state.state == STATE_UNAVAILABLE:
                return "entity_unavailable", words["entity_unavailable"].format(name=state.name)
            if state.state != self.entity_state:
                key = "entity_on" if state.state == STATE_ON else "entity_off"
                return key, words[key].format(name=state.name)
        if self.window and not _within(now.time(), self.start, self.end):
            return "window", words["window"]
        return None, None

    def entity_missing(self, hass: HomeAssistant) -> bool:
        """Whether the entity asked about is gone for good.

        Home Assistant keeps a removed entity in its registry and shows it as
        unavailable with ``restored`` set: nothing will ever update it again.
        """
        if not self.entity or self.manual:
            return False
        state = hass.states.get(self.entity)
        return state is None or (
            state.state == STATE_UNAVAILABLE and bool(state.attributes.get(ATTR_RESTORED))
        )


def someone_home(hass: HomeAssistant) -> bool:
    """Whether anybody is home, by the people Home Assistant knows.

    A house with no people set up counts as always home: refusing to light
    anything because nobody configured presence is not "it just works".
    """
    if not hass.states.async_entity_ids("person"):
        return True
    zone = hass.states.get(HOME_ZONE)
    if zone is None:
        return True
    try:
        return int(float(zone.state)) > 0
    except (TypeError, ValueError):
        return True


def switch_off_reason(language: str = "nl") -> str:
    return _WORDS.get(language, _WORDS["nl"])["switch"]


def _within(now: time, start: time, end: time) -> bool:
    if start <= end:
        return start <= now < end
    return now >= start or now < end


def _time(value, default: time) -> time:
    if isinstance(value, time):
        return value
    try:
        return time.fromisoformat(str(value))
    except (TypeError, ValueError):
        return default
