"""The room's entities, its life cycle and what it leaves behind."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from homeassistant.config_entries import ConfigEntryState, ConfigSubentryData
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    mock_restore_cache,
)

from custom_components.calm.const import DOMAIN
from custom_components.calm.diagnostics import async_get_config_entry_diagnostics

from .test_room import amsterdam, entity, home, start


async def test_the_calm_switch_turns_the_room_off_and_on(hass, kitchen, lamp_calls) -> None:
    await amsterdam(hass)
    home(hass)
    hass.states.async_set(kitchen["room_lux"], "2")
    entry = await start(hass, kitchen)
    switch = entity(hass, entry, "switch", "calm")
    room = entry.runtime_data.rooms[next(iter(entry.subentries))]
    await hass.services.async_call("switch", "turn_off", {"entity_id": switch}, blocking=True)
    assert hass.states.get(switch).state == "off" and not room.controller.enabled
    await hass.services.async_call("switch", "turn_on", {"entity_id": switch}, blocking=True)
    assert hass.states.get(switch).state == "on" and room.controller.enabled


async def test_the_evening_number_and_the_resume_button(hass, kitchen, lamp_calls) -> None:
    await amsterdam(hass)
    home(hass)
    hass.states.async_set(kitchen["room_lux"], "2")
    entry = await start(hass, kitchen)
    room = entry.runtime_data.rooms[next(iter(entry.subentries))]
    room.controller.notify_manual_change()
    await hass.services.async_call(
        "button", "press", {"entity_id": entity(hass, entry, "button", "resume")}, blocking=True)
    assert not room.controller.manual
    await hass.services.async_call("number", "set_value", {
        "entity_id": entity(hass, entry, "number", "brightness"), "value": 70}, blocking=True)
    assert room.controller.settings.brightness == pytest.approx(0.7)


async def test_an_override_switch_runs_its_override(hass, kitchen, lamp_calls) -> None:
    await amsterdam(hass)
    home(hass)
    hass.states.async_set(kitchen["room_lux"], "2")
    lamp_a, lamp_b = kitchen["lamps"]
    entry = await start(hass, kitchen, overrides=[{
        "key": "film", "name": "Film", "lamps": {lamp_a: "on", lamp_b: "on"},
        "level_mode": "relative", "level": 0.2}])
    switch = entity(hass, entry, "switch", "override_film")
    await hass.services.async_call("switch", "turn_on", {"entity_id": switch}, blocking=True)
    assert hass.states.get(switch).state == "on"
    await hass.services.async_call("switch", "turn_off", {"entity_id": switch}, blocking=True)
    assert hass.states.get(switch).state == "off"


async def test_the_reason_is_one_of_a_fixed_set_with_the_sentence_beside_it(hass, kitchen, lamp_calls) -> None:
    await amsterdam(hass)
    home(hass)
    hass.states.async_set(kitchen["room_lux"], "2")
    entry = await start(hass, kitchen)
    state = hass.states.get(entity(hass, entry, "sensor", "why"))
    assert state.state in state.attributes["options"]
    assert state.attributes["sentence"]


async def test_unloading_stops_every_room_and_reloading_starts_them_again(hass, kitchen, lamp_calls) -> None:
    await amsterdam(hass)
    home(hass)
    hass.states.async_set(kitchen["room_lux"], "2")
    entry = await start(hass, kitchen)
    room = entry.runtime_data.rooms[next(iter(entry.subentries))]
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED
    assert not room._unsubscribe and not room._tasks
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.runtime_data.rooms


async def test_a_switch_run_by_hand_is_restored_before_the_first_run(hass, kitchen, lamp_calls) -> None:
    """With "I do it myself" on, the switch as it was before a restart counts
    from the very first run."""
    await amsterdam(hass)
    home(hass)
    hass.states.async_set(kitchen["room_lux"], "2")
    entry = MockConfigEntry(
        domain=DOMAIN, version=2, title="Calm", options={"when": {"manual": True}},
        subentries_data=[ConfigSubentryData(
            data={"area_id": kitchen["area"], "name": "Keuken", "threshold_lux": 25.0},
            subentry_type="room", title="Keuken", unique_id=kitchen["area"])])
    entry.add_to_hass(hass)
    subentry_id = next(iter(entry.subentries))
    registry = er.async_get(hass)
    registry.async_get_or_create("switch", DOMAIN, f"{subentry_id}_calm", suggested_object_id="keuken_calm",
                                 config_entry=entry)
    mock_restore_cache(hass, [State("switch.keuken_calm", "on")])
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert lamp_calls, "de schakelaar van voor de herstart telde niet bij de eerste ronde"


async def test_the_loop_crosses_midnight_and_the_clocks_going_back(hass, kitchen, freezer, lamp_calls) -> None:
    await amsterdam(hass)
    home(hass)
    hass.states.async_set(kitchen["room_lux"], "2")
    freezer.move_to("2026-10-24 23:50:00+02:00")
    entry = await start(hass, kitchen)
    room = entry.runtime_data.rooms[next(iter(entry.subentries))]
    # Through midnight, and through 03:00 summer time back to 02:00 winter time.
    for _ in range(int(4 * 3600 / 30)):
        freezer.tick(timedelta(seconds=30))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
    assert room._last_run is not None
    assert room.controller.lit


async def test_lamp_commands_that_keep_failing_go_under_repairs(hass, kitchen, freezer) -> None:
    """No light service at all: every command fails."""
    await amsterdam(hass)
    home(hass)
    hass.states.async_set(kitchen["room_lux"], "2")
    entry = await start(hass, kitchen)
    room = entry.runtime_data.rooms[next(iter(entry.subentries))]
    for brightness in range(10, 90, 10):
        room.set_brightness(brightness)
        await hass.async_block_till_done()
    issue = ir.async_get(hass).async_get_issue(DOMAIN, f"commands_failing_{room.subentry_id}")
    assert issue is not None, "mislukte lampcommando's bleven onopgemerkt"


async def test_entities_nothing_updates_are_swept_and_new_ones_kept(hass, kitchen, lamp_calls) -> None:
    await amsterdam(hass)
    home(hass)
    hass.states.async_set(kitchen["room_lux"], "2")
    entry = await start(hass, kitchen)
    subentry_id = next(iter(entry.subentries))
    registry = er.async_get(hass)
    stale = registry.async_get_or_create("switch", DOMAIN, f"{subentry_id}_override_weg", config_entry=entry)
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert registry.async_get(stale.entity_id) is None, "een wees bleef in het register staan"
    assert entity(hass, entry, "number", "evening_brightness")


async def test_a_removed_room_leaves_nothing_behind(hass, kitchen, lamp_calls, hass_storage) -> None:
    await amsterdam(hass)
    home(hass)
    hass.states.async_set(kitchen["room_lux"], "2")
    entry = await start(hass, kitchen, options={"when": {"home": True}, "journal": True})
    subentry_id = next(iter(entry.subentries))
    key = f"{DOMAIN}.{entry.entry_id}.{subentry_id}"
    journal = Path(hass.config.path("calm", subentry_id))
    await hass.async_block_till_done()
    room = entry.runtime_data.rooms[subentry_id]
    await room._save()
    assert key in hass_storage and journal.exists()
    hass.config_entries.async_remove_subentry(entry, subentry_id)
    await hass.async_block_till_done()
    assert key not in hass_storage, "de opgeslagen kennis van een verwijderde ruimte bleef staan"
    assert not journal.exists(), "het dagverslag van een verwijderde ruimte bleef staan"


async def test_diagnostics_show_what_each_room_believes(hass, kitchen, lamp_calls) -> None:
    await amsterdam(hass)
    home(hass)
    hass.states.async_set(kitchen["room_lux"], "2")
    entry = await start(hass, kitchen)
    data = await async_get_config_entry_diagnostics(hass, entry)
    room = next(iter(data["rooms"].values()))
    assert room["name"] == "Keuken" and room["last_command"] is not None


async def test_a_rebuilt_room_keeps_its_switch(hass, house, kitchen, freezer, lamp_calls) -> None:
    await amsterdam(hass)
    home(hass)
    hass.states.async_set(kitchen["room_lux"], "2")
    entry = await start(hass, kitchen)
    hub = entry.runtime_data
    room = hub.rooms[next(iter(entry.subentries))]
    room.set_switch(False)
    house.lamp("werkblad_3", "Keuken")
    freezer.tick(timedelta(seconds=5))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    rebuilt = hub.rooms[next(iter(entry.subentries))]
    assert rebuilt is not room
    assert not rebuilt.switch_on, "de herbouwde ruimte vergat dat Calm hier uit stond"


@pytest.fixture
def lamp_calls(hass: HomeAssistant):
    from pytest_homeassistant_custom_component.common import async_mock_service
    async_mock_service(hass, "light", "turn_off")
    return async_mock_service(hass, "light", "turn_on")
