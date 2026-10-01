"""A room running inside a real Home Assistant."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from homeassistant.config_entries import ConfigSubentryData
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    async_mock_service,
)

from custom_components.calm.const import DOMAIN

EVENING = "2026-09-29 21:00:00+02:00"


async def amsterdam(hass: HomeAssistant) -> None:
    await hass.config.async_set_time_zone("Europe/Amsterdam")
    hass.config.latitude, hass.config.longitude = 52.09, 5.12


def home(hass: HomeAssistant, at_home: bool = True) -> None:
    hass.states.async_set("person.mason", "home" if at_home else "not_home")
    hass.states.async_set("zone.home", "1" if at_home else "0")


def room_data(kitchen: dict, **settings) -> dict:
    return {"area_id": kitchen["area"], "name": "Keuken", "threshold_lux": 25.0, **settings}


async def start(hass: HomeAssistant, kitchen: dict, options=None, **settings) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN, version=2, title="Calm",
        options=options or {"when": {"home": True}},
        subentries_data=[ConfigSubentryData(
            data=room_data(kitchen, **settings), subentry_type="room",
            title="Keuken", unique_id=kitchen["area"],
        )],
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def entity(hass: HomeAssistant, entry: MockConfigEntry, domain: str, key: str) -> str:
    subentry_id = next(iter(entry.subentries))
    entity_id = er.async_get(hass).async_get_entity_id(domain, DOMAIN, f"{subentry_id}_{key}")
    assert entity_id, f"geen {domain} voor {key}"
    return entity_id


@pytest.fixture(autouse=True)
def evening(freezer):
    """The clock is set before the house is built, so every state in it
    carries a time from this evening and not from whenever the tests run."""
    freezer.move_to(EVENING)
    return freezer


@pytest.fixture
def lamp_calls(hass: HomeAssistant):
    async_mock_service(hass, "light", "turn_off")
    return async_mock_service(hass, "light", "turn_on")


@pytest.fixture(autouse=True)
def lamps_answer(hass: HomeAssistant, lamp_calls):
    """Every room test has lamps that take commands."""
    return lamp_calls


async def test_a_room_becomes_a_device_with_its_parts(hass, kitchen, freezer) -> None:
    freezer.move_to(EVENING)
    await amsterdam(hass)
    home(hass)
    entry = await start(hass, kitchen)
    for domain, key in (("switch", "calm"), ("sensor", "why"), ("number", "threshold"),
                        ("number", "brightness"), ("number", "evening_brightness"),
                        ("button", "resume"), ("sensor", "daylight")):
        entity(hass, entry, domain, key)
    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, next(iter(entry.subentries))), entry.entry_id)
    assert device is not None and device.area_id == kitchen["area"], (
        "het apparaat van de ruimte staat niet in de ruimte zelf"
    )


async def test_a_dark_kitchen_with_somebody_home_is_lit(hass, kitchen, freezer, lamp_calls) -> None:
    freezer.move_to(EVENING)
    await amsterdam(hass)
    home(hass)
    hass.states.async_set(kitchen["room_lux"], "2", {"device_class": "illuminance"})
    entry = await start(hass, kitchen)
    assert lamp_calls, "de keuken bleef donker"
    lit = {e for call in lamp_calls for e in call.data["entity_id"]}
    assert lit == set(kitchen["lamps"])
    assert all("color_temp_kelvin" in call.data for call in lamp_calls)
    assert hass.states.get(entity(hass, entry, "switch", "calm")).state == "on"


async def test_nobody_home_keeps_it_dark_and_says_why(hass, kitchen, freezer, lamp_calls) -> None:
    freezer.move_to(EVENING)
    await amsterdam(hass)
    home(hass, at_home=False)
    hass.states.async_set(kitchen["room_lux"], "2")
    entry = await start(hass, kitchen)
    assert not lamp_calls
    why = hass.states.get(entity(hass, entry, "sensor", "why")).state
    assert "nobody is home" in why


async def test_coming_home_lights_the_room(hass, kitchen, freezer, lamp_calls) -> None:
    freezer.move_to(EVENING)
    await amsterdam(hass)
    home(hass, at_home=False)
    hass.states.async_set(kitchen["room_lux"], "2")
    entry = await start(hass, kitchen)
    home(hass, at_home=True)
    await hass.async_block_till_done()
    assert lamp_calls, "thuiskomen gaf geen licht"


async def test_walking_into_the_dark_kitchen_lights_it_at_once(hass, kitchen, freezer, lamp_calls) -> None:
    """29 September, 19:29, inside Home Assistant this time: the motion
    sensor's change is answered in the same moment."""
    freezer.move_to(EVENING)
    await amsterdam(hass)
    home(hass)
    hass.states.async_set(kitchen["room_lux"], "2")
    hass.states.async_set(kitchen["room_motion"], "off")
    freezer.tick(timedelta(hours=1))
    entry = await start(hass, kitchen, use_motion=True, when_empty="off")
    assert not lamp_calls, "de lege keuken ging aan"
    hass.states.async_set(kitchen["room_motion"], "on")
    await hass.async_block_till_done()
    assert lamp_calls, "binnenlopen in het donker gaf geen licht"
    assert lamp_calls[-1].data["transition"] <= 2.0


async def test_bright_daylight_keeps_the_lamps_out_from_the_start(hass, kitchen, freezer, lamp_calls) -> None:
    freezer.move_to("2026-09-29 13:00:00+02:00")
    await amsterdam(hass)
    home(hass)
    for lamp in kitchen["lamps"]:
        hass.states.async_set(lamp, "off", {"supported_color_modes": ["color_temp"]})
    hass.states.async_set(kitchen["room_lux"], "300")
    await start(hass, kitchen)
    assert not lamp_calls


async def test_lamps_burning_at_a_restart_in_daylight_are_put_out_not_left(hass, kitchen, freezer, lamp_calls) -> None:
    """Before, Calm took the burning lamps' light for daylight, saw enough of
    it, and left them burning all afternoon while saying there was enough
    daylight. Now they are its own again, and go out after the usual wait."""
    off_calls = async_mock_service(hass, "light", "turn_off")
    freezer.move_to("2026-09-29 13:00:00+02:00")
    await amsterdam(hass)
    home(hass)
    hass.states.async_set(kitchen["room_lux"], "300")
    entry = await start(hass, kitchen)
    room = entry.runtime_data.rooms[next(iter(entry.subentries))]
    assert room.controller.lit, "de brandende lampen werden niet als eigen licht gezien"
    for _ in range(25):
        freezer.tick(timedelta(seconds=30))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
    assert off_calls, "de lampen bleven branden in vol daglicht"
    assert not room.controller.lit


async def test_a_lamp_added_to_the_area_joins_by_itself(hass, house, kitchen, freezer, lamp_calls) -> None:
    """Set it up once in Home Assistant, and Calm follows."""
    freezer.move_to(EVENING)
    await amsterdam(hass)
    home(hass)
    hass.states.async_set(kitchen["room_lux"], "2")
    entry = await start(hass, kitchen)
    new = house.lamp("werkblad_2", "Keuken")
    freezer.tick(timedelta(seconds=5))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    room = entry.runtime_data.rooms[next(iter(entry.subentries))]
    assert new in room.lights, "de nieuwe lamp deed niet mee"


async def test_the_slider_applies_at_once_without_restarting_anything(hass, kitchen, freezer) -> None:
    freezer.move_to(EVENING)
    await amsterdam(hass)
    home(hass)
    hass.states.async_set(kitchen["room_lux"], "40")
    entry = await start(hass, kitchen)
    hub = entry.runtime_data
    room = hub.rooms[next(iter(entry.subentries))]
    await hass.services.async_call(
        "number", "set_value",
        {"entity_id": entity(hass, entry, "number", "threshold"), "value": 60},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert entry.runtime_data is hub and hub.rooms[next(iter(entry.subentries))] is room, (
        "de schuif herstartte de ruimte"
    )
    assert room.controller.settings.threshold_lux == 60
    assert next(iter(entry.subentries.values())).data["threshold_lux"] == 60, (
        "de nieuwe stand werd niet bewaard"
    )


async def test_a_hand_on_the_lamps_is_noticed(hass, kitchen, freezer, lamp_calls) -> None:
    freezer.move_to(EVENING)
    await amsterdam(hass)
    home(hass)
    hass.states.async_set(kitchen["room_lux"], "2")
    entry = await start(hass, kitchen)
    freezer.tick(timedelta(minutes=5))
    lamp = kitchen["lamps"][0]
    hass.states.async_set(lamp, "on", {"brightness": 20, "supported_color_modes": ["color_temp"]})
    await hass.async_block_till_done()
    room = entry.runtime_data.rooms[next(iter(entry.subentries))]
    assert room.controller.manual


async def test_the_room_keeps_its_own_day_file(hass, kitchen, freezer) -> None:
    freezer.move_to(EVENING)
    await amsterdam(hass)
    home(hass)
    hass.states.async_set(kitchen["room_lux"], "2")
    await start(hass, kitchen)
    await hass.async_block_till_done()
    files = list(Path(hass.config.path("calm", "keuken")).glob("*.csv"))
    assert files, "er werd geen dagverslag geschreven"
    lines = files[0].read_text().splitlines()
    assert lines[0].startswith("time,measured_lux")
    assert len(lines) >= 2


async def test_a_calm_1_entry_is_refused_with_a_clear_message(hass, caplog) -> None:
    old = MockConfigEntry(domain=DOMAIN, version=1, title="Keuken", data={"lights": []})
    old.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(old.entry_id)
    assert "Calm 1" in caplog.text


async def test_a_plain_dimmable_bulb_is_not_asked_for_a_colour(hass, house, kitchen, lamp_calls) -> None:
    """Asking a plain dimmable bulb for 2700 K is an error in the log every
    thirty seconds. It gets brightness, and the colour lamps get both."""
    await amsterdam(hass)
    home(hass)
    plain = house.entity("light", "gewone_lamp", device_id=house.device("gewoon", "Keuken"),
                         attributes={"supported_color_modes": ["brightness"]})
    hass.states.async_set(kitchen["room_lux"], "2")
    await start(hass, kitchen)
    for call in lamp_calls:
        if plain in call.data["entity_id"]:
            assert "color_temp_kelvin" not in call.data
            assert call.data["entity_id"] == [plain]
    assert any(plain in call.data["entity_id"] for call in lamp_calls), "de gewone lamp kreeg niets"


async def test_a_lamp_an_override_leaves_out_is_put_out(hass, kitchen) -> None:
    """Leaving a lamp out of an override has to put it out, not just stop
    driving it: otherwise it burns on while everything believes it dark."""
    off_calls = async_mock_service(hass, "light", "turn_off")
    await amsterdam(hass)
    home(hass)
    hass.states.async_set(kitchen["room_lux"], "2")
    lamp_a, lamp_b = kitchen["lamps"]
    entry = await start(hass, kitchen, overrides=[{
        "key": "film", "name": "Film", "lamps": {lamp_a: "off", lamp_b: "on"},
        "level_mode": "relative", "level": 0.2,
    }])
    hass.states.async_set(lamp_a, "on", {"brightness": 200, "supported_color_modes": ["color_temp"]},
                          context=None)
    room = entry.runtime_data.rooms[next(iter(entry.subentries))]
    room.attribution.forget()
    room.set_override("film", True)
    await hass.async_block_till_done()
    assert any(lamp_a in call.data["entity_id"] for call in off_calls), (
        "de lamp die niet meedeed bleef branden"
    )


async def test_a_day_file_from_calm_1_is_set_aside_not_mixed(hass, kitchen) -> None:
    """The day of the switch-over, the day file already exists with Calm 1's
    columns. Appending to it would put two kinds of line under one header."""
    await amsterdam(hass)
    home(hass)
    folder = Path(hass.config.path("calm", "keuken"))
    folder.mkdir(parents=True, exist_ok=True)
    old = folder / "2026-09-29.csv"
    old.write_text("time,measured_lux,daylight_lux,target_lux,master,kelvin,state\n2026-09-29T06:00:00+02:00,0,0,10,0,2200,off\n")
    hass.states.async_set(kitchen["room_lux"], "2")
    await start(hass, kitchen)
    await hass.async_block_till_done()
    assert (folder / "2026-09-29-eerder.csv").exists(), "het oude dagverslag werd niet apart gezet"
    header = (folder / "2026-09-29.csv").read_text().splitlines()[0]
    assert "rung" in header


async def test_switched_on_by_hand_in_night_mode_goes_out_when_everybody_leaves(hass, kitchen, lamp_calls) -> None:
    """30 September, 06:51: night mode said no, he switched Calm on by hand.
    That holds while night mode stays on, but once the house empties the
    conditions have changed, and the room follows them again."""
    await amsterdam(hass)
    home(hass)
    hass.states.async_set("binary_sensor.nachtmodus_actief", "on", {"friendly_name": "Nachtmodus actief"})
    hass.states.async_set(kitchen["room_lux"], "0")
    entry = await start(hass, kitchen, options={"when": {
        "home": True, "entity": "binary_sensor.nachtmodus_actief", "entity_state": "off"}})
    switch = entity(hass, entry, "switch", "calm")
    assert hass.states.get(switch).state == "off"
    await hass.services.async_call("switch", "turn_on", {"entity_id": switch}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get(switch).state == "on", "de hand op de schakelaar telde niet"

    hass.states.async_set("binary_sensor.nachtmodus_actief", "on",
                          {"friendly_name": "Nachtmodus actief", "changed": 1})
    await hass.async_block_till_done()
    assert hass.states.get(switch).state == "on", "dezelfde voorwaarde nam de hand terug"

    home(hass, at_home=False)
    await hass.async_block_till_done()
    assert hass.states.get(switch).state == "off", "iedereen weg, en Calm bleef aan"


async def test_a_condition_that_no_longer_exists_is_put_under_repairs(hass, kitchen) -> None:
    await amsterdam(hass)
    home(hass)
    hass.states.async_set("sensor.nachtmodus_actief", "unavailable",
                          {"friendly_name": "Nachtmodus actief", "restored": True})
    hass.states.async_set(kitchen["room_lux"], "0")
    entry = await start(hass, kitchen, options={"when": {
        "home": True, "entity": "sensor.nachtmodus_actief", "entity_state": "off"}})
    issues = ir.async_get(hass)
    issue = issues.async_get_issue(DOMAIN, "condition_missing_sensor.nachtmodus_actief")
    assert issue is not None, "niemand hoort dat de voorwaarde niet meer bestaat"
    why = hass.states.get(entity(hass, entry, "sensor", "why")).state
    assert "sensor.nachtmodus_actief" in why

    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert issues.async_get_issue(DOMAIN, "condition_missing_sensor.nachtmodus_actief") is None


async def test_a_lamp_still_burning_after_calm_put_the_room_out_is_put_out_once(hass, kitchen, freezer, lamp_calls) -> None:
    """An off command that gets lost leaves a lamp on all day while Calm
    believes the room dark. Calm looks once, after the fade, and tries again."""
    off_calls = async_mock_service(hass, "light", "turn_off")
    await amsterdam(hass)
    home(hass)
    for lamp in kitchen["lamps"]:
        hass.states.async_set(lamp, "on", {"brightness": 200, "supported_color_modes": ["color_temp"]})
    hass.states.async_set(kitchen["room_lux"], "2")
    entry = await start(hass, kitchen)
    room = entry.runtime_data.rooms[next(iter(entry.subentries))]
    room.set_switch(False)
    await hass.async_block_till_done()
    assert len(off_calls) == 1
    for _ in range(12):
        freezer.tick(timedelta(seconds=30))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
    assert len(off_calls) == 2, "de lamp die bleef branden werd niet nog eens uitgezet"
    assert set(off_calls[-1].data["entity_id"]) == set(kitchen["lamps"])
    assert not room.controller.manual


async def test_a_lamp_an_override_leaves_out_going_off_late_is_not_a_hand(hass, kitchen, freezer) -> None:
    """Out is what the override wanted for that lamp, however late the lamp
    says so. Taken as a hand it would put the whole room out."""
    await amsterdam(hass)
    home(hass)
    hass.states.async_set(kitchen["room_lux"], "2")
    lamp_a, lamp_b = kitchen["lamps"]
    hass.states.async_set(lamp_a, "on", {"brightness": 200, "supported_color_modes": ["color_temp"]})
    entry = await start(hass, kitchen, overrides=[{
        "key": "film", "name": "Film", "lamps": {lamp_a: "off", lamp_b: "on"},
        "level_mode": "relative", "level": 0.2,
    }])
    room = entry.runtime_data.rooms[next(iter(entry.subentries))]
    room.set_override("film", True)
    await hass.async_block_till_done()
    assert room.controller.lit
    freezer.tick(timedelta(minutes=3))
    hass.states.async_set(lamp_a, "off", {"supported_color_modes": ["color_temp"]})
    await hass.async_block_till_done()
    assert not room.controller.manual, "de lamp die de stand uit wilde werd een hand"


async def test_the_first_reading_waits_for_the_lamps_to_report(hass, kitchen, freezer, lamp_calls) -> None:
    """30 September, 21:14: Calm started before the Hue lamps had a state,
    and took their light for daylight. With the line below that light, the
    evening kitchen would have been left burning untended as "daylight"."""
    await amsterdam(hass)
    home(hass)
    for lamp in kitchen["lamps"]:
        hass.states.async_remove(lamp)
    hass.states.async_set(kitchen["room_lux"], "13")
    entry = await start(hass, kitchen, threshold_lux=5.0)
    room = entry.runtime_data.rooms[next(iter(entry.subentries))]
    assert not lamp_calls, "Calm besloot al voordat de lampen iets hadden gezegd"
    for lamp in kitchen["lamps"]:
        hass.states.async_set(lamp, "on", {"brightness": 230, "supported_color_modes": ["color_temp"]})
    await hass.async_block_till_done()
    assert room.controller.lit, "het licht van de lampen werd voor daglicht aangezien"
    assert room.controller.daylight_lux < 13.0


async def test_a_room_whose_lamps_never_report_starts_anyway(hass, kitchen, freezer, lamp_calls) -> None:
    await amsterdam(hass)
    home(hass)
    for lamp in kitchen["lamps"]:
        hass.states.async_remove(lamp)
    hass.states.async_set(kitchen["room_lux"], "2")
    await start(hass, kitchen)
    assert not lamp_calls
    for _ in range(5):
        freezer.tick(timedelta(seconds=30))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
    assert lamp_calls, "zonder bericht van de lampen begon de ruimte nooit"


async def test_the_evening_slider_applies_at_once_and_is_kept(hass, kitchen, freezer) -> None:
    await amsterdam(hass)
    home(hass)
    hass.states.async_set(kitchen["room_lux"], "2")
    entry = await start(hass, kitchen)
    hub = entry.runtime_data
    room = hub.rooms[next(iter(entry.subentries))]
    number = entity(hass, entry, "number", "evening_brightness")
    assert hass.states.get(number).state == "60", "de avondstand begon niet op 60%"
    await hass.services.async_call("number", "set_value", {"entity_id": number, "value": 45}, blocking=True)
    await hass.async_block_till_done()
    assert hub.rooms[next(iter(entry.subentries))] is room, "de schuif herstartte de ruimte"
    assert room.controller.settings.evening_brightness == pytest.approx(0.45)
    assert next(iter(entry.subentries.values())).data["evening_brightness"] == 45
