"""A real Home Assistant for the integration's tests, and a house to put in it."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Let Home Assistant load Calm from custom_components."""
    yield


@dataclass
class House:
    """Builds areas, devices and entities the way integrations register them."""

    hass: HomeAssistant
    hue: MockConfigEntry
    areas: Dict[str, str] = field(default_factory=dict)

    def area(self, name: str) -> str:
        if name not in self.areas:
            self.areas[name] = ar.async_get(self.hass).async_create(name).id
        return self.areas[name]

    def device(self, name: str, area: str, model: str = "Hue bulb") -> str:
        device = dr.async_get(self.hass).async_get_or_create(
            config_entry_id=self.hue.entry_id,
            identifiers={("hue", name)},
            name=name,
            model=model,
        )
        dr.async_get(self.hass).async_update_device(device.id, area_id=self.area(area))
        return device.id

    def entity(
        self,
        domain: str,
        object_id: str,
        device_id: Optional[str] = None,
        area: Optional[str] = None,
        device_class: Optional[str] = None,
        platform: str = "hue",
        state: str = "on",
        attributes: Optional[dict] = None,
        disabled: bool = False,
    ) -> str:
        entry = er.async_get(self.hass).async_get_or_create(
            domain, platform, object_id,
            suggested_object_id=object_id,
            device_id=device_id,
            original_device_class=device_class,
            disabled_by=er.RegistryEntryDisabler.USER if disabled else None,
        )
        if area is not None:
            er.async_get(self.hass).async_update_entity(entry.entity_id, area_id=self.area(area))
        self.hass.states.async_set(entry.entity_id, state, attributes or {})
        return entry.entity_id

    def lamp(self, name: str, area: str) -> str:
        return self.entity(
            "light", name, device_id=self.device(name, area),
            attributes={"supported_color_modes": ["color_temp"], "brightness": 128},
        )

    def hue_motion_sensor(self, name: str, area: str, lux: float = 12.0) -> tuple:
        device = self.device(name, area, model="Hue motion sensor")
        return (
            self.entity("sensor", f"{name}_illuminance", device_id=device,
                        device_class="illuminance", state=str(lux)),
            self.entity("binary_sensor", f"{name}_motion", device_id=device,
                        device_class="motion", state="off"),
        )

    def hue_room(self, name: str, area: str, lux: float = 12.0) -> dict:
        """What the Hue bridge adds for a whole room: a group, a light
        level and a motion sensor that combine everything in it."""
        device = self.device(f"{name} (Hue)", area, model="Room")
        return {
            "group": self.entity("light", f"{name}_group", device_id=device,
                                 attributes={"is_hue_group": True}),
            "lux": self.entity("sensor", f"{name}_licht", device_id=device,
                               device_class="illuminance", state=str(lux)),
            "motion": self.entity("binary_sensor", f"{name}_beweging", device_id=device,
                                  device_class="motion", state="off"),
        }


@pytest.fixture
async def house(hass: HomeAssistant) -> House:
    hue = MockConfigEntry(domain="hue", title="Hue")
    hue.add_to_hass(hass)
    return House(hass=hass, hue=hue)


@pytest.fixture
async def kitchen(house: House) -> dict:
    """His kitchen, as its registries actually looked on 29 September."""
    s7_lux, s7_motion = house.hue_motion_sensor("s7_wasruimte", "Keuken")
    room = house.hue_room("keuken", "Keuken")
    return {
        "area": house.area("Keuken"),
        "lamps": [house.lamp("bl3", "Keuken"), house.lamp("dimmable_light_1", "Keuken")],
        "s7_lux": s7_lux,
        "s7_motion": s7_motion,
        "room_lux": room["lux"],
        "room_motion": room["motion"],
        "hue_group": room["group"],
    }
