"""Light as the body clock sees it, rather than as the eye sees it.

Brightness and colour are not two settings that happen to both change over
the day. They are one quantity seen from two sides, and the quantity has a
name: **melanopic equivalent daylight illuminance** (melanopic EDI),
standardised in CIE S 026:2018.

The short version of the biology. Beside the rods and cones there is a third
kind of photoreceptor, the intrinsically photosensitive retinal ganglion
cell, carrying a pigment called melanopsin. It peaks around 480 nm — blue —
and it is what tells the brain whether it is day. It barely contributes to
what you *see*, which is why a room can look identical at 2700 K and 5000 K
and mean something entirely different to the clock.

So the same number of lux does very different things depending on the
spectrum. Dimming and warming are two ways of doing the same thing, and
doing both is why evening light works.

The recommendations this schedule is built on are the 2022 consensus in
*PLOS Biology* (Brown et al., "Recommendations for daytime, evening, and
night-time indoor light exposure"), which are stated in melanopic EDI at eye
level, vertically:

============ ==================== ==============================
Daytime      **≥ 250 lux** m-EDI  alert, entrained
Evening      **< 10 lux** m-EDI   from three hours before bed
Night        **< 1 lux** m-EDI    during sleep
============ ==================== ==============================

Two honest caveats, because they change what the numbers mean in a room:

* Those are *vertical* illuminances at the eye. A sensor reads something
  closer to horizontal, and vertical at eye level tends to run around half
  of it. A living room at 100 lux on a shelf is perhaps 20 m-EDI at the eye.
* Which means most living rooms already sit above the evening figure, and
  meeting it strictly is darker than most people will accept. The useful
  thing is the direction and the shape, not compliance.
"""

from __future__ import annotations

from typing import List, Tuple

#: Melanopic daylight efficacy ratio against correlated colour temperature,
#: for the phosphor-converted white LEDs used in lamps like these.
#:
#: Tabulated rather than fitted: the relationship is set by each lamp's own
#: spectrum, no formula reproduces it properly, and no manufacturer publishes
#: the figure per product. These are representative values, and what the
#: schedule below depends on is their *shape* — warming a lamp from 5000 K to
#: 2200 K cuts its effect on the clock by roughly a factor of four, whatever
#: the exact numbers turn out to be for a given bulb.
_DER_BY_KELVIN: List[Tuple[float, float]] = [
    (1800.0, 0.13),
    (2000.0, 0.16),
    (2200.0, 0.20),
    (2700.0, 0.34),
    (3000.0, 0.42),
    (3500.0, 0.52),
    (4000.0, 0.60),
    (5000.0, 0.76),
    (5700.0, 0.87),
    (6500.0, 0.95),
]


def melanopic_ratio(kelvin: float) -> float:
    """Melanopic DER for a white LED at this colour temperature.

    1.0 would be daylight itself. A warm lamp lands near 0.2, which is the
    entire reason a warm evening is worth anything.
    """
    if kelvin <= _DER_BY_KELVIN[0][0]:
        return _DER_BY_KELVIN[0][1]
    if kelvin >= _DER_BY_KELVIN[-1][0]:
        return _DER_BY_KELVIN[-1][1]
    for (k0, v0), (k1, v1) in zip(_DER_BY_KELVIN, _DER_BY_KELVIN[1:]):
        if k0 <= kelvin <= k1:
            return v0 + (v1 - v0) * (kelvin - k0) / (k1 - k0)
    return _DER_BY_KELVIN[-1][1]


def melanopic_edi(lux: float, kelvin: float) -> float:
    """What this much light, at this colour, means to the body clock."""
    return max(0.0, lux) * melanopic_ratio(kelvin)


def lux_for_edi(edi: float, kelvin: float) -> float:
    """How many lux of light at this colour gives that melanopic effect."""
    ratio = melanopic_ratio(kelvin)
    return edi / ratio if ratio > 0 else float("inf")


def colour_then_brightness(
    lux_target: float,
    edi_target: float,
    warm_kelvin: float,
    cool_kelvin: float,
) -> Tuple[float, float]:
    """Split a melanopic target into a colour and a brightness.

    Many pairs of (lux, kelvin) hit the same melanopic figure, so something
    has to choose between them, and the choice is not neutral. Warming the
    light and dimming it protect sleep equally well; they do not feel the
    same. A warm room at a decent level reads as cosy. A cool room dimmed to
    the same melanopic figure reads as gloomy, and gloomy is the one people
    turn back up — at which point it has protected nothing.

    So colour goes first. Brightness is only asked to give way once the light
    is as warm as the lamps can make it.
    """
    if edi_target <= 0 or lux_target <= 0:
        return 0.0, warm_kelvin

    needed_ratio = edi_target / lux_target
    warm_ratio = melanopic_ratio(warm_kelvin)
    cool_ratio = melanopic_ratio(cool_kelvin)

    if needed_ratio >= cool_ratio:
        # Daytime: we cannot be cool enough to reach the recommendation, so
        # be as cool as the lamps go and keep the brightness that was asked
        # for. Falling short here is normal — almost no home reaches 250.
        return lux_target, cool_kelvin

    if needed_ratio <= warm_ratio:
        # Evening: even the warmest light overshoots, so now dim as well.
        return min(lux_target, edi_target / warm_ratio), warm_kelvin

    return lux_target, _kelvin_for_ratio(needed_ratio, warm_kelvin, cool_kelvin)


def _kelvin_for_ratio(ratio: float, warm: float, cool: float) -> float:
    """Colour temperature whose melanopic ratio is ``ratio``.

    Bisected in mired, where the steps are perceptually even; walking in
    kelvin would crawl at the warm end and leap at the cool one.
    """
    low, high = 1_000_000.0 / cool, 1_000_000.0 / warm
    for _ in range(40):
        middle = (low + high) / 2.0
        if melanopic_ratio(1_000_000.0 / middle) > ratio:
            low = middle
        else:
            high = middle
    return 1_000_000.0 / ((low + high) / 2.0)


#: The consensus figures, in melanopic EDI at the eye.
DAYTIME_EDI = 250.0
EVENING_EDI = 10.0
NIGHT_EDI = 1.0

#: Roughly how much of a horizontal reading arrives vertically at the eye.
#: Varies enormously with where somebody sits and which way they face; it is
#: here so the arithmetic is visible rather than buried.
VERTICAL_FRACTION = 0.5
