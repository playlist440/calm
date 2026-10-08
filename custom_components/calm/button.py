"""Take the room back now, rather than when the hand-hold runs out."""

from __future__ import annotations

from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import SUBENTRY_ROOM
from .descriptions import BUTTON_KEYS
from .entity import CalmEntity
from .hub import CalmHub

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry[CalmHub], async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    hub = entry.runtime_data
    for subentry_id, subentry in entry.subentries.items():
        if subentry.subentry_type == SUBENTRY_ROOM:
            async_add_entities(
                [ResumeButton(hub, subentry_id, dict(subentry.data))],
                config_subentry_id=subentry_id,
            )


class ResumeButton(CalmEntity, ButtonEntity):
    """Take the room back now, rather than when the hold runs out."""

    def __init__(self, hub: CalmHub, subentry_id: str, data: dict[str, Any]) -> None:
        super().__init__(hub, subentry_id, data, BUTTON_KEYS[0])

    async def async_press(self) -> None:
        if self.room:
            self.room.release_manual()
