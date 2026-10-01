"""The sliders: from how dark the lamps take over, and how bright they get by day and by evening."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import LIGHT_LUX, PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import SUBENTRY_ROOM
from .entity import CalmEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    hub = entry.runtime_data
    for subentry_id, subentry in entry.subentries.items():
        if subentry.subentry_type != SUBENTRY_ROOM:
            continue
        data = dict(subentry.data)
        async_add_entities(
            [ThresholdNumber(hub, subentry_id, data), BrightnessNumber(hub, subentry_id, data),
             EveningBrightnessNumber(hub, subentry_id, data)],
            config_subentry_id=subentry_id,
        )


class ThresholdNumber(CalmEntity, NumberEntity):
    """"Lampen aan als het donkerder is dan": the line on the card's slider."""

    _attr_native_min_value = 1
    _attr_native_max_value = 300
    _attr_native_step = 1
    _attr_mode = NumberMode.BOX
    _attr_native_unit_of_measurement = LIGHT_LUX

    def __init__(self, hub, subentry_id: str, data: dict) -> None:
        super().__init__(hub, subentry_id, data, "threshold")

    @property
    def native_value(self) -> float | None:
        room = self.room
        return room.controller.settings.threshold_lux if room else None

    async def async_set_native_value(self, value: float) -> None:
        if self.room:
            self.room.set_threshold(value)


class BrightnessNumber(CalmEntity, NumberEntity):
    """"Hoe fel overdag": the most the lamps are asked for."""

    _attr_native_min_value = 5
    _attr_native_max_value = 100
    _attr_native_step = 5
    _attr_mode = NumberMode.SLIDER
    _attr_native_unit_of_measurement = PERCENTAGE

    def __init__(self, hub, subentry_id: str, data: dict) -> None:
        super().__init__(hub, subentry_id, data, "brightness")

    @property
    def native_value(self) -> float | None:
        room = self.room
        return round(room.controller.settings.brightness * 100) if room else None

    async def async_set_native_value(self, value: float) -> None:
        if self.room:
            self.room.set_brightness(value)


class EveningBrightnessNumber(CalmEntity, NumberEntity):
    """"Hoe fel 's avonds": what the daytime setting walks down to by bedtime."""

    _attr_native_min_value = 5
    _attr_native_max_value = 100
    _attr_native_step = 5
    _attr_mode = NumberMode.SLIDER
    _attr_native_unit_of_measurement = PERCENTAGE

    def __init__(self, hub, subentry_id: str, data: dict) -> None:
        super().__init__(hub, subentry_id, data, "evening_brightness")

    @property
    def native_value(self) -> float | None:
        room = self.room
        return round(room.controller.settings.evening_brightness * 100) if room else None

    async def async_set_native_value(self, value: float) -> None:
        if self.room:
            self.room.set_evening_brightness(value)
