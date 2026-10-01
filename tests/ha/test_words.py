"""Every screen has words for everything on it, in both languages.

Version one once showed a raw key where a label belonged. Checked here by
walking the real screens and looking up every field they show, rather than
by keeping a list that goes stale.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.calm.const import DOMAIN

from .test_config_flow import answers, install, settings_answers

HERE = Path(__file__).resolve().parents[2] / "custom_components" / "calm"
LANGUAGES = {
    "nl": json.loads((HERE / "translations" / "nl.json").read_text()),
    "en": json.loads((HERE / "translations" / "en.json").read_text()),
    "strings": json.loads((HERE / "strings.json").read_text()),
}


@pytest.fixture(autouse=True)
def evening(freezer, hass):
    freezer.move_to("2026-09-29 21:00:00+02:00")
    async_mock_service(hass, "light", "turn_on")
    async_mock_service(hass, "light", "turn_off")


def check(result, where: str) -> None:
    """Every field, section and menu row of one shown screen has a label."""
    step_id = result["step_id"]
    for name, words in LANGUAGES.items():
        node = words
        for part in where.split("."):
            node = node[part]
        step = node["step"].get(step_id)
        assert step is not None, f"{name}: scherm {where}.{step_id} heeft geen tekst"
        if result["type"] is FlowResultType.MENU:
            options = result["menu_options"]
            keys = options.keys() if isinstance(options, dict) else options
            for option in keys:
                assert option in step.get("menu_options", {}), f"{name}: menu {step_id}.{option}"
            continue
        assert "title" in step, f"{name}: {step_id} heeft geen titel"
        for key, value in result["data_schema"].schema.items():
            field = str(key)
            if hasattr(value, "schema") and hasattr(value, "options"):
                section = step.get("sections", {}).get(field)
                assert section and "name" in section, f"{name}: sectie {step_id}.{field}"
                for inner in value.schema.schema:
                    assert str(inner) in section.get("data", {}), (
                        f"{name}: veld {step_id}.{field}.{inner} heeft geen label")
                continue
            assert field in step.get("data", {}), f"{name}: veld {step_id}.{field} heeft geen label"


async def test_installing(hass: HomeAssistant, house, kitchen) -> None:
    house.lamp("bureau", "Kantoor")
    flow = hass.config_entries.flow
    result = await flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    check(result, "config")
    result = await flow.async_configure(result["flow_id"], {"rooms": [kitchen["area"], house.area("Kantoor")]})
    check(result, "config")
    result = await flow.async_configure(result["flow_id"], {"light_sensor": kitchen["s7_lux"]})
    check(result, "config")
    result = await flow.async_configure(result["flow_id"], answers())
    check(result, "config")


async def test_calm_s_own_settings(hass: HomeAssistant, house, kitchen) -> None:
    await install(hass, [kitchen["area"]])
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    for step in ("rooms", "when", "rhythm"):
        result = await hass.config_entries.options.async_init(entry.entry_id)
        check(result, "options")
        result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": step})
        check(result, "options")
    house.lamp("bureau", "Kantoor")
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "rooms"})
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"rooms": [kitchen["area"], house.area("Kantoor")]})
    check(result, "options")


async def test_a_room_s_screens(hass: HomeAssistant, house, kitchen) -> None:
    await install(hass, [kitchen["area"]])
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    subentry_id = next(iter(entry.subentries))
    flows = hass.config_entries.subentries
    where = "config_subentries.room"

    result = await flows.async_init((entry.entry_id, "room"), context={"source": config_entries.SOURCE_USER})
    check(result, where)
    custom = await flows.async_configure(result["flow_id"], {"next_step_id": "custom"})
    check(custom, where)
    house.lamp("bureau", "Kantoor")
    result = await flows.async_init((entry.entry_id, "room"), context={"source": config_entries.SOURCE_USER})
    area = await flows.async_configure(result["flow_id"], {"next_step_id": "area"})
    check(area, where)
    sensor = await flows.async_configure(area["flow_id"], {"area_id": house.area("Kantoor")})
    check(sensor, where)

    for step in ("settings", "overrides"):
        result = await flows.async_init((entry.entry_id, "room"), context={
            "source": config_entries.SOURCE_RECONFIGURE, "subentry_id": subentry_id})
        check(result, where)
        result = await flows.async_configure(result["flow_id"], {"next_step_id": step})
        check(result, where)
    result = await flows.async_configure(result["flow_id"], {"override": "__new__"})
    check(result, where)
    result = await flows.async_configure(result["flow_id"], {"template": "tv"})
    check(result, where)

    result = await flows.async_init((entry.entry_id, "room"), context={
        "source": config_entries.SOURCE_RECONFIGURE, "subentry_id": subentry_id})
    result = await flows.async_configure(result["flow_id"], {"next_step_id": "settings"})
    result = await flows.async_configure(result["flow_id"], settings_answers(kitchen, True, True))
    check(result, where)
    result = await flows.async_configure(result["flow_id"], answers())
    check(result, where)


def test_every_choice_in_a_list_has_words() -> None:
    for name, words in LANGUAGES.items():
        for key, options in {
            "on_off": {"on", "off"},
            "when_empty": {"off", "standby"},
            "level_mode": {"relative", "fixed"},
            "colour_mode": {"follow", "shift", "fixed"},
            "auto": {"none", "time", "entity"},
        }.items():
            assert set(words["selector"][key]["options"]) == options, f"{name}: keuzes {key}"


def test_nothing_the_message_parser_chokes_on() -> None:
    """Angle brackets read as tags, an unmatched brace as a placeholder.
    Version one showed "Translation error: UNCLOSED_TAG" over exactly this."""
    def strings(node):
        if isinstance(node, dict):
            for value in node.values():
                yield from strings(value)
        elif isinstance(node, str):
            yield node

    for name, words in LANGUAGES.items():
        for text in strings(words):
            assert "<" not in text and ">" not in text, f"{name}: {text!r}"
            assert text.count("{") == text.count("}"), f"{name}: {text!r}"
