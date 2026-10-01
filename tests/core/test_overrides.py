"""Overrides: named arrangements, and the templates they start from."""

from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

import pytest

from custom_components.calm.core import (
    TEMPLATES,
    AutoMode,
    ColourMode,
    DimProfile,
    Lamp,
    LampRole,
    LevelMode,
    Override,
    OverrideSet,
    from_template,
    kelvin_to_mired,
)

TZ = ZoneInfo("Europe/Amsterdam")


def kitchen() -> DimProfile:
    """Two ceiling lamps, and a worktop light that sits the ordinary day out."""
    return DimProfile.from_lamps([
        Lamp(id="light.ceiling_a"),
        Lamp(id="light.ceiling_b"),
        Lamp(id="light.worktop", enabled=False),
    ])


def test_a_lamp_switched_on_by_an_override_joins_even_if_it_normally_sits_out():
    """How a worktop light that is only for work gets called in."""
    work = Override(key="work", name="Werklicht", lamps={"light.worktop": LampRole.ON})
    ids = {lamp.id for lamp in work.profile_from(kitchen()).active}
    assert "light.worktop" in ids


def test_a_lamp_switched_off_by_an_override_stays_dark_for_the_duration():
    film = Override(key="film", name="Film", lamps={"light.ceiling_a": LampRole.OFF})
    ids = {lamp.id for lamp in film.profile_from(kitchen()).active}
    assert ids == {"light.ceiling_b"}


def test_a_lamp_nobody_mentioned_does_what_it_normally_does():
    nothing = Override(key="x", name="X")
    ids = {lamp.id for lamp in nothing.profile_from(kitchen()).active}
    assert ids == {"light.ceiling_a", "light.ceiling_b"}


def test_what_is_left_is_not_promoted():
    """Switch off the main light for a film and the accent lamp deliberately
    set to a third must not jump to full."""
    base = DimProfile.from_lamps([
        Lamp(id="main", weight=1.0), Lamp(id="accent", weight=0.33),
    ])
    film = Override(key="film", name="Film", lamps={"main": LampRole.OFF})
    accent = next(l for l in film.profile_from(base).lamps if l.id == "accent")
    assert accent.weight == pytest.approx(0.33)


def test_the_dimmer_limits_survive_an_override():
    """The floor and ceiling are about the wiring, not the arrangement."""
    base = DimProfile.from_lamps([Lamp(id="spot", floor=0.17, ceiling=0.98)])
    lamp = Override(key="x", name="X").profile_from(base).lamps[0]
    assert (lamp.floor, lamp.ceiling) == (0.17, 0.98)


def test_a_relative_level_scales_the_room_and_a_fixed_one_ignores_it():
    relative = Override(key="a", name="A", level_mode=LevelMode.RELATIVE, level=0.4)
    fixed = Override(key="b", name="B", level_mode=LevelMode.FIXED, level=0.3)
    assert relative.target_lux(50.0) == pytest.approx(20.0)
    assert fixed.target_lux(50.0) is None
    assert fixed.fixed_level == pytest.approx(0.3)


def test_a_fixed_level_above_full_is_still_only_full():
    assert Override(key="b", name="B", level_mode=LevelMode.FIXED, level=3.0).fixed_level == 1.0


def test_colour_follows_shifts_or_is_pinned():
    follow = Override(key="a", name="A")
    shift = Override(key="b", name="B", colour_mode=ColourMode.SHIFT, mired_shift=60.0)
    pinned = Override(key="c", name="C", colour_mode=ColourMode.FIXED, kelvin=4000.0)
    assert follow.target_mired(400.0) == 400.0
    assert shift.target_mired(400.0) == 460.0
    assert pinned.target_mired(250.0) == pytest.approx(kelvin_to_mired(4000.0))
    assert pinned.target_mired(500.0) == pytest.approx(kelvin_to_mired(4000.0))


# -- switching itself on --------------------------------------------------


def test_dinner_switches_itself_on_around_dinner_time():
    dinner = Override(key="eten", name="Eten", auto=AutoMode.TIME,
                      auto_start=time(17, 30), auto_end=time(19, 0))
    assert dinner.wants_on(datetime(2026, 9, 30, 18, 0, tzinfo=TZ))
    assert not dinner.wants_on(datetime(2026, 9, 30, 19, 0, tzinfo=TZ))
    assert not dinner.wants_on(datetime(2026, 9, 30, 12, 0, tzinfo=TZ))


def test_a_window_may_run_past_midnight():
    late = Override(key="laat", name="Laat", auto=AutoMode.TIME,
                    auto_start=time(22, 0), auto_end=time(2, 0))
    assert late.wants_on(datetime(2026, 9, 30, 23, 30, tzinfo=TZ))
    assert late.wants_on(datetime(2026, 10, 1, 1, 30, tzinfo=TZ))
    assert not late.wants_on(datetime(2026, 10, 1, 3, 0, tzinfo=TZ))


def test_the_television_switches_tv_mode_on():
    tv = Override(key="tv", name="TV kijken", auto=AutoMode.ENTITY,
                  auto_entity="media_player.tv", auto_state="on")
    now = datetime(2026, 9, 30, 21, 0, tzinfo=TZ)
    assert tv.wants_on(now, "on")
    assert not tv.wants_on(now, "off")
    assert not tv.wants_on(now, None), "een onbekende stand zette de overrule aan"


def test_an_override_without_auto_never_switches_itself_on():
    assert not Override(key="x", name="X").wants_on(datetime(2026, 9, 30, 18, 0, tzinfo=TZ))


# -- templates ------------------------------------------------------------


def test_every_template_named_in_the_design_exists():
    assert set(TEMPLATES) == {
        "tv", "film", "work", "dinner", "cooking", "reading", "cosy", "cleaning", "nightlight",
    }


@pytest.mark.parametrize("template", sorted(TEMPLATES))
def test_every_template_builds_a_working_override(template):
    override = from_template(template, "Iets", "iets")
    assert override.key == "iets" and override.name == "Iets"
    override.target_mired(400.0)
    override.target_lux(40.0)


def test_templates_leave_every_lamp_alone():
    """Which lamp sits by the television is something only the person
    setting it up knows."""
    for template in TEMPLATES:
        assert not from_template(template, "X", "x").lamps


def test_the_work_light_template_pins_a_working_colour():
    assert from_template("work", "Werklicht", "werklicht").colour_mode is ColourMode.FIXED


def test_the_tv_template_waits_for_a_device_to_be_chosen():
    tv = from_template("tv", "TV kijken", "tv_kijken")
    assert tv.auto is AutoMode.ENTITY and tv.auto_entity is None
    assert not tv.wants_on(datetime(2026, 9, 30, 21, 0, tzinfo=TZ), "on"), (
        "een tv-overrule zonder gekozen tv ging vanzelf aan"
    )


# -- storage --------------------------------------------------------------


def test_an_override_survives_being_stored_and_read_back():
    original = Override(
        key="tv", name="TV kijken",
        lamps={"light.spot": LampRole.OFF}, weights={"light.lamp": 0.5},
        level_mode=LevelMode.RELATIVE, level=0.4,
        colour_mode=ColourMode.SHIFT, mired_shift=60.0,
        auto=AutoMode.ENTITY, auto_entity="media_player.tv", auto_state="playing",
    )
    assert Override.from_dict(original.to_dict()) == original


def test_a_time_window_survives_storage_too():
    original = from_template("dinner", "Eten", "eten")
    assert Override.from_dict(original.to_dict()) == original


def test_settings_with_rubbish_in_them_still_give_an_override():
    """Written by an older version, or by hand: defaults, not an exception
    in somebody's setup."""
    read = Override.from_dict({
        "name": "Raar", "level_mode": "nonsense", "colour_mode": 7,
        "auto": "sometimes", "lamps": {"light.a": "sideways"}, "auto_start": "not a time",
    })
    assert read.level_mode is LevelMode.RELATIVE
    assert read.colour_mode is ColourMode.FOLLOW
    assert read.auto is AutoMode.NONE
    assert read.lamps["light.a"] is LampRole.NORMAL
    assert read.auto_start is None


def test_an_override_without_a_usable_name_is_refused():
    with pytest.raises(ValueError):
        Override.from_dict({"name": "!!!"})


# -- the set --------------------------------------------------------------


def test_names_become_keys_once_and_never_collide():
    overrides = OverrideSet()
    first = overrides.create("Werklicht")
    second = overrides.create("Werklicht")
    assert first.key == "werklicht"
    assert second.key != first.key


def test_a_key_already_in_use_is_refused():
    overrides = OverrideSet([Override(key="tv", name="TV")])
    with pytest.raises(ValueError):
        overrides.add(Override(key="tv", name="Andere TV"))


def test_the_set_survives_storage_and_skips_what_it_cannot_read():
    stored = [
        Override(key="tv", name="TV").to_dict(),
        {"name": "!!!"},
        Override(key="eten", name="Eten").to_dict(),
    ]
    read = OverrideSet.from_list(stored)
    assert read.keys() == ["tv", "eten"]


def test_the_order_is_kept():
    """A list that reshuffles itself between visits is a list nobody trusts."""
    overrides = OverrideSet()
    for name in ("Zeta", "Alpha", "Mid"):
        overrides.create(name)
    assert [o.name for o in overrides] == ["Zeta", "Alpha", "Mid"]
