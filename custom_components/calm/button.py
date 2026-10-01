"""Take the room back now, rather than when the hand-hold runs out."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import SUBENTRY_ROOM
from .entity import CalmEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    hub = entry.runtime_data
    for subentry_id, subentry in entry.subentries.items():
        if subentry.subentry_type == SUBENTRY_ROOM:
            async_add_entities(
                [ResumeButton(hub, subentry_id, dict(subentry.data))],
                config_subentry_id=subentry_id,
            )


class ResumeButton(CalmEntity, ButtonEntity):
    def __init__(self, hub, subentry_id: str, data: dict) -> None:
        super().__init__(hub, subentry_id, data, "resume")

    async def async_press(self) -> None:
        if self.room:
            self.room.release_manual()
