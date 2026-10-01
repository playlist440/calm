"""What every Calm entity has in common: a room, a device, and staying current."""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import CONF_AREA, CONF_NAME, DOMAIN

if TYPE_CHECKING:
    from .hub import CalmHub
    from .room import CalmRoom


def device_name(data: dict) -> str:
    """"Calm", in the room's own area; "Calm <name>" for a room put together by hand.

    Home Assistant builds an entity id from the area, the device and the
    entity. A device called after its room, in that room, gives
    switch.keuken_keuken_calm. Called Calm, in the kitchen, it gives
    switch.keuken_calm, and it shows up on the kitchen's own page as "Calm".
    A room without an area has no area to lend it a name, so it carries its
    own.
    """
    if data.get(CONF_AREA):
        return "Calm"
    return f"Calm {data.get(CONF_NAME) or ''}".strip()


class CalmEntity(Entity):
    """One part of one room.

    Looks its room up by id every time rather than holding on to it: a room
    is rebuilt when its area changes in Home Assistant, and an entity holding
    the old one would go on reporting a room that no longer runs.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, hub: "CalmHub", subentry_id: str, data: dict, key: str) -> None:
        self.hub = hub
        self.subentry_id = subentry_id
        self._attr_unique_id = f"{subentry_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, subentry_id)},
            name=device_name(data),
            manufacturer="Calm",
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def room(self) -> Optional["CalmRoom"]:
        return self.hub.rooms.get(self.subentry_id)

    @property
    def available(self) -> bool:
        return self.room is not None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self.hub.add_listener(self.subentry_id, self._updated))

    def _updated(self) -> None:
        self.async_write_ha_state()
