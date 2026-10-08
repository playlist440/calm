"""The Calm switch for each room, and one switch per override."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import SUBENTRY_ROOM
from .core import OverrideSet
from .descriptions import SWITCH_KEY, override_key
from .entity import CalmEntity
from .hub import CalmHub

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry[CalmHub], async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    hub = entry.runtime_data
    for subentry_id, subentry in entry.subentries.items():
        if subentry.subentry_type != SUBENTRY_ROOM:
            continue
        data = dict(subentry.data)
        entities: list[SwitchEntity] = [CalmSwitch(hub, subentry_id, data)]
        for override in OverrideSet.from_list(data.get("overrides") or []):
            entities.append(OverrideSwitch(hub, subentry_id, data, override.key, override.name))
        async_add_entities(entities, config_subentry_id=subentry_id)


class CalmSwitch(CalmEntity, SwitchEntity):
    """On means Calm runs this room.

    The conditions set it when they change. In between, a person or an
    automation may flip it, and that holds until the conditions next change.
    Its state before a restart is read back by the room itself, before the
    room's first run.
    """

    #: Named after its device alone: switch.keuken_calm.
    _attr_name = None

    def __init__(self, hub: CalmHub, subentry_id: str, data: dict[str, Any]) -> None:
        super().__init__(hub, subentry_id, data, SWITCH_KEY)
        self._attr_translation_key = None

    @property
    def is_on(self) -> bool:
        return bool(self.room and self.room.switch_on)

    async def async_turn_on(self, **kwargs: Any) -> None:
        if self.room:
            self.room.set_switch(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        if self.room:
            self.room.set_switch(False)


class OverrideSwitch(CalmEntity, SwitchEntity):
    """One override. On means it runs; turning on a second takes over.

    Not restored after a restart on purpose: an override is somebody's
    choice for now, and "now" does not survive a restart.
    """

    def __init__(self, hub: CalmHub, subentry_id: str, data: dict[str, Any], key: str, name: str) -> None:
        super().__init__(hub, subentry_id, data, override_key(key))
        self._attr_translation_key = "override"
        self._attr_translation_placeholders = {"name": name}
        self._key = key

    @property
    def is_on(self) -> bool:
        room = self.room
        if room is None:
            return False
        active = room.controller.active_override
        return active is not None and active.key == self._key

    async def async_turn_on(self, **kwargs: Any) -> None:
        if self.room:
            self.room.set_override(self._key, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        if self.room:
            self.room.set_override(self._key, False)
