"""Calm: light that is right on its own, in every room you choose.

One integration, with each room under it. The rooms come from Home
Assistant's own areas and follow them: a lamp added to an area in Home
Assistant is in the room for Calm too.
"""

from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig  # type: ignore[attr-defined]
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.typing import ConfigType

from .const import CARD_URL, DOMAIN
from .hub import CalmHub, async_remove_room_files

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.BUTTON, Platform.NUMBER, Platform.SENSOR, Platform.SWITCH]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type CalmConfigEntry = ConfigEntry[CalmHub]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Serve the Calm card, once, whatever happens to the entries."""
    card = Path(__file__).parent / "frontend" / "calm-card.js"
    if card.exists() and hass.http is not None:
        await hass.http.async_register_static_paths(
            [StaticPathConfig(CARD_URL, str(card), cache_headers=False)]
        )
        # The card is a nicety; the rooms are the point. A Home Assistant
        # without its frontend loaded must still get its lights.
        if "frontend" in hass.config.components:
            add_extra_js_url(hass, CARD_URL)
        else:
            _LOGGER.debug("Calm: frontend not loaded, the Calm card is not offered")
    return True


async def async_setup_entry(hass: HomeAssistant, entry: CalmConfigEntry) -> bool:
    hub = CalmHub(hass, entry)
    entry.runtime_data = hub
    await hub.async_setup()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _sweep(hass, entry, hub)
    entry.async_on_unload(entry.add_update_listener(_async_entry_updated))
    return True


def _sweep(hass: HomeAssistant, entry: ConfigEntry, hub: CalmHub) -> None:
    """Remove entities nothing will ever update again.

    A removed override leaves its switch in the registry, and Home Assistant
    shows it as unavailable forever. Only Calm's own entities of this entry
    are touched.
    """
    registry = er.async_get(hass)
    expected = hub.expected_unique_ids()
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        if entity.platform == DOMAIN and entity.unique_id not in expected:
            registry.async_remove(entity.entity_id)


async def async_unload_entry(hass: HomeAssistant, entry: CalmConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_unload()
    return unloaded


async def _async_entry_updated(hass: HomeAssistant, entry: CalmConfigEntry) -> None:
    """Reload when the settings changed, but not for a room's own slider."""
    hub = entry.runtime_data
    await hub.async_drop_removed_rooms(entry)
    if hub.needs_reload(entry):
        hass.config_entries.async_schedule_reload(entry.entry_id)


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Calm removed: delete every room's stored state and day files."""
    await async_remove_room_files(hass, entry.entry_id, entry.subentries)


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Version one kept one entry per room and cannot be carried over.

    Refusing makes Home Assistant say the entry needs removing; Calm 2 then
    sets a room up again in a minute.
    """
    if entry.version < 2:
        _LOGGER.error(
            "Calm: '%s' is a Calm 1 entry and cannot be migrated. Remove it and add Calm again.",
            entry.title,
        )
        return False
    return True
