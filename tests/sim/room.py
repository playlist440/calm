"""The room the controller thinks it is steering."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

from custom_components.calm.core.photometry import device_to_perceptual, perceptual_to_luminous


@dataclass
class SimulatedLamp:
    id: str
    #: Lux this lamp actually puts on the sensor at full — the ground truth
    #: the estimator is trying to find, and which it is never told.
    full_contribution: float
    floor: float = 0.04


@dataclass
class SimulatedRoom:
    """Lamps plus daylight, added up the way light actually adds up."""

    lamps: List[SimulatedLamp]
    brightness: Dict[str, int] = field(default_factory=dict)
    lights_on: bool = False

    def apply(self, brightness: Dict[str, int]) -> None:
        self.brightness = dict(brightness)

    def illuminance(self, daylight_lux: float) -> float:
        total = daylight_lux
        if not self.lights_on:
            return total
        for lamp in self.lamps:
            device = self.brightness.get(lamp.id, 0)
            level = device_to_perceptual(device)
            if level < lamp.floor:
                # Below its floor a real lamp does not dim, it goes out.
                continue
            total += lamp.full_contribution * perceptual_to_luminous(level)
        return total

    @property
    def full_contribution(self) -> float:
        return sum(lamp.full_contribution for lamp in self.lamps)

    def move_sensor(self, factor: float) -> None:
        """Rehang the sensor: every coupling changes at once.

        Which is exactly why it is worth simulating. The model is not a
        little wrong afterwards, it is wrong about everything, and the
        recovery has to work without anyone being asked to do something.
        """
        for lamp in self.lamps:
            lamp.full_contribution *= factor
