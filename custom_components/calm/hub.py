"""Every room Calm runs, and the settings they share.

The hub owns what is common: the "when" and the day's rhythm that every
room follows unless it has its own, and the live link to Home Assistant's
areas. When a lamp is added to an area, the room built on that area is
rebuilt with it, and nobody has to tell Calm.
"""

from __future__ import annotations

import logging
from datetime import time
from typing import Any, Callable, Dict, List, Mapping, Optional

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.debounce import Debouncer

from . import discovery
from .conditions import When
from .const import (
    CONF_BED,
    CONF_COOL_KELVIN,
    CONF_OWN_RHYTHM,
    CONF_OWN_WHEN,
    CONF_RHYTHM,
    CONF_WAKE,
    CONF_WARM_KELVIN,
    CONF_WHEN,
    DEFAULT_BED,
    DEFAULT_COOL_KELVIN,
    DEFAULT_WAKE,
    DEFAULT_WARM_KELVIN,
    DOMAIN,
    SUBENTRY_ROOM,
)
from .core import DayCurveConfig
from .room import CalmRoom

_LOGGER = logging.getLogger(__name__)

def rhythm_from(data: Optional[Mapping[str, Any]]) -> DayCurveConfig:
    data = data or {}
    return DayCurveConfig(
        wake=_time(data.get(CONF_WAKE), DEFAULT_WAKE),
        bed=_time(data.get(CONF_BED), DEFAULT_BED),
        warm_kelvin=float(data.get(CONF_WARM_KELVIN, DEFAULT_WARM_KELVIN)),
        cool_kelvin=float(data.get(CONF_COOL_KELVIN, DEFAULT_COOL_KELVIN)),
    )


def _time(value, default: str) -> time:
    try:
        return time.fromisoformat(str(value or default))
    except ValueError:
        return time.fromisoformat(default)


class CalmHub:
    """One integration entry: the shared settings and every room under it."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        options = entry.options
        self.when = When.from_dict(options.get(CONF_WHEN))
        self.rhythm = rhythm_from(options.get(CONF_RHYTHM))
        self.rooms: Dict[str, CalmRoom] = {}
        self._listeners: Dict[str, List[Callable[[], None]]] = {}
        self._unsubscribe: List[Callable[[], None]] = []
        #: What each room was built from, to tell a change worth a rebuild
        #: from a registry event about something else entirely.
        self._built_from: Dict[str, tuple] = {}
        self._rescan = Debouncer(
            hass, _LOGGER, cooldown=2.0, immediate=False, function=self._async_rescan
        )

    # -- what rooms inherit ---------------------------------------------

    def when_for(self, data: Mapping[str, Any]) -> When:
        if data.get(CONF_OWN_WHEN) and data.get(CONF_WHEN):
            return When.from_dict(data.get(CONF_WHEN))
        return self.when

    def rhythm_for(self, data: Mapping[str, Any]) -> DayCurveConfig:
        if data.get(CONF_OWN_RHYTHM) and data.get(CONF_RHYTHM):
            return rhythm_from(data.get(CONF_RHYTHM))
        return self.rhythm

    # -- lifecycle ------------------------------------------------------

    async def async_setup(self) -> None:
        for subentry_id, subentry in self.entry.subentries.items():
            if subentry.subentry_type == SUBENTRY_ROOM:
                self._place_device(subentry_id, dict(subentry.data))
                await self._async_start_room(subentry_id, dict(subentry.data))
        for event in (
            er.EVENT_ENTITY_REGISTRY_UPDATED,
            dr.EVENT_DEVICE_REGISTRY_UPDATED,
            ar.EVENT_AREA_REGISTRY_UPDATED,
        ):
            self._unsubscribe.append(self.hass.bus.async_listen(event, self._registry_changed))

    def _place_device(self, subentry_id: str, data: Dict[str, Any]) -> None:
        """Create the room's device in its area before any entity is made.

        Done first, and not by the first entity, because Home Assistant names
        every entity after the area its device is in at that moment: done any
        later and the first entity of a room is named differently from the rest.
        """
        from .entity import device_name

        registry = dr.async_get(self.hass)
        device = registry.async_get_or_create(
            config_entry_id=self.entry.entry_id,
            config_subentry_id=subentry_id,
            identifiers={(DOMAIN, subentry_id)},
            name=device_name(data),
            manufacturer="Calm",
            entry_type=dr.DeviceEntryType.SERVICE,
        )
        area_id = data.get("area_id")
        if area_id and device.area_id is None:
            registry.async_update_device(device.id, area_id=area_id)

    def expected_unique_ids(self) -> set:
        """Every entity the rooms under this entry should have right now."""
        from .core import OverrideSet
        from .sensor import NUMBERS

        expected = set()
        for subentry_id, subentry in self.entry.subentries.items():
            if subentry.subentry_type != SUBENTRY_ROOM:
                continue
            keys = ["calm", "why", "threshold", "brightness", "evening_brightness", "resume",
                    *(d.key for d in NUMBERS)]
            keys += [f"override_{o.key}" for o in OverrideSet.from_list(subentry.data.get("overrides") or [])]
            expected |= {f"{subentry_id}_{key}" for key in keys}
        return expected

    async def async_unload(self) -> None:
        for remove in self._unsubscribe:
            remove()
        self._unsubscribe.clear()
        self._rescan.async_cancel()
        for room in list(self.rooms.values()):
            await room.async_stop()
        self.rooms.clear()

    async def _async_start_room(self, subentry_id: str, data: Dict[str, Any]) -> None:
        room = CalmRoom(self, subentry_id, data)
        self.rooms[subentry_id] = room
        self._built_from[subentry_id] = self._fingerprint(data)
        await room.async_start()

    def _fingerprint(self, data: Mapping[str, Any]) -> tuple:
        return (
            tuple(discovery.room_lights(self.hass, dict(data))),
            discovery.room_light_sensor(self.hass, dict(data)),
            discovery.room_motion_sensor(self.hass, dict(data)),
        )

    # -- the live link --------------------------------------------------

    @callback
    def _registry_changed(self, event: Event) -> None:
        # Registry events come in bursts when an integration loads; one look
        # after they settle is enough.
        self._rescan.async_schedule_call()

    async def _async_rescan(self) -> None:
        for subentry_id, room in list(self.rooms.items()):
            fingerprint = self._fingerprint(room.data)
            if fingerprint == self._built_from.get(subentry_id):
                continue
            _LOGGER.info("%s: de ruimte in Home Assistant is veranderd, Calm neemt het over", room.name)
            await room.async_stop()
            await self._async_start_room(subentry_id, room.data)
            self.notify(subentry_id)

    # -- settings written by a running room --------------------------------

    @callback
    def remember_live(self, subentry_id: str, key: str, value: Any) -> None:
        """Store a slider's new value on the room's subentry, without a reload."""
        subentry = self.entry.subentries.get(subentry_id)
        if subentry is None:
            return
        data = {**subentry.data, key: value}
        self.hass.config_entries.async_update_subentry(self.entry, subentry, data=data)

    def needs_reload(self, entry: ConfigEntry) -> bool:
        """Whether a change to the entry is more than a room's own slider.

        A room that moves its own slider updates what it runs on before it
        writes it down, so its echo compares equal and nothing restarts.
        Anything else that differs was changed somewhere else: rooms added or
        removed, shared settings, a room's settings screen. Reload.
        """
        rooms = {k for k, v in entry.subentries.items() if v.subentry_type == SUBENTRY_ROOM}
        if rooms != set(self.rooms):
            return True
        if (When.from_dict(entry.options.get(CONF_WHEN)) != self.when
                or rhythm_from(entry.options.get(CONF_RHYTHM)) != self.rhythm):
            return True
        return any(
            dict(entry.subentries[subentry_id].data) != room.data
            for subentry_id, room in self.rooms.items()
        )

    # -- telling the entities -------------------------------------------

    @callback
    def add_listener(self, subentry_id: str, update: Callable[[], None]) -> Callable[[], None]:
        self._listeners.setdefault(subentry_id, []).append(update)

        def remove() -> None:
            self._listeners.get(subentry_id, []).remove(update)

        return remove

    @callback
    def notify(self, subentry_id: str) -> None:
        for update in list(self._listeners.get(subentry_id, [])):
            update()
