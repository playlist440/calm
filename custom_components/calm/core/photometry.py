"""Conversions between the three scales this project lives in.

- **perceptual** (0..1) — how bright a person judges the light to be.
- **luminous** (0..1) — how much light is actually emitted, relative to full.
- **device** (0..255) — what Home Assistant sends to a light.

The distinction matters more than it looks. Light *adds up* only in luminous
space: two lamps each at luminous 0.3 put 0.6 on a sensor. Rate limits and
dead bands are only meaningful in perceptual space: a change of 0.02 means
the same thing to the eye at the bottom of the range as at the top.

Mixing the two up is the classic way to build a dimmer that crawls at the
top and lurches at the bottom, so every conversion is explicit here and the
rest of the package never does the arithmetic inline.
"""

from __future__ import annotations

_L_KNEE = 8.0
_LUMINOUS_KNEE = 0.008856451679035631
_LINEAR_SLOPE = 903.2962962962963


def perceptual_to_luminous(perceptual: float) -> float:
    """Perceived brightness (0..1) to relative light output (0..1).

    Uses the CIE L* curve, the same relationship displays and lighting use
    to make a dimmer feel linear. Half-perceived is about 18% of the light.
    """
    p = _clamp01(perceptual)
    lightness = p * 100.0
    if lightness > _L_KNEE:
        return ((lightness + 16.0) / 116.0) ** 3
    return lightness / _LINEAR_SLOPE


def luminous_to_perceptual(luminous: float) -> float:
    """Relative light output (0..1) back to perceived brightness (0..1)."""
    y = _clamp01(luminous)
    if y > _LUMINOUS_KNEE:
        lightness = 116.0 * (y ** (1.0 / 3.0)) - 16.0
    else:
        lightness = y * _LINEAR_SLOPE
    return _clamp01(lightness / 100.0)


def perceptual_to_device(perceptual: float) -> int:
    """Perceived brightness to the 0..255 Home Assistant sends to a light.

    Hue's own dimming curve is already close to perceptual, so this is a
    plain scaling. Where a lamp turns out to differ, the deviation is
    absorbed by the estimator rather than hard-coded here.
    """
    return int(round(_clamp01(perceptual) * 255.0))


def device_to_perceptual(device: int) -> float:
    """The 0..255 brightness of a light back to perceived brightness."""
    return _clamp01(device / 255.0)


def kelvin_to_mired(kelvin: float) -> float:
    """Colour temperature in kelvin to mired.

    Interpolating colour in mired rather than kelvin matters: 2000 K to
    2500 K is a large visible step, 6000 K to 6500 K is barely one, and in
    mired both are the same distance.
    """
    if kelvin <= 0:
        raise ValueError("kelvin must be positive")
    return 1_000_000.0 / kelvin


def mired_to_kelvin(mired: float) -> float:
    """Mired back to kelvin."""
    if mired <= 0:
        raise ValueError("mired must be positive")
    return 1_000_000.0 / mired


def lux_ratio_to_perceptual_delta(ratio: float) -> float:
    """How large a change in lux feels, as a fraction of the visible range.

    The eye responds to ratios, not differences, so a dead band is stated
    as "10% more light" rather than "20 lux more".
    """
    if ratio <= 0:
        raise ValueError("ratio must be positive")
    return luminous_to_perceptual(min(1.0, ratio)) if ratio < 1 else 1.0


def _clamp01(value: float) -> float:
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value
