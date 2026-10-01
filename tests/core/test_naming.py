"""Names are typed by people; keys are pointed at by machines.

Everything in here exists because a key that shifts under an entity takes an
automation down with it, and the person who wrote that automation has no way
of knowing why.
"""

from __future__ import annotations

import pytest

from custom_components.calm.core import slugify, unique_key
from custom_components.calm.core.naming import MAX_LENGTH


def test_a_plain_name_becomes_a_plain_key():
    assert slugify("Werklicht") == "werklicht"


def test_punctuation_and_spacing_collapse_to_one_separator():
    """Two people writing the same thing should land on the same key."""
    assert slugify("werklicht  (keuken)") == "werklicht_keuken"
    assert slugify("Werklicht - keuken") == "werklicht_keuken"
    assert slugify("  Werklicht/Keuken  ") == "werklicht_keuken"


def test_accents_are_folded_rather_than_dropped():
    """Dropping them makes "Buréau" and "Bau" collide, which is worse than
    either of them being spelled oddly."""
    assert slugify("Buréau") == "bureau"
    assert slugify("Eetkamer boven de tafel") == "eetkamer_boven_de_tafel"


def test_letters_that_survive_decomposition_are_spelled_out():
    """"ß" is not an "s" with a mark on it, so stripping marks leaves it
    whole and the ASCII filter would throw the whole letter away."""
    assert slugify("Straße") == "strasse"
    assert slugify("Søren") == "soren"


def test_a_name_with_nothing_usable_in_it_yields_no_key():
    """Empty means "ask again", never "use this"."""
    assert slugify("---") == ""
    assert slugify("   ") == ""
    assert slugify("日本語") == ""


def test_digits_are_kept_because_people_number_things():
    assert slugify("Overrule 2") == "overrule_2"


def test_a_key_never_ends_up_longer_than_an_entity_id_can_carry():
    key = slugify("x" * 200)
    assert len(key) <= MAX_LENGTH


def test_a_truncated_key_does_not_end_on_a_separator():
    """A trailing underscore reads as a mistake in the entity id."""
    name = "a" * (MAX_LENGTH - 1) + " bcd"
    assert not slugify(name).endswith("_")


def test_a_free_name_keeps_its_own_key():
    assert unique_key("Werklicht", ["film", "nachtlampje"]) == "werklicht"


def test_a_taken_name_is_numbered_the_way_a_person_would():
    assert unique_key("Werklicht", ["werklicht"]) == "werklicht_2"
    assert unique_key("Werklicht", ["werklicht", "werklicht_2"]) == "werklicht_3"


def test_numbering_a_long_name_still_fits():
    taken = [slugify("x" * 200)]
    key = unique_key("x" * 200, taken)
    assert key not in taken
    assert len(key) <= MAX_LENGTH


def test_an_unusable_name_stays_unusable_however_many_are_taken():
    assert unique_key("///", ["a", "b"]) == ""


def test_running_out_of_numbers_is_an_error_rather_than_a_collision():
    """A silent duplicate would hand two overrides the same switch."""
    taken = ["werklicht"] + ["werklicht_%d" % n for n in range(2, 1000)]
    with pytest.raises(ValueError):
        unique_key("Werklicht", taken)
