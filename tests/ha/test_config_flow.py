"""Installing Calm and changing it, walked through the way a person would."""

from __future__ import annotations

import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType, InvalidData
from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.calm.const import DOMAIN

EVENING = "2026-09-29 21:00:00+02:00"


@pytest.fixture(autouse=True)
def evening(freezer):
    freezer.move_to(EVENING)


@pytest.fixture(autouse=True)
def lamps(hass: HomeAssistant):
    async_mock_service(hass, "light", "turn_off")
    return async_mock_service(hass, "light", "turn_on")


def answers(entity=None, state="off", home=True, window=False,
            start="06:00:00", end="23:30:00", manual=False) -> dict:
    """The "when" screen filled in, the way the screen folds it."""
    condition = {"entity_state": state, **({"entity": entity} if entity else {})}
    return {"home": home, "condition": condition,
            "hours": {"window": window, "start": start, "end": end},
            "yourself": {"manual": manual}}


async def install(hass: HomeAssistant, rooms, sensors=None, when=None, rhythm=None):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "user"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"rooms": rooms})
    for sensor in sensors or []:
        assert result["step_id"] == "sensor"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"light_sensor": sensor} if sensor else {})
    assert result["step_id"] == "when"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], when or answers())
    assert result["step_id"] == "rhythm"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], rhythm or {"wake": "07:00:00", "bed": "23:00:00"})
    await hass.async_block_till_done()
    return result


async def test_one_tick_and_three_times_next(hass: HomeAssistant, kitchen) -> None:
    """The promise from the design: nobody who changes nothing has to do
    more than pick the room and carry on."""
    result = await install(hass, [kitchen["area"]])
    assert result["type"] is FlowResultType.CREATE_ENTRY
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    rooms = list(entry.subentries.values())
    assert [r.title for r in rooms] == ["Keuken"]
    assert rooms[0].data["area_id"] == kitchen["area"]
    assert entry.runtime_data.rooms, "de ruimte draait niet na de installatie"


async def test_intermediate_screens_say_next_and_the_last_one_saves(hass: HomeAssistant, kitchen) -> None:
    """He asked about this in version one: a button saying "Submit" halfway
    through reads as if the whole thing is being sent off."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["last_step"] is False
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"rooms": [kitchen["area"]]})
    assert result["last_step"] is False
    result = await hass.config_entries.flow.async_configure(result["flow_id"], answers())
    assert result["step_id"] == "rhythm" and result["last_step"] is not False


async def test_the_list_says_which_rooms_are_ready(hass: HomeAssistant, house, kitchen) -> None:
    house.lamp("bureau", "Kantoor")
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    options = result["data_schema"].schema["rooms"].config["options"]
    labels = [o["label"] for o in options]
    assert labels[0] == "Keuken — 2 lamps, light sensor, motion sensor"
    assert labels[-1] == "Kantoor — 1 lamp, no light sensor (you pick one next)", (
        "de ruimte zonder sensor zei niet wat er mist en wat er dan gebeurt"
    )


async def test_the_when_screen_shows_one_question_and_folds_the_rest(hass: HomeAssistant, kitchen) -> None:
    """His "ik snap niks van dit scherm": seven fields at once. Now one, and
    three headings that open only when they are in use."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"rooms": [kitchen["area"]]})
    schema = result["data_schema"].schema
    assert [str(key) for key in schema] == ["home", "condition", "hours", "yourself"]
    assert all(schema[key].options["collapsed"] for key in schema if str(key) != "home")


async def test_only_things_that_are_on_or_off_can_be_picked(hass: HomeAssistant, kitchen) -> None:
    """30 September: the list offered two entities called "Nachtmodus
    actief", a sensor that had not existed for months and the real binary
    sensor, and the wrong one got picked."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"rooms": [kitchen["area"]]})
    with pytest.raises(InvalidData):
        await hass.config_entries.flow.async_configure(
            result["flow_id"], answers("sensor.nachtmodus_actief"))


async def test_a_room_without_a_sensor_asks_for_one(hass: HomeAssistant, house, kitchen) -> None:
    house.lamp("bureau", "Kantoor")
    result = await install(hass, [house.area("Kantoor")], sensors=[kitchen["s7_lux"]])
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    room = next(iter(entry.subentries.values()))
    assert room.data["light_sensor"] == kitchen["s7_lux"]


async def test_skipping_the_sensor_leaves_the_room_out(hass: HomeAssistant, house, kitchen) -> None:
    house.lamp("bureau", "Kantoor")
    await install(hass, [kitchen["area"], house.area("Kantoor")], sensors=[None])
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert [r.title for r in entry.subentries.values()] == ["Keuken"]


async def test_choosing_nothing_is_not_accepted(hass: HomeAssistant, kitchen) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"rooms": []})
    assert result["errors"] == {"base": "choose_one"}


async def test_a_house_without_rooms_explains_what_to_do(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "no_rooms"


async def test_night_mode_on_or_off_is_stored(hass: HomeAssistant, kitchen) -> None:
    await install(hass, [kitchen["area"]], when=answers("binary_sensor.nachtmodus_actief"))
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert entry.options["when"]["entity"] == "binary_sensor.nachtmodus_actief"
    assert entry.options["when"]["entity_state"] == "off"


async def test_calm_is_installed_once(hass: HomeAssistant, kitchen) -> None:
    await install(hass, [kitchen["area"]])
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.ABORT


# -- after installing -------------------------------------------------------


async def installed(hass: HomeAssistant, kitchen):
    await install(hass, [kitchen["area"]])
    return hass.config_entries.async_entries(DOMAIN)[0]


async def test_ticking_a_room_in_the_settings_adds_it(hass: HomeAssistant, house, kitchen) -> None:
    entry = await installed(hass, kitchen)
    s, _ = house.hue_motion_sensor("s9", "Gang")
    house.lamp("gang_1", "Gang")
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "rooms"})
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"rooms": [kitchen["area"], house.area("Gang")]})
    await hass.async_block_till_done()
    assert sorted(r.title for r in entry.subentries.values()) == ["Gang", "Keuken"]
    assert len(entry.runtime_data.rooms) == 2, "de nieuwe ruimte draait niet"


async def test_unticking_a_room_removes_it(hass: HomeAssistant, kitchen) -> None:
    entry = await installed(hass, kitchen)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "rooms"})
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"rooms": []})
    await hass.async_block_till_done()
    assert not entry.subentries
    assert not entry.runtime_data.rooms


async def reconfigure(hass: HomeAssistant, entry, step: str):
    subentry_id = next(iter(entry.subentries))
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "room"), context={"source": config_entries.SOURCE_RECONFIGURE, "subentry_id": subentry_id})
    assert result["type"] is FlowResultType.MENU
    return await hass.config_entries.subentries.async_configure(result["flow_id"], {"next_step_id": step})


async def test_a_room_s_settings_screen_shows_what_the_sensor_reads(hass: HomeAssistant, kitchen) -> None:
    for lamp in kitchen["lamps"]:
        hass.states.async_set(lamp, "off", {"supported_color_modes": ["color_temp"]})
    hass.states.async_set(kitchen["room_lux"], "34")
    entry = await installed(hass, kitchen)
    result = await reconfigure(hass, entry, "settings")
    assert result["description_placeholders"]["reading"] == (
        "When you opened this screen, the sensor read 34 lux.")
    sections = set(result["data_schema"].schema)
    assert {"light", "motion", "when", "rhythm", "lamps"} <= {str(s) for s in sections}


async def test_changing_a_room_s_settings_is_stored_and_applied(hass: HomeAssistant, kitchen) -> None:
    entry = await installed(hass, kitchen)
    result = await reconfigure(hass, entry, "settings")
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], {
        "light": {"threshold_lux": 40, "brightness": 80},
        "motion": {"use_motion": True, "motion_hold_minutes": 10, "when_empty": "standby",
                   "standby_level": 20, "standby_kelvin": 2400, "motion_always": True,
                   "nightlight_level": 5, "nightlight_kelvin": 2000},
        "when": {"own_when": False},
        "rhythm": {"own_rhythm": False},
        "lamps": {"excluded_lights": [kitchen["lamps"][0]], "min_level": 17, "max_level": 98,
                  "manual_hold_minutes": 120},
    })
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()
    room_data = next(iter(entry.subentries.values())).data
    assert room_data["when_empty"] == "standby" and room_data["standby_level"] == 20
    assert room_data["excluded_lights"] == [kitchen["lamps"][0]]
    room = next(iter(entry.runtime_data.rooms.values()))
    assert room.controller.settings.threshold_lux == 40
    assert room.controller.settings.use_motion is True


async def test_an_override_made_from_a_template(hass: HomeAssistant, kitchen) -> None:
    entry = await installed(hass, kitchen)
    result = await reconfigure(hass, entry, "overrides")
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], {"override": "__new__"})
    assert result["step_id"] == "override_new"
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], {"template": "work"})
    assert result["step_id"] == "override"
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], {
        "lights": kitchen["lamps"], "level_mode": "relative", "level": 200, "colour_mode": "fixed",
        "mired_shift": 0, "kelvin": 4000, "auto": "none",
        "auto_start": "17:30:00", "auto_end": "19:00:00", "auto_state": "on",
    })
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()
    overrides = next(iter(entry.subentries.values())).data["overrides"]
    assert [o["name"] for o in overrides] == ["Work light"] or [o["name"] for o in overrides] == ["Werklicht"]
    assert overrides[0]["colour_mode"] == "fixed" and overrides[0]["kelvin"] == 4000
    switches = [s for s in hass.states.async_entity_ids("switch") if "work" in s or "werklicht" in s]
    assert switches, "de overrule kreeg geen schakelaar"


async def test_an_override_can_be_removed_and_its_switch_goes_with_it(hass: HomeAssistant, kitchen) -> None:
    entry = await installed(hass, kitchen)
    result = await reconfigure(hass, entry, "overrides")
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], {"override": "__new__"})
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], {"template": "film"})
    base = {"lights": kitchen["lamps"], "level_mode": "relative", "level": 20, "colour_mode": "shift",
            "mired_shift": 90, "kelvin": 2700, "auto": "none", "auto_start": "17:30:00",
            "auto_end": "19:00:00", "auto_state": "on"}
    await hass.config_entries.subentries.async_configure(result["flow_id"], base)
    await hass.async_block_till_done()
    key = next(iter(entry.subentries.values())).data["overrides"][0]["key"]
    result = await reconfigure(hass, entry, "overrides")
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], {"override": key})
    await hass.config_entries.subentries.async_configure(result["flow_id"], {**base, "remove": True})
    await hass.async_block_till_done()
    assert not next(iter(entry.subentries.values())).data["overrides"]
    assert not [s for s in hass.states.async_entity_ids("switch") if "film" in s]


async def test_entity_ids_name_the_room_once(hass: HomeAssistant, kitchen) -> None:
    """Home Assistant builds an id from area, device and entity. A device
    named after its room, in that room, gave switch.keuken_keuken_calm. The
    room's switch is switch.keuken_calm, as it was in version one."""
    from homeassistant.helpers import entity_registry as er

    await installed(hass, kitchen)
    ids = [e.entity_id for e in er.async_get(hass).entities.values() if e.platform == DOMAIN]
    assert "switch.keuken_calm" in ids
    assert "sensor.keuken_calm_why" in ids
    assert not [i for i in ids if "keuken_keuken" in i], ids



def settings_answers(kitchen, own_when=False, own_rhythm=False) -> dict:
    return {
        "light": {"threshold_lux": 25, "brightness": 95},
        "motion": {"use_motion": False, "motion_hold_minutes": 10, "when_empty": "off",
                   "standby_level": 15, "standby_kelvin": 2400, "motion_always": False,
                   "nightlight_level": 5, "nightlight_kelvin": 2000},
        "when": {"own_when": own_when},
        "rhythm": {"own_rhythm": own_rhythm},
        "lamps": {"excluded_lights": [], "min_level": 20, "max_level": 99, "manual_hold_minutes": 120},
    }


async def test_a_room_s_screen_has_no_fields_that_do_nothing(hass: HomeAssistant, kitchen) -> None:
    """30 September, twice: night mode changed under the room's "when",
    with its own-setting switch off, and silently thrown away. Only the
    switch is on that screen now."""
    entry = await installed(hass, kitchen)
    result = await reconfigure(hass, entry, "settings")
    sections = {str(k): v for k, v in result["data_schema"].schema.items()}
    assert [str(k) for k in sections["when"].schema.schema] == ["own_when"]
    assert [str(k) for k in sections["rhythm"].schema.schema] == ["own_rhythm"]


async def test_a_room_s_own_when_is_set_on_the_next_screen(hass: HomeAssistant, kitchen) -> None:
    entry = await installed(hass, kitchen)
    result = await reconfigure(hass, entry, "settings")
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], settings_answers(kitchen, own_when=True))
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "room_when"
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], answers("binary_sensor.nachtmodus_actief"))
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()
    data = next(iter(entry.subentries.values())).data
    assert data["own_when"] and data["when"]["entity"] == "binary_sensor.nachtmodus_actief"
    room = next(iter(entry.runtime_data.rooms.values()))
    assert room.when.entity == "binary_sensor.nachtmodus_actief"

    result = await reconfigure(hass, entry, "settings")
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], settings_answers(kitchen, own_when=False))
    assert result["type"] is FlowResultType.ABORT
    await hass.async_block_till_done()
    data = next(iter(entry.subentries.values())).data
    assert not data["own_when"] and "when" not in data, "uitgezet, en toch bleef de eigen instelling bewaard"


async def test_a_room_s_own_rhythm_follows_its_own_when(hass: HomeAssistant, kitchen) -> None:
    entry = await installed(hass, kitchen)
    result = await reconfigure(hass, entry, "settings")
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], settings_answers(kitchen, own_when=True, own_rhythm=True))
    assert result["step_id"] == "room_when" and result["last_step"] is False
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], answers())
    assert result["step_id"] == "room_rhythm"
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], {
        "wake": "06:30:00", "bed": "22:00:00", "colours": {"warm_kelvin": 2200, "cool_kelvin": 5000}})
    assert result["type"] is FlowResultType.ABORT
    data = next(iter(entry.subentries.values())).data
    assert data["own_rhythm"] and data["rhythm"]["wake"] == "06:30:00"



async def test_with_the_lamps_on_the_screen_says_how_much_was_daylight(hass: HomeAssistant, kitchen) -> None:
    """The line is held against the daylight, not the reading: at night with
    the lamps on the sensor reads their light too, and a line set against
    the bare reading would be set wrong."""
    hass.states.async_set(kitchen["room_lux"], "2")
    entry = await installed(hass, kitchen)
    room = next(iter(entry.runtime_data.rooms.values()))
    assert room.controller.lit
    hass.states.async_set(kitchen["room_lux"], "13")
    result = await reconfigure(hass, entry, "settings")
    reading = result["description_placeholders"]["reading"]
    assert reading.startswith("When you opened this screen, the sensor read 13 lux, of which about ")
    assert "The rest came from the lamps." in reading


async def test_a_sensor_without_a_reading_is_said_plainly(hass: HomeAssistant, kitchen) -> None:
    hass.states.async_set(kitchen["room_lux"], "unavailable")
    entry = await installed(hass, kitchen)
    result = await reconfigure(hass, entry, "settings")
    assert result["description_placeholders"]["reading"] == (
        "When you opened this screen, the sensor gave no reading.")
