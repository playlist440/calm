"""The Calm switch for each room, and one switch per override."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import SUBENTRY_ROOM
from .core import OverrideSet
from .entity import CalmEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    hub = entry.runtime_data
    for subentry_id, subentry in entry.subentries.items():
        if subentry.subentry_type != SUBENTRY_ROOM:
            continue
        data = dict(subentry.data)
        entities: list = [CalmSwitch(hub, subentry_id, data)]
        for override in OverrideSet.from_list(data.get("overrides") or []):
            entities.append(OverrideSwitch(hub, subentry_id, data, override.key, override.name))
        async_add_entities(entities, config_subentry_id=subentry_id)


class CalmSwitch(CalmEntity, SwitchEntity, RestoreEntity):
    """On means Calm runs this room.

    The conditions set it when they change. In between, a person or an
    automation may flip it, and that holds until the conditions next change.
    """

    #: Named after its device alone: switch.keuken_calm.
    _attr_name = None

    def __init__(self, hub, subentry_id: str, data: dict) -> None:
        super().__init__(hub, subentry_id, data, "calm")
        self._attr_translation_key = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        room = self.room
        if room is not None and last is not None:
            room.restore_switch(last.state == STATE_ON)

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

    _attr_translation_key = "override"

    def __init__(self, hub, subentry_id: str, data: dict, key: str, name: str) -> None:
        super().__init__(hub, subentry_id, data, f"override_{key}")
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
