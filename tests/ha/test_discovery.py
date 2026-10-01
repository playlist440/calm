"""What Calm finds in an area, checked against a real Home Assistant."""

from __future__ import annotations

from homeassistant.core import HomeAssistant

from custom_components.calm import discovery


async def test_the_kitchen_gives_exactly_its_two_lamps(hass: HomeAssistant, kitchen) -> None:
    """Chosen from the area, the kitchen has to come out as the two lamps it
    has in Calm today. The Hue room is itself a light in that area, and
    counting it would drive every bulb twice."""
    found = discovery.contents(hass, kitchen["area"])
    assert sorted(found.lights) == sorted(kitchen["lamps"])
    assert kitchen["hue_group"] not in found.lights


async def test_hue_s_own_room_sensor_is_preferred(hass: HomeAssistant, house, kitchen) -> None:
    """It combines every Hue sensor in the room and follows the Hue app, so
    a sensor added there later counts without anybody telling Calm. Checked
    with another sensor whose name sorts first, so alphabet cannot pass it."""
    house.hue_motion_sensor("aaa_extra", "Keuken")
    found = discovery.contents(hass, kitchen["area"])
    assert found.light_sensor == kitchen["room_lux"]
    assert found.motion_sensor == kitchen["room_motion"]
    assert kitchen["s7_lux"] in found.light_sensors


async def test_calm_s_own_sensors_are_never_candidates(hass: HomeAssistant, house, kitchen) -> None:
    """Version one gave every room an illuminance sensor of its own, the
    daylight estimate, and it turned up as a light sensor for its own room."""
    own = house.entity("sensor", "keuken_daglicht", area="Keuken",
                       device_class="illuminance", platform="calm")
    found = discovery.contents(hass, kitchen["area"])
    assert own not in found.light_sensors


async def test_a_home_assistant_light_group_is_not_a_lamp(hass: HomeAssistant, house, kitchen) -> None:
    group = house.entity("light", "alle_keukenlampen", area="Keuken", platform="group",
                         attributes={"entity_id": kitchen["lamps"]})
    found = discovery.contents(hass, kitchen["area"])
    assert group not in found.lights


async def test_a_disabled_lamp_is_left_out(hass: HomeAssistant, house, kitchen) -> None:
    lamp = house.entity("light", "kapot", area="Keuken", disabled=True)
    assert lamp not in discovery.contents(hass, kitchen["area"]).lights


async def test_a_lamp_s_own_area_wins_over_its_device_s(hass: HomeAssistant, house, kitchen) -> None:
    """A lamp on a two-gang module can hang in another room than the module."""
    device = house.device("module", "Keuken")
    elsewhere = house.entity("light", "gang_spot", device_id=device, area="Gang")
    assert elsewhere not in discovery.contents(hass, kitchen["area"]).lights
    assert elsewhere in discovery.contents(hass, house.area("Gang")).lights


async def test_a_room_without_a_light_sensor_is_not_suitable_yet(hass: HomeAssistant, house) -> None:
    for i in range(4):
        house.lamp(f"kantoor_{i}", "Kantoor")
    found = discovery.contents(hass, house.area("Kantoor"))
    assert len(found.lights) == 4
    assert not found.suitable


async def test_the_list_holds_every_area_with_a_lamp_and_nothing_else(hass: HomeAssistant, house, kitchen) -> None:
    house.hue_motion_sensor("buiten", "Achtertuin")
    house.lamp("bureau", "Kantoor")
    names = [c.name for c in discovery.scan(hass)]
    assert names == ["Kantoor", "Keuken"], "een ruimte zonder lampen stond in de lijst"


async def test_a_lamp_added_in_home_assistant_is_there_on_the_next_look(hass: HomeAssistant, house, kitchen) -> None:
    """Nothing is remembered: the live link is simply asking again."""
    before = discovery.contents(hass, kitchen["area"]).lights
    new = house.lamp("nieuw", "Keuken")
    after = discovery.contents(hass, kitchen["area"]).lights
    assert new not in before and new in after


async def test_a_room_put_together_by_hand_keeps_its_own_lamps(hass: HomeAssistant, kitchen) -> None:
    data = {"area_id": None, "lights": ["light.a", "light.b"], "light_sensor": kitchen["s7_lux"]}
    assert discovery.room_lights(hass, data) == ["light.a", "light.b"]
    assert discovery.room_light_sensor(hass, data) == kitchen["s7_lux"]


async def test_a_chosen_sensor_beats_the_area_s(hass: HomeAssistant, kitchen) -> None:
    data = {"area_id": kitchen["area"], "light_sensor": kitchen["s7_lux"]}
    assert discovery.room_light_sensor(hass, data) == kitchen["s7_lux"]
    assert discovery.room_light_sensor(hass, {"area_id": kitchen["area"]}) == kitchen["room_lux"]
