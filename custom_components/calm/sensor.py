"""Why the room is the way it is, and the numbers behind it.

The Why sensor's state is one of a fixed set of reasons, translated by Home
Assistant; the sentence that spells it out, and what the Calm card needs to
draw its slider, are attributes. The numbers are diagnostic sensors.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN, SUBENTRY_ROOM
from .core import Why
from .descriptions import SENSORS, WHY_KEY, CalmSensorDescription
from .entity import CalmEntity
from .hub import CalmHub

PARALLEL_UPDATES = 0

#: Attributes that change with every reading: kept out of the recorder.
_VOLATILE = frozenset({
    "sentence", "daylight_lux", "lit", "target_lux", "confidence", "kelvin", "stale",
    "occupied", "returning_in_s", "manual_remaining_s", "reason", "rung", "scene", "mode",
    "unreachable", "inactive_because",
})


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry[CalmHub], async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    hub = entry.runtime_data
    for subentry_id, subentry in entry.subentries.items():
        if subentry.subentry_type != SUBENTRY_ROOM:
            continue
        data = dict(subentry.data)
        entities: list[SensorEntity] = [WhySensor(hub, subentry_id, data)]
        entities += [NumberSensor(hub, subentry_id, data, description) for description in SENSORS]
        async_add_entities(entities, config_subentry_id=subentry_id)


class WhySensor(CalmEntity, SensorEntity):
    """The reason the lamps are the way they are."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options: list[str] | None = [why.value for why in Why]  # noqa: RUF012
    _unrecorded_attributes = _VOLATILE

    def __init__(self, hub: CalmHub, subentry_id: str, data: dict[str, Any]) -> None:
        super().__init__(hub, subentry_id, data, WHY_KEY)
        self._state: tuple[str | None, dict[str, Any]] = (None, {})

    def _refresh(self) -> None:
        room = self.room
        if room is None:
            self._state = (None, {})
            return
        explanation = room.explanation()
        controller = room.controller
        self._state = (explanation.why.value, {
            **{k: v for k, v in explanation.detail.items() if v is not None},
            "sentence": explanation.sentence,
            "room": room.name,
            "lit": controller.lit,
            "daylight_lux": round(controller.daylight_lux, 1),
            "threshold_lux": controller.settings.threshold_lux,
            "threshold_entity": er.async_get(self.hass).async_get_entity_id(
                "number", DOMAIN, f"{self.subentry_id}_threshold"),
            "light_sensor": room.light_sensor,
            "motion_sensor": room.motion_sensor,
        })

    @property
    def native_value(self) -> str | None:
        return self._state[0]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self._state[1]


class NumberSensor(CalmEntity, SensorEntity):
    """One of the numbers behind the reason."""

    entity_description: CalmSensorDescription

    def __init__(self, hub: CalmHub, subentry_id: str, data: dict[str, Any],
                 description: CalmSensorDescription) -> None:
        super().__init__(hub, subentry_id, data, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> float | int | None:
        room = self.room
        return self.entity_description.value_fn(room) if room else None
