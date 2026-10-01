"""Naming the sliders after the lamps rather than after their entity ids."""

from __future__ import annotations

from custom_components.calm.core import label_lamps

NAMES = {
    "light.hue_color_spot_1": "Spot C8",
    "light.hue_color_spot_1_2": "Spot C9",
    "light.vloerlamp": "Vloerlamp",
}


def test_a_lamp_is_called_what_its_owner_called_it():
    labels = label_lamps(NAMES, NAMES.get)
    assert list(labels) == ["Spot C8", "Spot C9", "Vloerlamp"]
    assert labels["Spot C9"] == "light.hue_color_spot_1_2"


def test_a_lamp_without_a_name_falls_back_to_its_id():
    labels = label_lamps(["light.naamloos"], lambda _: None)
    assert labels == {"light.naamloos": "light.naamloos"}


def test_a_blank_name_counts_as_no_name():
    labels = label_lamps(["light.leeg"], lambda _: "   ")
    assert labels == {"light.leeg": "light.leeg"}


def test_two_lamps_with_the_same_name_both_get_told_apart():
    """Both, not just the second.

    Leaving the first as the bare name suggests it is *the* Spot, and
    choosing the wrong one of two identically named lamps is a mistake
    nobody would ever think to go looking for.
    """
    labels = label_lamps(
        ["light.a", "light.b"], lambda _: "Spot"
    )
    assert set(labels) == {"Spot (light.a)", "Spot (light.b)"}
    assert labels["Spot (light.a)"] == "light.a"


def test_the_order_of_the_room_is_kept():
    order = ["light.c", "light.a", "light.b"]
    labels = label_lamps(order, lambda e: e.split(".")[1].upper())
    assert list(labels.values()) == order


def test_every_lamp_ends_up_exactly_once():
    """The mapping is what the answers get stored under, so losing one would
    silently drop somebody's setting."""
    lights = ["light.%d" % i for i in range(6)]
    labels = label_lamps(lights, lambda e: "Spot" if e.endswith(("1", "2")) else e)
    assert sorted(labels.values()) == sorted(lights)
    assert len(labels) == len(lights)
