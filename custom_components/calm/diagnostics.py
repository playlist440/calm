"""Diagnostics: what each room believes, for a bug report."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .hub import CalmHub


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: ConfigEntry[CalmHub]) -> dict[str, Any]:
    hub = entry.runtime_data
    rooms: dict[str, Any] = {}
    for subentry_id, room in hub.rooms.items():
        controller = room.controller
        command = controller.last_command
        rooms[subentry_id] = {
            "name": room.name,
            "settings": dict(room.data),
            "lights": room.lights,
            "light_sensor": room.light_sensor,
            "motion_sensor": room.motion_sensor,
            "switch_on": room.switch_on,
            "lit": controller.lit,
            "manual": controller.manual,
            "daylight_lux": controller.daylight_lux,
            "estimator": controller.estimator.snapshot(),
            "mapping": controller.mapping.snapshot(),
            "last_command": None if command is None else {
                "action": command.action.value,
                "master": command.master,
                "mired": command.mired,
                "brightness": command.brightness,
                "transition_s": command.transition_s,
                "diagnostics": command.diagnostics,
            },
            "why": room.explanation().why.value,
            "commands_sent": room.commands_sent,
        }
    return {"options": dict(entry.options), "rooms": rooms}
