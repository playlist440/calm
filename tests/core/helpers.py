"""A plausible room, wired up the usual way. Shared by the core tests."""

from __future__ import annotations

from custom_components.calm.core import DimProfile, Lamp

UTRECHT = (52.09, 5.12)

#: What the model table guesses each spot contributes. Wrong on purpose: the
#: point of the estimator is that a catalogue figure and a real room differ.
PRIOR_PER_LAMP = 40.0
TRUE_PER_LAMP = 24.0


def make_profile(count: int = 5, weights=None) -> DimProfile:
    weights = weights or [1.0] * count
    return DimProfile.from_lamps(
        [
            Lamp(id="spot_%d" % i, weight=weights[i], full_contribution=PRIOR_PER_LAMP)
            for i in range(count)
        ]
    )

