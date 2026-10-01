"""Setting Calm up, and changing it later.

Three places, as in the design:

* **Installing**: which rooms, a light sensor where one is missing, when
  Calm may run, and the day's rhythm. One tick and three times "Next" for
  somebody who changes nothing.
* **Calm's own settings** (the gear on the integration): the rooms again,
  "when" and the rhythm for all of them.
* **A room's settings** (under the room): one screen with sections, and the
  room's overrides.

Nothing here asks for a number nobody can answer. Lux shows up in one
place, next to what the sensor reads right now.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List, Optional

import voluptuous as vol
from homeassistant.config_entries import (
    SOURCE_RECONFIGURE,
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentry,
    ConfigSubentryData,
    ConfigSubentryFlow,
    OptionsFlow,
    SubentryFlowResult,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers import selector

from . import discovery
from .conditions import ON_OFF_DOMAINS, When
from .const import (
    CONF_AREA,
    CONF_BED,
    CONF_BRIGHTNESS,
    CONF_COOL_KELVIN,
    CONF_END,
    CONF_EVENING_BRIGHTNESS,
    CONF_ENTITY,
    CONF_ENTITY_STATE,
    CONF_EXCLUDED,
    CONF_HOME,
    CONF_LIGHT_SENSOR,
    CONF_LIGHTS,
    CONF_MANUAL,
    CONF_MANUAL_HOLD,
    CONF_MAX_LEVEL,
    CONF_MIN_LEVEL,
    CONF_MOTION_ALWAYS,
    CONF_MOTION_HOLD,
    CONF_MOTION_SENSOR,
    CONF_NAME,
    CONF_NIGHTLIGHT_KELVIN,
    CONF_NIGHTLIGHT_LEVEL,
    CONF_OVERRIDES,
    CONF_OWN_RHYTHM,
    CONF_OWN_WHEN,
    CONF_RHYTHM,
    CONF_STANDBY_KELVIN,
    CONF_STANDBY_LEVEL,
    CONF_START,
    CONF_THRESHOLD,
    CONF_USE_MOTION,
    CONF_WAKE,
    CONF_WARM_KELVIN,
    CONF_WHEN,
    CONF_WHEN_EMPTY,
    CONF_WINDOW,
    DEFAULT_BED,
    DEFAULT_BRIGHTNESS,
    DEFAULT_COOL_KELVIN,
    DEFAULT_EVENING_BRIGHTNESS,
    DEFAULT_MANUAL_HOLD,
    DEFAULT_MAX_LEVEL,
    DEFAULT_MIN_LEVEL,
    DEFAULT_MOTION_HOLD,
    DEFAULT_NIGHTLIGHT_KELVIN,
    DEFAULT_NIGHTLIGHT_LEVEL,
    DEFAULT_STANDBY_KELVIN,
    DEFAULT_STANDBY_LEVEL,
    DEFAULT_THRESHOLD,
    DEFAULT_WAKE,
    DEFAULT_WARM_KELVIN,
    DOMAIN,
    SUBENTRY_ROOM,
)
from .core import TEMPLATES, Override, OverrideSet, from_template, unique_key

# -- the words in a room's line in the list ------------------------------

_LINE = {
    "nl": {"lamps": "{n} lampen", "lamp": "1 lamp", "sensor": "lichtsensor",
           "motion": "bewegingssensor", "no_sensor": "geen lichtsensor (kies je hierna)"},
    "en": {"lamps": "{n} lamps", "lamp": "1 lamp", "sensor": "light sensor",
           "motion": "motion sensor", "no_sensor": "no light sensor (you pick one next)"},
}

#: What the sensor read as a room's settings opened.
_READING = {
    "nl": {"plain": "Toen je dit scherm opende, mat de sensor {measured} lux.",
           "lamps": "Toen je dit scherm opende, mat de sensor {measured} lux, waarvan ongeveer "
                    "{daylight} lux daglicht. De rest kwam van de lampen.",
           "none": "Toen je dit scherm opende, gaf de sensor geen meting."},
    "en": {"plain": "When you opened this screen, the sensor read {measured} lux.",
           "lamps": "When you opened this screen, the sensor read {measured} lux, of which about "
                    "{daylight} lux was daylight. The rest came from the lamps.",
           "none": "When you opened this screen, the sensor gave no reading."},
}

#: Template names, in the language of the house.
_TEMPLATE_NAMES = {
    "nl": {"tv": "TV kijken", "film": "Film", "work": "Werklicht", "dinner": "Eten",
           "cooking": "Koken", "reading": "Lezen", "cosy": "Gezellig",
           "cleaning": "Schoonmaken", "nightlight": "Nachtlampje", "blank": "Leeg"},
    "en": {"tv": "Watching TV", "film": "Film", "work": "Work light", "dinner": "Dinner",
           "cooking": "Cooking", "reading": "Reading", "cosy": "Cosy",
           "cleaning": "Cleaning", "nightlight": "Nightlight", "blank": "Blank"},
}

NEW = "__new__"


def _lang(hass: HomeAssistant) -> str:
    return "nl" if (hass.config.language or "nl").startswith("nl") else "en"


def _describe(hass: HomeAssistant, contents: discovery.AreaContents) -> str:
    """One line per room, all a list row can hold: what is in it, and
    whether it is ready or needs a sensor first."""
    words = _LINE[_lang(hass)]
    count = len(contents.lights)
    parts = [words["lamp"] if count == 1 else words["lamps"].format(n=count)]
    if contents.light_sensors:
        parts.append(words["sensor"])
        if contents.motion_sensors:
            parts.append(words["motion"])
    else:
        parts.append(words["no_sensor"])
    return f"{contents.name} — " + ", ".join(parts)


def _room_options(hass: HomeAssistant, areas: List[discovery.AreaContents]):
    """Suitable rooms first, then the ones that need a sensor, each by name."""
    ordered = sorted(areas, key=lambda c: (not c.suitable, c.name.casefold()))
    return [selector.SelectOptionDict(value=c.area_id, label=_describe(hass, c)) for c in ordered]


def _new_room(contents: discovery.AreaContents, sensor: Optional[str] = None) -> ConfigSubentryData:
    data: Dict[str, Any] = {CONF_AREA: contents.area_id, CONF_NAME: contents.name,
                            CONF_THRESHOLD: DEFAULT_THRESHOLD}
    if sensor:
        data[CONF_LIGHT_SENSOR] = sensor
    return ConfigSubentryData(
        data=data, subentry_type=SUBENTRY_ROOM, title=contents.name, unique_id=contents.area_id,
    )


# -- selectors used in more than one place --------------------------------

def _percent(low: int = 1, high: int = 100, step: int = 1) -> selector.NumberSelector:
    return selector.NumberSelector(selector.NumberSelectorConfig(
        min=low, max=high, step=step, unit_of_measurement="%",
        mode=selector.NumberSelectorMode.SLIDER))


def _kelvin(low: int = 2000, high: int = 6500) -> selector.NumberSelector:
    return selector.NumberSelector(selector.NumberSelectorConfig(
        min=low, max=high, step=100, unit_of_measurement="K",
        mode=selector.NumberSelectorMode.SLIDER))


def _minutes(high: int = 60) -> selector.NumberSelector:
    return selector.NumberSelector(selector.NumberSelectorConfig(
        min=1, max=high, step=1, unit_of_measurement="min",
        mode=selector.NumberSelectorMode.SLIDER))


LIGHT_SENSOR = selector.EntitySelector(selector.EntitySelectorConfig(
    domain="sensor", device_class="illuminance"))
MOTION_SENSOR = selector.EntitySelector(selector.EntitySelectorConfig(
    domain="binary_sensor", device_class=["motion", "occupancy", "presence"]))
TIME = selector.TimeSelector()
ON_OFF = selector.SelectSelector(selector.SelectSelectorConfig(
    options=["off", "on"], translation_key="on_off", mode=selector.SelectSelectorMode.LIST))


#: The "when" screen: one question on top, the rarer ones folded away under
#: a heading, open only when they are already in use.
_WHEN_SECTIONS = {
    "condition": (CONF_ENTITY, CONF_ENTITY_STATE),
    "hours": (CONF_WINDOW, CONF_START, CONF_END),
    "yourself": (CONF_MANUAL,),
}

ON_OFF_ENTITY = selector.EntitySelector(selector.EntitySelectorConfig(domain=ON_OFF_DOMAINS))


def _when_fields(when: When) -> Dict[Any, Any]:
    return {
        vol.Required(CONF_HOME, default=when.home): selector.BooleanSelector(),
        vol.Optional(CONF_ENTITY, description={"suggested_value": when.entity}): ON_OFF_ENTITY,
        vol.Required(CONF_ENTITY_STATE, default=when.entity_state): ON_OFF,
        vol.Required(CONF_WINDOW, default=when.window): selector.BooleanSelector(),
        vol.Required(CONF_START, default=when.start.isoformat()): TIME,
        vol.Required(CONF_END, default=when.end.isoformat()): TIME,
        vol.Required(CONF_MANUAL, default=when.manual): selector.BooleanSelector(),
    }


def _when_schema(when: When) -> vol.Schema:
    fields = {str(key): (key, value) for key, value in _when_fields(when).items()}
    in_use = {"condition": bool(when.entity), "hours": when.window, "yourself": when.manual}
    schema: Dict[Any, Any] = dict([fields[CONF_HOME]])
    for name, keys in _WHEN_SECTIONS.items():
        schema[vol.Required(name)] = section(
            vol.Schema(dict(fields[key] for key in keys)), {"collapsed": not in_use[name]})
    return vol.Schema(schema)


def _when_values(user_input: Dict[str, Any]) -> Dict[str, Any]:
    """The answers, whether they came folded in sections or flat."""
    flat = dict(user_input)
    for name in _WHEN_SECTIONS:
        flat.update(flat.pop(name, None) or {})
    return When.from_dict({
        CONF_HOME: flat.get(CONF_HOME, True),
        CONF_ENTITY: flat.get(CONF_ENTITY),
        CONF_ENTITY_STATE: flat.get(CONF_ENTITY_STATE, "off"),
        CONF_WINDOW: flat.get(CONF_WINDOW, False),
        CONF_START: flat.get(CONF_START),
        CONF_END: flat.get(CONF_END),
        CONF_MANUAL: flat.get(CONF_MANUAL, False),
    }).to_dict()


def _rhythm_schema(rhythm: Dict[str, Any], colours: bool) -> vol.Schema:
    fields: Dict[Any, Any] = {
        vol.Required(CONF_WAKE, default=rhythm.get(CONF_WAKE, DEFAULT_WAKE)): TIME,
        vol.Required(CONF_BED, default=rhythm.get(CONF_BED, DEFAULT_BED)): TIME,
    }
    if colours:
        fields[vol.Required("colours")] = section(vol.Schema({
            vol.Required(CONF_WARM_KELVIN, default=rhythm.get(CONF_WARM_KELVIN, DEFAULT_WARM_KELVIN)):
                _kelvin(1800, 3000),
            vol.Required(CONF_COOL_KELVIN, default=rhythm.get(CONF_COOL_KELVIN, DEFAULT_COOL_KELVIN)):
                _kelvin(4000, 6500),
        }), {"collapsed": True})
    return vol.Schema(fields)


def _rhythm_values(user_input: Dict[str, Any]) -> Dict[str, Any]:
    colours = user_input.get("colours") or {}
    return {
        CONF_WAKE: user_input.get(CONF_WAKE, DEFAULT_WAKE),
        CONF_BED: user_input.get(CONF_BED, DEFAULT_BED),
        CONF_WARM_KELVIN: colours.get(CONF_WARM_KELVIN, DEFAULT_WARM_KELVIN),
        CONF_COOL_KELVIN: colours.get(CONF_COOL_KELVIN, DEFAULT_COOL_KELVIN),
    }


# -- installing ------------------------------------------------------------


class CalmConfigFlow(ConfigFlow, domain=DOMAIN):
    """Installing Calm: which rooms, and the settings they share."""

    VERSION = 2
    MINOR_VERSION = 1

    def __init__(self) -> None:
        self._chosen: List[str] = []
        self._needs_sensor: List[str] = []
        self._sensors: Dict[str, str] = {}
        self._when: Dict[str, Any] = When().to_dict()

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> "CalmOptionsFlow":
        return CalmOptionsFlow()

    @classmethod
    @callback
    def async_get_supported_subentry_types(cls, entry: ConfigEntry):
        return {SUBENTRY_ROOM: RoomFlow}

    async def async_step_user(self, user_input: Optional[Dict[str, Any]] = None) -> ConfigFlowResult:
        areas = discovery.scan(self.hass)
        if not areas:
            return self.async_abort(reason="no_rooms")
        errors: Dict[str, str] = {}
        if user_input is not None:
            chosen = list(user_input.get("rooms") or [])
            if not chosen:
                errors["base"] = "choose_one"
            else:
                self._chosen = chosen
                by_id = {c.area_id: c for c in areas}
                self._needs_sensor = [a for a in chosen if not by_id[a].suitable]
                return await self.async_step_sensor()
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Optional("rooms", default=self._chosen): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=_room_options(self.hass, areas), multiple=True,
                        mode=selector.SelectSelectorMode.LIST)),
            }),
            errors=errors,
            last_step=False,
        )

    async def async_step_sensor(self, user_input: Optional[Dict[str, Any]] = None) -> ConfigFlowResult:
        """One screen per chosen room that has no light sensor of its own."""
        if user_input is not None and self._needs_sensor:
            area_id = self._needs_sensor.pop(0)
            if user_input.get(CONF_LIGHT_SENSOR):
                self._sensors[area_id] = user_input[CONF_LIGHT_SENSOR]
            else:
                self._chosen.remove(area_id)
        if not self._needs_sensor:
            return await self.async_step_when()
        area_id = self._needs_sensor[0]
        name = discovery.contents(self.hass, area_id).name
        return self.async_show_form(
            step_id="sensor",
            data_schema=vol.Schema({vol.Optional(CONF_LIGHT_SENSOR): LIGHT_SENSOR}),
            description_placeholders={"room": name},
            last_step=False,
        )

    async def async_step_when(self, user_input: Optional[Dict[str, Any]] = None) -> ConfigFlowResult:
        if user_input is not None:
            self._when = _when_values(user_input)
            return await self.async_step_rhythm()
        return self.async_show_form(
            step_id="when", data_schema=_when_schema(When.from_dict(self._when)), last_step=False)

    async def async_step_rhythm(self, user_input: Optional[Dict[str, Any]] = None) -> ConfigFlowResult:
        if user_input is not None:
            rooms = []
            for area_id in self._chosen:
                contents = discovery.contents(self.hass, area_id)
                if contents is not None:
                    rooms.append(_new_room(contents, self._sensors.get(area_id)))
            return self.async_create_entry(
                title="Calm", data={},
                options={CONF_WHEN: self._when, CONF_RHYTHM: _rhythm_values(user_input)},
                subentries=rooms,
            )
        return self.async_show_form(step_id="rhythm", data_schema=_rhythm_schema({}, colours=False))


# -- Calm's own settings -----------------------------------------------------


class CalmOptionsFlow(OptionsFlow):
    """Which rooms, and what they all share."""

    def __init__(self) -> None:
        self._adding: List[str] = []
        self._sensors: Dict[str, str] = {}

    async def async_step_init(self, user_input: Optional[Dict[str, Any]] = None) -> ConfigFlowResult:
        return self.async_show_menu(step_id="init", menu_options=["rooms", "when", "rhythm"])

    def _active(self) -> Dict[str, str]:
        """Area id to subentry id, for rooms that come from an area."""
        return {
            sub.data.get(CONF_AREA): sub_id
            for sub_id, sub in self.config_entry.subentries.items()
            if sub.subentry_type == SUBENTRY_ROOM and sub.data.get(CONF_AREA)
        }

    async def async_step_rooms(self, user_input: Optional[Dict[str, Any]] = None) -> ConfigFlowResult:
        areas = discovery.scan(self.hass)
        active = self._active()
        if user_input is not None:
            chosen = set(user_input.get("rooms") or [])
            for area_id, sub_id in active.items():
                if area_id not in chosen:
                    self.hass.config_entries.async_remove_subentry(self.config_entry, sub_id)
            by_id = {c.area_id: c for c in areas}
            self._adding = [a for a in chosen if a not in active and a in by_id]
            return await self.async_step_room_sensor()
        return self.async_show_form(
            step_id="rooms",
            data_schema=vol.Schema({
                vol.Optional("rooms", default=[a for a in active if a]): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=_room_options(self.hass, areas), multiple=True,
                        mode=selector.SelectSelectorMode.LIST)),
            }),
        )

    async def async_step_room_sensor(self, user_input: Optional[Dict[str, Any]] = None) -> ConfigFlowResult:
        """Add the newly ticked rooms, asking for a sensor where one is missing."""
        if user_input is not None and self._adding:
            area_id = self._adding.pop(0)
            if user_input.get(CONF_LIGHT_SENSOR):
                self._add(area_id, user_input[CONF_LIGHT_SENSOR])
        while self._adding:
            area_id = self._adding[0]
            contents = discovery.contents(self.hass, area_id)
            if contents is None:
                self._adding.pop(0)
                continue
            if contents.suitable:
                self._adding.pop(0)
                self._add(area_id, None)
                continue
            return self.async_show_form(
                step_id="room_sensor",
                data_schema=vol.Schema({vol.Optional(CONF_LIGHT_SENSOR): LIGHT_SENSOR}),
                description_placeholders={"room": contents.name},
            )
        return self.async_create_entry(data=dict(self.config_entry.options))

    def _add(self, area_id: str, sensor: Optional[str]) -> None:
        contents = discovery.contents(self.hass, area_id)
        if contents is None:
            return
        room = _new_room(contents, sensor)
        self.hass.config_entries.async_add_subentry(self.config_entry, ConfigSubentry(
            data=room["data"], subentry_type=room["subentry_type"],
            title=room["title"], unique_id=room["unique_id"],
        ))

    async def async_step_when(self, user_input: Optional[Dict[str, Any]] = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(
                data={**self.config_entry.options, CONF_WHEN: _when_values(user_input)})
        when = When.from_dict(self.config_entry.options.get(CONF_WHEN))
        return self.async_show_form(step_id="when", data_schema=_when_schema(when))

    async def async_step_rhythm(self, user_input: Optional[Dict[str, Any]] = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(
                data={**self.config_entry.options, CONF_RHYTHM: _rhythm_values(user_input)})
        rhythm = self.config_entry.options.get(CONF_RHYTHM) or {}
        return self.async_show_form(step_id="rhythm", data_schema=_rhythm_schema(rhythm, colours=True))


# -- one room ----------------------------------------------------------------


class RoomFlow(ConfigSubentryFlow):
    """Adding a room, and a room's own settings and overrides."""

    def __init__(self) -> None:
        self._area: Optional[str] = None
        self._override_key: Optional[str] = None
        #: A room's settings on their way to being saved, while its own
        #: "when" or rhythm is asked for on a screen of its own.
        self._pending: Optional[Dict[str, Any]] = None
        #: A new override made from a template but not saved yet: it is only
        #: stored once its own screen has been answered.
        self._pending: Optional[Dict[str, Any]] = None

    # -- adding --------------------------------------------------------

    async def async_step_user(self, user_input: Optional[Dict[str, Any]] = None) -> SubentryFlowResult:
        return self.async_show_menu(step_id="user", menu_options=["area", "custom"])

    def _taken(self) -> set:
        return {
            sub.data.get(CONF_AREA) for sub in self._get_entry().subentries.values()
            if sub.subentry_type == SUBENTRY_ROOM
        }

    async def async_step_area(self, user_input: Optional[Dict[str, Any]] = None) -> SubentryFlowResult:
        areas = [c for c in discovery.scan(self.hass) if c.area_id not in self._taken()]
        if not areas:
            return self.async_abort(reason="no_more_rooms")
        if user_input is not None:
            contents = discovery.contents(self.hass, user_input[CONF_AREA])
            if contents.suitable:
                room = _new_room(contents)
                return self.async_create_entry(
                    title=room["title"], data=room["data"], unique_id=room["unique_id"])
            self._area = contents.area_id
            return await self.async_step_area_sensor()
        return self.async_show_form(
            step_id="area",
            data_schema=vol.Schema({
                vol.Required(CONF_AREA): selector.SelectSelector(selector.SelectSelectorConfig(
                    options=_room_options(self.hass, areas), mode=selector.SelectSelectorMode.LIST)),
            }),
        )

    async def async_step_area_sensor(self, user_input: Optional[Dict[str, Any]] = None) -> SubentryFlowResult:
        contents = discovery.contents(self.hass, self._area)
        errors: Dict[str, str] = {}
        if user_input is not None:
            if user_input.get(CONF_LIGHT_SENSOR):
                room = _new_room(contents, user_input[CONF_LIGHT_SENSOR])
                return self.async_create_entry(
                    title=room["title"], data=room["data"], unique_id=room["unique_id"])
            errors[CONF_LIGHT_SENSOR] = "sensor_needed"
        return self.async_show_form(
            step_id="area_sensor",
            data_schema=vol.Schema({vol.Optional(CONF_LIGHT_SENSOR): LIGHT_SENSOR}),
            description_placeholders={"room": contents.name},
            errors=errors,
        )

    async def async_step_custom(self, user_input: Optional[Dict[str, Any]] = None) -> SubentryFlowResult:
        errors: Dict[str, str] = {}
        if user_input is not None:
            if not user_input.get(CONF_LIGHTS):
                errors[CONF_LIGHTS] = "no_lights"
            elif not user_input.get(CONF_LIGHT_SENSOR):
                errors[CONF_LIGHT_SENSOR] = "sensor_needed"
            else:
                data = {
                    CONF_AREA: None, CONF_NAME: user_input[CONF_NAME].strip(),
                    CONF_LIGHTS: user_input[CONF_LIGHTS],
                    CONF_LIGHT_SENSOR: user_input[CONF_LIGHT_SENSOR],
                    CONF_THRESHOLD: DEFAULT_THRESHOLD,
                }
                if user_input.get(CONF_MOTION_SENSOR):
                    data[CONF_MOTION_SENSOR] = user_input[CONF_MOTION_SENSOR]
                return self.async_create_entry(title=data[CONF_NAME], data=data)
        return self.async_show_form(
            step_id="custom",
            data_schema=vol.Schema({
                vol.Required(CONF_NAME): selector.TextSelector(),
                vol.Required(CONF_LIGHTS): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="light", multiple=True)),
                vol.Required(CONF_LIGHT_SENSOR): LIGHT_SENSOR,
                vol.Optional(CONF_MOTION_SENSOR): MOTION_SENSOR,
            }),
            errors=errors,
        )

    # -- a room's settings ----------------------------------------------

    def _room(self) -> Dict[str, Any]:
        return deepcopy(dict(self._get_reconfigure_subentry().data))

    def _save(self, data: Dict[str, Any]) -> SubentryFlowResult:
        return self.async_update_and_abort(
            self._get_entry(), self._get_reconfigure_subentry(), data=data)

    async def async_step_reconfigure(self, user_input: Optional[Dict[str, Any]] = None) -> SubentryFlowResult:
        return self.async_show_menu(step_id="reconfigure", menu_options=["settings", "overrides"])

    async def async_step_settings(self, user_input: Optional[Dict[str, Any]] = None) -> SubentryFlowResult:
        data = self._room()
        hass = self.hass
        lights = discovery.room_lights(hass, data)
        motion = discovery.room_motion_sensor(hass, data)
        entry = self._get_entry()
        if user_input is not None:
            self._pending = _settings_values(data, user_input, motion is not None)
            return await self._next_or_save()

        fields: Dict[Any, Any] = {
            vol.Required("light"): section(vol.Schema({
                vol.Required(CONF_THRESHOLD, default=data.get(CONF_THRESHOLD, DEFAULT_THRESHOLD)):
                    selector.NumberSelector(selector.NumberSelectorConfig(
                        min=1, max=300, step=1, unit_of_measurement="lx",
                        mode=selector.NumberSelectorMode.BOX)),
                vol.Required(CONF_BRIGHTNESS, default=data.get(CONF_BRIGHTNESS, DEFAULT_BRIGHTNESS)):
                    _percent(5, 100, 5),
                vol.Required(CONF_EVENING_BRIGHTNESS,
                             default=data.get(CONF_EVENING_BRIGHTNESS, DEFAULT_EVENING_BRIGHTNESS)):
                    _percent(5, 100, 5),
            }), {"collapsed": False}),
        }
        if motion is not None:
            fields[vol.Required("motion")] = section(vol.Schema({
                vol.Required(CONF_USE_MOTION, default=data.get(CONF_USE_MOTION, False)):
                    selector.BooleanSelector(),
                vol.Required(CONF_MOTION_HOLD, default=data.get(CONF_MOTION_HOLD, DEFAULT_MOTION_HOLD)):
                    _minutes(60),
                vol.Required(CONF_WHEN_EMPTY, default=data.get(CONF_WHEN_EMPTY, "off")):
                    selector.SelectSelector(selector.SelectSelectorConfig(
                        options=["off", "standby"], translation_key="when_empty",
                        mode=selector.SelectSelectorMode.LIST)),
                vol.Required(CONF_STANDBY_LEVEL, default=data.get(CONF_STANDBY_LEVEL, DEFAULT_STANDBY_LEVEL)):
                    _percent(1, 60),
                vol.Required(CONF_STANDBY_KELVIN, default=data.get(CONF_STANDBY_KELVIN, DEFAULT_STANDBY_KELVIN)):
                    _kelvin(2000, 4000),
                vol.Required(CONF_MOTION_ALWAYS, default=data.get(CONF_MOTION_ALWAYS, False)):
                    selector.BooleanSelector(),
                vol.Required(CONF_NIGHTLIGHT_LEVEL, default=data.get(CONF_NIGHTLIGHT_LEVEL, DEFAULT_NIGHTLIGHT_LEVEL)):
                    _percent(1, 30),
                vol.Required(CONF_NIGHTLIGHT_KELVIN, default=data.get(CONF_NIGHTLIGHT_KELVIN, DEFAULT_NIGHTLIGHT_KELVIN)):
                    _kelvin(1800, 3000),
            }), {"collapsed": False})
        # Only the switch here. The fields came on the next screen: shown
        # here, they were changed and silently thrown away while the switch
        # was off, which is how night mode went unchanged twice on 30 September.
        fields[vol.Required("when")] = section(vol.Schema({
            vol.Required(CONF_OWN_WHEN, default=bool(data.get(CONF_OWN_WHEN))): selector.BooleanSelector(),
        }), {"collapsed": not data.get(CONF_OWN_WHEN)})
        fields[vol.Required("rhythm")] = section(vol.Schema({
            vol.Required(CONF_OWN_RHYTHM, default=bool(data.get(CONF_OWN_RHYTHM))): selector.BooleanSelector(),
        }), {"collapsed": not data.get(CONF_OWN_RHYTHM)})
        lamps_schema: Dict[Any, Any] = {}
        if lights:
            lamps_schema[vol.Optional(CONF_EXCLUDED, default=[e for e in data.get(CONF_EXCLUDED, []) if e in lights])] = (
                selector.EntitySelector(selector.EntitySelectorConfig(
                    domain="light", multiple=True, include_entities=lights)))
        lamps_schema.update({
            vol.Optional(CONF_LIGHT_SENSOR, description={"suggested_value": data.get(CONF_LIGHT_SENSOR)}):
                LIGHT_SENSOR,
            vol.Optional(CONF_MOTION_SENSOR, description={"suggested_value": data.get(CONF_MOTION_SENSOR)}):
                MOTION_SENSOR,
            vol.Required(CONF_MIN_LEVEL, default=data.get(CONF_MIN_LEVEL, DEFAULT_MIN_LEVEL)): _percent(1, 60),
            vol.Required(CONF_MAX_LEVEL, default=data.get(CONF_MAX_LEVEL, DEFAULT_MAX_LEVEL)): _percent(40, 100),
            vol.Required(CONF_MANUAL_HOLD, default=data.get(CONF_MANUAL_HOLD, DEFAULT_MANUAL_HOLD)):
                _minutes(480),
        })
        fields[vol.Required("lamps")] = section(vol.Schema(lamps_schema), {"collapsed": True})

        return self.async_show_form(
            step_id="settings", data_schema=vol.Schema(fields),
            description_placeholders={
                "room": data.get(CONF_NAME, ""),
                "reading": self._reading(data),
            },
        )

    def _reading(self, data: Dict[str, Any]) -> str:
        """What the sensor read as the screen opened, under the line's slider.

        A form cannot follow the sensor while it is open; the Calm card can.
        This is the next best thing, and it says which moment it is about.
        With the lamps on it says how much of the reading was daylight, the
        only part Calm holds the line against.
        """
        words = _READING[_lang(self.hass)]
        sensor = discovery.room_light_sensor(self.hass, data)
        state = self.hass.states.get(sensor) if sensor else None
        try:
            measured = round(float(state.state)) if state is not None else None
        except ValueError:
            measured = None
        if measured is None:
            return words["none"]
        room = self._get_entry().runtime_data.rooms.get(self._get_reconfigure_subentry().subentry_id)
        if room is not None and room.controller.lit:
            daylight = round(room.controller.daylight_lux)
            if measured - daylight >= 1:
                return words["lamps"].format(measured=measured, daylight=daylight)
        return words["plain"].format(measured=measured)

    async def _next_or_save(self) -> SubentryFlowResult:
        pending = self._pending or {}
        if pending.get(CONF_OWN_WHEN) and CONF_WHEN not in pending:
            return await self.async_step_room_when()
        if pending.get(CONF_OWN_RHYTHM) and CONF_RHYTHM not in pending:
            return await self.async_step_room_rhythm()
        return self._save(pending)

    async def async_step_room_when(self, user_input: Optional[Dict[str, Any]] = None) -> SubentryFlowResult:
        """This room's own "when", on the same screen Calm's own uses."""
        pending = self._pending or self._room()
        if user_input is not None:
            pending[CONF_WHEN] = _when_values(user_input)
            self._pending = pending
            return await self._next_or_save()
        own = self._room().get(CONF_WHEN) if self._room().get(CONF_OWN_WHEN) else None
        when = When.from_dict(own or self._get_entry().options.get(CONF_WHEN))
        return self.async_show_form(
            step_id="room_when", data_schema=_when_schema(when),
            description_placeholders={"room": pending.get(CONF_NAME, "")},
            last_step=not pending.get(CONF_OWN_RHYTHM),
        )

    async def async_step_room_rhythm(self, user_input: Optional[Dict[str, Any]] = None) -> SubentryFlowResult:
        pending = self._pending or self._room()
        if user_input is not None:
            pending[CONF_RHYTHM] = _rhythm_values(user_input)
            self._pending = pending
            return await self._next_or_save()
        own = self._room().get(CONF_RHYTHM) if self._room().get(CONF_OWN_RHYTHM) else None
        rhythm = own or self._get_entry().options.get(CONF_RHYTHM) or {}
        return self.async_show_form(
            step_id="room_rhythm", data_schema=_rhythm_schema(rhythm, colours=True),
            description_placeholders={"room": pending.get(CONF_NAME, "")},
        )

    async def async_step_overrides(self, user_input: Optional[Dict[str, Any]] = None) -> SubentryFlowResult:
        """Pick one to change, or start a new one."""
        data = self._room()
        overrides = OverrideSet.from_list(data.get(CONF_OVERRIDES) or [])
        if user_input is not None:
            choice = user_input["override"]
            if choice == NEW:
                return await self.async_step_override_new()
            self._override_key = choice
            return await self.async_step_override()
        options = [selector.SelectOptionDict(value=o.key, label=o.name) for o in overrides]
        options.append(selector.SelectOptionDict(
            value=NEW, label="+ " + ("Nieuwe overrule" if _lang(self.hass) == "nl" else "New override")))
        return self.async_show_form(
            step_id="overrides",
            data_schema=vol.Schema({vol.Required("override", default=NEW): selector.SelectSelector(
                selector.SelectSelectorConfig(options=options, mode=selector.SelectSelectorMode.LIST))}),
            last_step=False,
        )

    async def async_step_override_new(self, user_input: Optional[Dict[str, Any]] = None) -> SubentryFlowResult:
        """Start from a template, with a name filled in."""
        names = _TEMPLATE_NAMES[_lang(self.hass)]
        errors: Dict[str, str] = {}
        if user_input is not None and "template" in user_input:
            data = self._room()
            overrides = OverrideSet.from_list(data.get(CONF_OVERRIDES) or [])
            name = (user_input.get(CONF_NAME) or names.get(user_input["template"], "")).strip()
            key = unique_key(name, overrides.keys())
            if not key:
                errors[CONF_NAME] = "name_unusable"
            else:
                template = user_input["template"]
                override = (
                    Override(key=key, name=name) if template == "blank"
                    else from_template(template, name, key)
                )
                overrides.add(override)
                data[CONF_OVERRIDES] = overrides.to_list()
                self._override_key = key
                self._pending = data
                return await self.async_step_override()
        options = [selector.SelectOptionDict(value=k, label=names[k]) for k in (*TEMPLATES, "blank")]
        return self.async_show_form(
            step_id="override_new",
            data_schema=vol.Schema({
                vol.Required("template", default="work"): selector.SelectSelector(
                    selector.SelectSelectorConfig(options=options, mode=selector.SelectSelectorMode.LIST)),
                vol.Optional(CONF_NAME): selector.TextSelector(),
            }),
            errors=errors,
            last_step=False,
        )

    async def async_step_override(self, user_input: Optional[Dict[str, Any]] = None) -> SubentryFlowResult:
        """One override's answers: lamps, level, colour, and whether it starts itself."""
        data = self._pending or self._room()
        overrides = OverrideSet.from_list(data.get(CONF_OVERRIDES) or [])
        override = overrides.get(self._override_key)
        lights = discovery.room_lights(self.hass, data)
        if user_input is not None and "level_mode" in user_input:
            if user_input.get("remove"):
                overrides.remove(override.key)
            else:
                overrides.replace(_override_values(override, user_input, lights))
            data[CONF_OVERRIDES] = overrides.to_list()
            return self._save(data)

        on = [e for e in lights if override.lamps.get(e, None) is None or override.lamps[e].value != "off"]
        schema: Dict[Any, Any] = {
            vol.Optional("lights", default=on): selector.EntitySelector(selector.EntitySelectorConfig(
                domain="light", multiple=True, include_entities=lights)),
            vol.Required("level_mode", default=override.level_mode.value): selector.SelectSelector(
                selector.SelectSelectorConfig(options=["relative", "fixed"], translation_key="level_mode",
                                              mode=selector.SelectSelectorMode.LIST)),
            vol.Required("level", default=round(override.level * 100)): _percent(5, 300, 5),
            vol.Required("colour_mode", default=override.colour_mode.value): selector.SelectSelector(
                selector.SelectSelectorConfig(options=["follow", "shift", "fixed"], translation_key="colour_mode",
                                              mode=selector.SelectSelectorMode.LIST)),
            vol.Required("mired_shift", default=override.mired_shift): selector.NumberSelector(
                selector.NumberSelectorConfig(min=-120, max=200, step=10, unit_of_measurement="mired",
                                              mode=selector.NumberSelectorMode.SLIDER)),
            vol.Required("kelvin", default=override.kelvin): _kelvin(),
            vol.Required("auto", default=override.auto.value): selector.SelectSelector(
                selector.SelectSelectorConfig(options=["none", "time", "entity"], translation_key="auto",
                                              mode=selector.SelectSelectorMode.LIST)),
            vol.Required("auto_start", default=(override.auto_start.isoformat() if override.auto_start else "17:30:00")): TIME,
            vol.Required("auto_end", default=(override.auto_end.isoformat() if override.auto_end else "19:00:00")): TIME,
            vol.Optional("auto_entity", description={"suggested_value": override.auto_entity}):
                selector.EntitySelector(),
            vol.Required("auto_state", default=override.auto_state): selector.TextSelector(),
        }
        if self._override_in_store(override.key):
            schema[vol.Optional("remove", default=False)] = selector.BooleanSelector()
        return self.async_show_form(
            step_id="override", data_schema=vol.Schema(schema),
            description_placeholders={"name": override.name},
        )

    def _override_in_store(self, key: str) -> bool:
        stored = OverrideSet.from_list(self._room().get(CONF_OVERRIDES) or [])
        return key in stored


def _settings_values(data: Dict[str, Any], user_input: Dict[str, Any], has_motion: bool) -> Dict[str, Any]:
    """A room's settings screen, back into what is stored."""
    result = dict(data)
    light = user_input.get("light") or {}
    result[CONF_THRESHOLD] = float(light.get(CONF_THRESHOLD, data.get(CONF_THRESHOLD, DEFAULT_THRESHOLD)))
    result[CONF_BRIGHTNESS] = float(light.get(CONF_BRIGHTNESS, data.get(CONF_BRIGHTNESS, DEFAULT_BRIGHTNESS)))
    result[CONF_EVENING_BRIGHTNESS] = float(light.get(
        CONF_EVENING_BRIGHTNESS, data.get(CONF_EVENING_BRIGHTNESS, DEFAULT_EVENING_BRIGHTNESS)))
    if has_motion:
        motion = user_input.get("motion") or {}
        for key in (CONF_USE_MOTION, CONF_MOTION_HOLD, CONF_WHEN_EMPTY, CONF_STANDBY_LEVEL,
                    CONF_STANDBY_KELVIN, CONF_MOTION_ALWAYS, CONF_NIGHTLIGHT_LEVEL, CONF_NIGHTLIGHT_KELVIN):
            if key in motion:
                result[key] = motion[key]
    # Its own "when" and rhythm are asked for on the screens after this one;
    # switched off, the room follows Calm's again and keeps nothing of its own.
    result[CONF_OWN_WHEN] = bool((user_input.get("when") or {}).get(CONF_OWN_WHEN))
    result.pop(CONF_WHEN, None)
    result[CONF_OWN_RHYTHM] = bool((user_input.get("rhythm") or {}).get(CONF_OWN_RHYTHM))
    result.pop(CONF_RHYTHM, None)
    lamps = user_input.get("lamps") or {}
    result[CONF_EXCLUDED] = list(lamps.get(CONF_EXCLUDED) or [])
    for key in (CONF_LIGHT_SENSOR, CONF_MOTION_SENSOR):
        if lamps.get(key):
            result[key] = lamps[key]
        else:
            result.pop(key, None)
    for key in (CONF_MIN_LEVEL, CONF_MAX_LEVEL, CONF_MANUAL_HOLD):
        if key in lamps:
            result[key] = lamps[key]
    return result


def _override_values(override: Override, user_input: Dict[str, Any], lights: List[str]) -> Override:
    """An override's screen, back into an override."""
    from datetime import time

    from .core import AutoMode, ColourMode, LampRole, LevelMode

    chosen = set(user_input.get("lights") or [])
    lamps = {e: (LampRole.ON if e in chosen else LampRole.OFF) for e in lights}
    level_mode = LevelMode(user_input.get("level_mode", "relative"))
    level = float(user_input.get("level", 100)) / 100.0
    return Override(
        key=override.key,
        name=override.name,
        lamps=lamps,
        weights=dict(override.weights),
        level_mode=level_mode,
        level=level,
        colour_mode=ColourMode(user_input.get("colour_mode", "follow")),
        mired_shift=float(user_input.get("mired_shift", 0.0)),
        kelvin=float(user_input.get("kelvin", 2700)),
        transition_s=override.transition_s,
        auto=AutoMode(user_input.get("auto", "none")),
        auto_start=time.fromisoformat(user_input["auto_start"]) if user_input.get("auto_start") else None,
        auto_end=time.fromisoformat(user_input["auto_end"]) if user_input.get("auto_end") else None,
        auto_entity=user_input.get("auto_entity") or None,
        auto_state=str(user_input.get("auto_state") or "on"),
    )
