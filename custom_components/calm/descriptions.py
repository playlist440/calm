"""Every entity a room has, described once.

The platforms build their entities from these, and the hub derives the
unique ids a room should have from the same list, so the two cannot drift.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import LIGHT_LUX, PERCENTAGE, EntityCategory, UnitOfTemperature

from .const import CONF_OVERRIDES
from .core import OverrideSet

if TYPE_CHECKING:
    from .room import CalmRoom


@dataclass(frozen=True, kw_only=True)
class CalmSensorDescription(SensorEntityDescription):
    """A number behind the sentence, read off a running room."""

    value_fn: Callable[[CalmRoom], float | int | None]


def _diagnostic(key: str) -> Callable[[CalmRoom], float | None]:
    def read(room: CalmRoom) -> float | None:
        command = room.controller.last_command
        value = command.diagnostics.get(key) if command else None
        return round(float(value), 1) if value is not None else None

    return read


SENSORS: tuple[CalmSensorDescription, ...] = (
    CalmSensorDescription(
        key="daylight", translation_key="daylight", device_class=SensorDeviceClass.ILLUMINANCE,
        native_unit_of_measurement=LIGHT_LUX, state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda room: round(room.controller.daylight_lux, 1),
    ),
    CalmSensorDescription(
        key="target", translation_key="target", native_unit_of_measurement=LIGHT_LUX,
        state_class=SensorStateClass.MEASUREMENT, entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False, value_fn=_diagnostic("target_lux"),
    ),
    CalmSensorDescription(
        key="colour_temperature", translation_key="colour_temperature",
        native_unit_of_measurement=UnitOfTemperature.KELVIN, state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC, entity_registry_enabled_default=False,
        value_fn=lambda room: round(1_000_000.0 / room.controller.mired) if room.controller.mired else None,
    ),
    CalmSensorDescription(
        key="confidence", translation_key="confidence", native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT, entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda room: round(room.controller.estimator.confidence * 100),
    ),
    CalmSensorDescription(
        key="lamp_contribution", translation_key="lamp_contribution", native_unit_of_measurement=LIGHT_LUX,
        state_class=SensorStateClass.MEASUREMENT, entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda room: round(room.controller.estimator.full_contribution, 1),
    ),
    CalmSensorDescription(
        key="commands_sent", translation_key="commands_sent", state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC, entity_registry_enabled_default=False,
        value_fn=lambda room: room.commands_sent,
    ),
)

#: The room's entities apart from the sensors above and one switch per override.
SWITCH_KEY = "calm"
WHY_KEY = "why"
NUMBER_KEYS = ("threshold", "brightness", "evening_brightness")
BUTTON_KEYS = ("resume",)


def override_key(key: str) -> str:
    return f"override_{key}"


def room_entity_keys(data: Mapping[str, Any]) -> list[str]:
    """The keys of every entity a room with these settings has."""
    keys = [SWITCH_KEY, WHY_KEY, *NUMBER_KEYS, *BUTTON_KEYS, *(d.key for d in SENSORS)]
    keys += [override_key(o.key) for o in OverrideSet.from_list(data.get(CONF_OVERRIDES) or [])]
    return keys
