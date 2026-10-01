"""What an area in Home Assistant has in it, as far as Calm is concerned.

The whole of "set it up once in Home Assistant and Calm follows" rests on
this file. Every question is answered from the registries as they are now,
never remembered, so that a lamp moved to another room in Home Assistant is
in the other room for Calm on the next look.

Three things to leave out, and each was a real surprise in a real house:

* **Groups.** A Hue room is itself a light entity in that area, and so is a
  Home Assistant light group. Counting them as lamps drives every bulb twice.
* **Calm's own entities.** Version one gave every room an illuminance sensor
  of its own (the daylight estimate), which then turned up as a candidate
  light sensor for the same room.
* **Everything that is not a light.** Obvious, but motion "sensors" include a
  camera's person detection, and a garden with two cameras had twenty-eight.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, List, Optional

from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN

MOTION_CLASSES = {
    BinarySensorDeviceClass.MOTION,
    BinarySensorDeviceClass.OCCUPANCY,
    BinarySensorDeviceClass.PRESENCE,
}

#: What the Hue bridge calls the devices standing for a whole room or zone.
#: Their sensors combine every Hue sensor in that room, and follow the Hue
#: app when somebody adds one: the best candidate there is.
HUE_ROOM_MODELS = {"room", "zone"}


@dataclass(frozen=True)
class AreaContents:
    """One area, as Calm sees it."""

    area_id: str
    name: str
    lights: List[str] = field(default_factory=list)
    light_sensors: List[str] = field(default_factory=list)
    motion_sensors: List[str] = field(default_factory=list)

    @property
    def light_sensor(self) -> Optional[str]:
        """The one Calm would pick, best first."""
        return self.light_sensors[0] if self.light_sensors else None

    @property
    def motion_sensor(self) -> Optional[str]:
        return self.motion_sensors[0] if self.motion_sensors else None

    @property
    def suitable(self) -> bool:
        """At least one lamp, and something to tell daylight by."""
        return bool(self.lights) and bool(self.light_sensors)


def scan(hass: HomeAssistant) -> List[AreaContents]:
    """Every area with at least one lamp in it, in the order people see them."""
    areas = ar.async_get(hass)
    found = [contents(hass, area.id) for area in areas.async_list_areas()]
    return sorted(
        (c for c in found if c is not None and c.lights),
        key=lambda c: c.name.casefold(),
    )


def contents(hass: HomeAssistant, area_id: str) -> Optional[AreaContents]:
    """What one area has in it, right now."""
    area = ar.async_get(hass).async_get_area(area_id)
    if area is None:
        return None
    lights, lux, motion = [], [], []
    for entry in _entities_in(hass, area_id):
        domain = entry.entity_id.split(".", 1)[0]
        if domain == "light":
            if not _is_group(hass, entry):
                lights.append(entry.entity_id)
        elif domain == "sensor" and _device_class(entry) == SensorDeviceClass.ILLUMINANCE:
            lux.append(entry)
        elif domain == "binary_sensor" and _device_class(entry) in MOTION_CLASSES:
            motion.append(entry)
    return AreaContents(
        area_id=area_id,
        name=area.name,
        lights=sorted(lights),
        light_sensors=[e.entity_id for e in _best_first(hass, lux)],
        motion_sensors=[e.entity_id for e in _best_first(hass, motion)],
    )


def _entities_in(hass: HomeAssistant, area_id: str) -> Iterable[er.RegistryEntry]:
    """Entities in an area, directly or through their device.

    An entity's own area wins over its device's: a lamp on a two-gang
    module can sit in a different room from the module.
    """
    entities = er.async_get(hass)
    devices = dr.async_get(hass)
    seen = set()
    for entry in er.async_entries_for_area(entities, area_id):
        seen.add(entry.entity_id)
        if _usable(entry):
            yield entry
    for device in dr.async_entries_for_area(devices, area_id):
        for entry in er.async_entries_for_device(entities, device.id):
            if entry.entity_id in seen or entry.area_id not in (None, area_id):
                continue
            seen.add(entry.entity_id)
            if _usable(entry):
                yield entry


def _usable(entry: er.RegistryEntry) -> bool:
    return entry.disabled_by is None and entry.platform != DOMAIN


def _device_class(entry: er.RegistryEntry):
    return entry.device_class or entry.original_device_class


def _is_group(hass: HomeAssistant, entry: er.RegistryEntry) -> bool:
    """A light that stands for other lights, not a lamp of its own."""
    if entry.platform == "group":
        return True
    state = hass.states.get(entry.entity_id)
    if state is not None:
        if state.attributes.get("is_hue_group"):
            return True
        # Home Assistant's own light groups list their members.
        if state.attributes.get(ATTR_ENTITY_ID):
            return True
    return _hue_room_device(hass, entry)


def _hue_room_device(hass: HomeAssistant, entry: er.RegistryEntry) -> bool:
    if entry.platform != "hue" or entry.device_id is None:
        return False
    device = dr.async_get(hass).async_get(entry.device_id)
    return device is not None and (device.model or "").casefold() in HUE_ROOM_MODELS


def _best_first(hass: HomeAssistant, entries: List[er.RegistryEntry]) -> List[er.RegistryEntry]:
    """Hue's own room-wide sensor first, then the rest by name."""
    return sorted(
        entries,
        key=lambda e: (0 if _hue_room_device(hass, e) else 1, e.entity_id),
    )


def room_lights(hass: HomeAssistant, data: dict) -> List[str]:
    """The lamps a room drives: its area's, live, or the ones chosen by hand."""
    area_id = data.get("area_id")
    if area_id:
        found = contents(hass, area_id)
        return list(found.lights) if found else []
    return list(data.get("lights") or [])


def room_light_sensor(hass: HomeAssistant, data: dict) -> Optional[str]:
    chosen = data.get("light_sensor")
    if chosen:
        return chosen
    area_id = data.get("area_id")
    found = contents(hass, area_id) if area_id else None
    return found.light_sensor if found else None


def room_motion_sensor(hass: HomeAssistant, data: dict) -> Optional[str]:
    chosen = data.get("motion_sensor")
    if chosen:
        return chosen
    area_id = data.get("area_id")
    found = contents(hass, area_id) if area_id else None
    return found.motion_sensor if found else None
