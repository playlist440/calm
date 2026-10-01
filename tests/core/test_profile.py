from __future__ import annotations

import pytest

from custom_components.calm.core import DimProfile, Lamp
from custom_components.calm.core.photometry import perceptual_to_device

from .helpers import make_profile


def test_every_lamp_equal_by_default(profile):
    """The requirement in one assertion: nothing stands out."""
    levels = profile.levels(0.6)
    assert len(levels) == 5
    assert len(set(levels.values())) == 1
    devices = {perceptual_to_device(v) for v in levels.values()}
    assert len(devices) == 1


def test_a_weight_is_a_fixed_ratio_not_a_suggestion():
    profile = make_profile(weights=[1.0, 1.0, 1.0, 1.0, 0.5])
    low = profile.levels(0.4)
    high = profile.levels(0.9)
    assert low["spot_4"] / low["spot_0"] == pytest.approx(0.5)
    assert high["spot_4"] / high["spot_0"] == pytest.approx(0.5)


def test_normalisation_lets_the_brightest_lamp_reach_full():
    profile = DimProfile.from_lamps(
        [
            Lamp(id="a", weight=0.6, full_contribution=30.0),
            Lamp(id="b", weight=0.3, full_contribution=30.0),
        ]
    )
    assert profile.levels(1.0)["a"] == pytest.approx(1.0)
    assert profile.levels(1.0)["b"] == pytest.approx(0.5)


def test_a_mode_does_not_promote_the_lamps_it_leaves_behind():
    """Switch the main light off for cinema and the accent must stay an accent.

    Re-normalising a subset would quietly push a lamp deliberately set to a
    third up to full, which is the opposite of what the mode was for.
    """
    profile = DimProfile.from_lamps(
        [
            Lamp(id="main", weight=1.0, full_contribution=80.0),
            Lamp(id="accent", weight=0.33, full_contribution=20.0),
        ]
    )
    cinema = profile.with_enabled(["accent"])
    assert cinema.levels(1.0)["accent"] == pytest.approx(0.33, abs=1e-9)


def test_group_floor_is_the_highest_floor_in_the_room():
    """Ten spots must never wink out one at a time."""
    profile = DimProfile.from_lamps(
        [
            Lamp(id="a", weight=1.0, floor=0.03, full_contribution=30.0),
            Lamp(id="b", weight=1.0, floor=0.09, full_contribution=30.0),
            Lamp(id="c", weight=1.0, floor=0.05, full_contribution=30.0),
        ]
    )
    assert profile.group_floor == pytest.approx(0.09)
    at_floor = profile.levels(profile.group_floor)
    for lamp in profile.lamps:
        assert at_floor[lamp.id] >= lamp.floor - 1e-9


def test_group_floor_accounts_for_weighting():
    """A lamp held at half reaches its own floor at twice the master level."""
    profile = DimProfile.from_lamps(
        [
            Lamp(id="a", weight=1.0, floor=0.04, full_contribution=30.0),
            Lamp(id="b", weight=0.5, floor=0.04, full_contribution=30.0),
        ]
    )
    assert profile.group_floor == pytest.approx(0.08)


def test_master_for_lux_inverts_the_response(profile):
    for wanted in (5.0, 20.0, 60.0, 120.0, 180.0):
        master = profile.master_for_lux(wanted)
        assert master is not None
        assert profile.luminous_at(master) == pytest.approx(wanted, rel=1e-3)


def test_master_for_lux_saturates_rather_than_lying(profile):
    assert profile.master_for_lux(10_000.0) == 1.0
    assert profile.master_for_lux(0.0) is None


def test_contributions_add_up(profile):
    """Superposition, asserted — the assumption the whole design leans on."""
    total = profile.luminous_at(0.7)
    parts = sum(
        DimProfile(lamps=[lamp]).luminous_at(0.7) for lamp in profile.lamps
    )
    assert total == pytest.approx(parts)


def test_a_profile_needs_at_least_one_real_lamp():
    with pytest.raises(ValueError):
        DimProfile.from_lamps([Lamp(id="a", weight=0.0, full_contribution=10.0)])


# -- what the dimmer behind the lamps can actually do ------------------


def _dimmer(low: float = 0.15, high: float = 0.98) -> DimProfile:
    """A room of spots on a wall dimmer: dead below fifteen, buzzing at the top."""
    return DimProfile.from_lamps([
        Lamp(id="spot_%d" % n, weight=1.0, floor=low, ceiling=high,
             full_contribution=6.0)
        for n in range(3)
    ])


def test_a_lit_lamp_is_never_asked_for_less_than_its_dimmer_can_do():
    """Below fifteen percent these spots deliver nothing at all, while Calm
    goes on believing it asked for ten and got ten. Every conclusion after
    that is drawn from a lamp that was dark."""
    profile = _dimmer()
    assert all(level == pytest.approx(0.15) for level in profile.levels(0.08).values())


def test_and_never_for_more_than_it_can_do_either():
    """The fix everybody with a buzzing dimmer arrives at by hand."""
    profile = _dimmer()
    assert all(level == pytest.approx(0.98) for level in profile.levels(1.0).values())


def test_off_is_still_off():
    """The lowest setting is the bottom of the lit range, not a glow that can
    never be put out. A floor that switched the room on would be worse than
    no floor at all."""
    levels = _dimmer().levels(0.0)
    assert levels, "de lampen zijn uit de profielen verdwenen"
    assert all(level == 0.0 for level in levels.values())


def test_the_prediction_is_made_from_the_same_number_as_the_command():
    """The one that matters. The room predicts its own contribution to decide
    how far to push, and commands the lamps from a second calculation. Let
    those two disagree and the estimator spends its life explaining a gap
    that was never in the room — it would conclude these lamps are feeble
    while they sit pinned at their floor, and push harder and harder at a
    dimmer that has already stopped listening.
    """
    profile = _dimmer()
    for master in (0.0, 0.05, 0.1, 0.15, 0.4, 0.9, 1.0):
        from custom_components.calm.core.photometry import perceptual_to_luminous

        commanded = sum(
            lamp.full_contribution * perceptual_to_luminous(
                profile.levels(master)[lamp.id]
            )
            for lamp in profile.active
        )
        assert profile.luminous_at(master) == pytest.approx(commanded), master


def test_a_room_that_says_nothing_behaves_as_it_always_did():
    """Both limits default to "no limit at all". Somebody who never opens the
    screen must not find their lamps behaving differently after an update."""
    profile = make_profile()
    assert profile.levels(0.5) == {
        lamp.id: pytest.approx(0.5 * lamp.weight) for lamp in profile.active
    }
    assert max(lamp.ceiling for lamp in profile.lamps) == 1.0


def test_the_limits_survive_being_rescaled():
    """Re-seeding after a calibration builds a fresh profile from the old one.
    Losing the ceiling there would hand a buzzing dimmer back its top end on
    the day the room finally learned what its lamps do."""
    scaled = _dimmer().scaled(2.0)
    assert all(lamp.ceiling == pytest.approx(0.98) for lamp in scaled.lamps)
    assert all(lamp.floor == pytest.approx(0.15) for lamp in scaled.lamps)


def test_and_survive_a_mode_taking_lamps_out():
    scaled = _dimmer().with_enabled(["spot_0"])
    assert all(lamp.ceiling == pytest.approx(0.98) for lamp in scaled.lamps)
    assert all(lamp.floor == pytest.approx(0.15) for lamp in scaled.lamps)


def test_the_room_goes_dark_rather_than_into_the_dimmer_s_dead_zone():
    """What the floor is *for*, beyond the clamp: the level below which there
    is no point pretending. The whole group goes at once, because spots
    winking out one after another is the patchwork the fixed profile exists
    to prevent."""
    assert _dimmer().group_floor == pytest.approx(0.15)
