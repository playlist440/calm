"""Why the room is the way it is, and the numbers behind it.

"Waarom" is the one sensor meant for people: a sentence. It also carries
what the Calm card needs to draw its slider, so the card can be pointed at a
single entity. The numbers are there too, for whoever wants them, but kept
out of sight by default.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import LIGHT_LUX, PERCENTAGE, UnitOfTemperature, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN, SUBENTRY_ROOM
from .entity import CalmEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    hub = entry.runtime_data
    for subentry_id, subentry in entry.subentries.items():
        if subentry.subentry_type != SUBENTRY_ROOM:
            continue
        data = dict(subentry.data)
        entities: list = [WhySensor(hub, subentry_id, data)]
        entities += [NumberSensor(hub, subentry_id, data, d) for d in NUMBERS]
        async_add_entities(entities, config_subentry_id=subentry_id)


class WhySensor(CalmEntity, SensorEntity):
    """One sentence: why the lamps are the way they are."""

    def __init__(self, hub, subentry_id: str, data: dict) -> None:
        super().__init__(hub, subentry_id, data, "why")

    @property
    def native_value(self) -> Optional[str]:
        room = self.room
        return room.explanation().sentence if room else None

    @property
    def extra_state_attributes(self) -> dict:
        room = self.room
        if room is None:
            return {}
        explanation = room.explanation()
        controller = room.controller
        return {
            **{k: v for k, v in explanation.detail.items() if v is not None},
            "why": explanation.why.value,
            "room": room.name,
            "lit": controller.lit,
            "daylight_lux": round(controller.daylight_lux, 1),
            "threshold_lux": controller.settings.threshold_lux,
            "threshold_entity": er.async_get(self.hass).async_get_entity_id(
                "number", DOMAIN, f"{self.subentry_id}_threshold"
            ),
            "light_sensor": room.light_sensor,
            "motion_sensor": room.motion_sensor,
        }


@dataclass(frozen=True)
class NumberDescription:
    key: str
    value: Callable[[Any], Optional[float]]
    unit: Optional[str] = None
    device_class: Optional[SensorDeviceClass] = None
    enabled: bool = False


NUMBERS = (
    NumberDescription(
        "daylight", lambda room: round(room.controller.daylight_lux, 1),
        unit=LIGHT_LUX, device_class=SensorDeviceClass.ILLUMINANCE, enabled=True,
    ),
    NumberDescription(
        "target", lambda room: _diag(room, "target_lux"), unit=LIGHT_LUX,
    ),
    NumberDescription(
        "colour_temperature",
        lambda room: round(1_000_000.0 / room.controller.mired) if room.controller.mired else None,
        unit="K",
    ),
    NumberDescription(
        "confidence", lambda room: round(room.controller.estimator.confidence * 100),
        unit=PERCENTAGE,
    ),
    NumberDescription(
        "lamp_contribution", lambda room: round(room.controller.estimator.full_contribution, 1),
        unit=LIGHT_LUX,
    ),
    NumberDescription("commands_sent", lambda room: room.commands_sent),
)


def _diag(room, key: str) -> Optional[float]:
    command = room.controller.last_command
    value = command.diagnostics.get(key) if command else None
    return round(float(value), 1) if value is not None else None


class NumberSensor(CalmEntity, SensorEntity):
    """One of the numbers behind the sentence. Out of sight by default.

    The daylight estimate is the exception: it is what the room compares
    with the line, and anybody wondering why the lamps are off wants it.
    Still marked diagnostic, so it does not crowd the room's page.
    """

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, hub, subentry_id: str, data: dict, description: NumberDescription) -> None:
        super().__init__(hub, subentry_id, data, description.key)
        self._description = description
        self._attr_native_unit_of_measurement = description.unit
        self._attr_device_class = description.device_class
        self._attr_entity_registry_enabled_default = description.enabled
        if description.key == "commands_sent":
            self._attr_state_class = SensorStateClass.TOTAL_INCREASING

    @property
    def native_value(self) -> Optional[float]:
        room = self.room
        return self._description.value(room) if room else None
