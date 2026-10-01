"""When Calm may run a room, against real Home Assistant state."""

from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

from homeassistant.core import HomeAssistant

from custom_components.calm.conditions import When, someone_home

TZ = ZoneInfo("Europe/Amsterdam")
EVENING = datetime(2026, 9, 30, 19, 30, tzinfo=TZ)


def people(hass: HomeAssistant, at_home: int) -> None:
    hass.states.async_set("person.mason", "home" if at_home else "not_home")
    hass.states.async_set("zone.home", str(at_home))


async def test_his_kitchen_somebody_home_and_night_mode_off(hass: HomeAssistant) -> None:
    """Both have to hold, which is what "a combination" turned out to mean."""
    when = When(home=True, entity="binary_sensor.nachtmodus_actief", entity_state="off")
    people(hass, 1)
    hass.states.async_set("binary_sensor.nachtmodus_actief", "off", {"friendly_name": "Nachtmodus actief"})
    assert when.evaluate(hass, EVENING) == (True, None)

    hass.states.async_set("binary_sensor.nachtmodus_actief", "on", {"friendly_name": "Nachtmodus actief"})
    allowed, because = when.evaluate(hass, EVENING)
    assert not allowed and because == "Nachtmodus actief staat aan"

    hass.states.async_set("binary_sensor.nachtmodus_actief", "off", {"friendly_name": "Nachtmodus actief"})
    people(hass, 0)
    assert when.evaluate(hass, EVENING) == (False, "er is niemand thuis")


async def test_the_entity_may_have_to_be_on_as_well_as_off(hass: HomeAssistant) -> None:
    """"Als deze aan of uit staat": the household picks which."""
    when = When(home=False, entity="input_boolean.gasten", entity_state="on")
    hass.states.async_set("input_boolean.gasten", "on")
    assert when.evaluate(hass, EVENING)[0]
    hass.states.async_set("input_boolean.gasten", "off")
    assert not when.evaluate(hass, EVENING)[0]


async def test_a_missing_entity_does_not_count_as_matching(hass: HomeAssistant) -> None:
    when = When(home=False, entity="binary_sensor.weg", entity_state="off")
    assert not when.evaluate(hass, EVENING)[0]


async def test_an_entity_that_is_gone_is_named_by_its_id(hass: HomeAssistant) -> None:
    """30 September: the kitchen stayed dark because "Nachtmodus actief" was
    a sensor removed from the configuration months ago. Home Assistant keeps
    it as unavailable and restored; its name is the live one's name too."""
    hass.states.async_set("sensor.nachtmodus_actief", "unavailable",
                          {"friendly_name": "Nachtmodus actief", "restored": True})
    when = When(home=False, entity="sensor.nachtmodus_actief", entity_state="off")
    assert when.entity_missing(hass)
    failing, because = when.check(hass, EVENING)
    assert failing == "entity_missing"
    assert because == "sensor.nachtmodus_actief bestaat niet meer, kies bij Wanneer een andere"


async def test_an_entity_that_is_briefly_unreachable_is_not_gone(hass: HomeAssistant) -> None:
    hass.states.async_set("binary_sensor.nachtmodus_actief", "unavailable",
                          {"friendly_name": "Nachtmodus actief"})
    when = When(home=False, entity="binary_sensor.nachtmodus_actief", entity_state="off")
    assert not when.entity_missing(hass)
    assert when.check(hass, EVENING) == ("entity_unavailable", "Nachtmodus actief is onbereikbaar")


async def test_set_hours_may_run_past_midnight(hass: HomeAssistant) -> None:
    when = When(home=False, window=True, start=time(18, 0), end=time(1, 0))
    assert when.evaluate(hass, EVENING)[0]
    assert when.evaluate(hass, datetime(2026, 10, 1, 0, 30, tzinfo=TZ))[0]
    allowed, because = when.evaluate(hass, datetime(2026, 10, 1, 13, 0, tzinfo=TZ))
    assert not allowed and "tijden" in because


async def test_a_house_without_people_set_up_counts_as_home(hass: HomeAssistant) -> None:
    """Refusing to light anything because nobody configured presence is
    not "it just works"."""
    assert someone_home(hass)


async def test_it_explains_itself_in_english_too(hass: HomeAssistant) -> None:
    people(hass, 0)
    assert When().evaluate(hass, EVENING, "en") == (False, "nobody is home")


async def test_doing_it_yourself_watches_nothing(hass: HomeAssistant) -> None:
    assert When(manual=True, entity="binary_sensor.x").watched() == []
    assert When(entity="binary_sensor.x").watched() == ["zone.home", "binary_sensor.x"]


async def test_settings_survive_storage(hass: HomeAssistant) -> None:
    when = When(home=True, entity="binary_sensor.nachtmodus_actief", entity_state="off",
                window=True, start=time(6, 30), end=time(23, 0))
    assert When.from_dict(when.to_dict()) == when


async def test_nothing_stored_means_somebody_home_and_nothing_else(hass: HomeAssistant) -> None:
    assert When.from_dict(None) == When()
