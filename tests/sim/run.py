"""Run a room through a simulated day and keep every number."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, List, Optional

from custom_components.calm.core import Action, RoomController

from .daylight import DaylightModel
from .room import SimulatedRoom
from .sensor import HueLightSensor


@dataclass
class TraceRow:
    when: datetime
    true_lux: float
    daylight_lux: float
    reported_lux: Optional[float]
    master: float
    mired: float
    action: str
    reason: str
    scene: str
    rung: Optional[str]
    transition_s: float
    lit: bool


@dataclass
class Trace:
    rows: List[TraceRow] = field(default_factory=list)

    def between(self, start: datetime, end: datetime) -> "Trace":
        return Trace([r for r in self.rows if start <= r.when < end])

    def switched_on(self) -> List[TraceRow]:
        return [r for r in self.rows if r.action == Action.TURN_ON.value]

    def switched_off(self) -> List[TraceRow]:
        return [r for r in self.rows if r.action == Action.TURN_OFF.value]


def run_day(
    controller: RoomController,
    room: SimulatedRoom,
    daylight: DaylightModel,
    sensor: HueLightSensor,
    start: datetime,
    hours: float,
    tick_s: float = 30.0,
    enabled: Optional[Callable[[datetime], bool]] = None,
    motion: Optional[Callable[[datetime], bool]] = None,
) -> Trace:
    """Step the whole loop forward and record it.

    The room only ever sees what the sensor chose to report, and gets
    ``None`` on every tick it stayed quiet, which is most of them.
    """
    trace = Trace()
    now = start
    end = start + timedelta(hours=hours)
    was_moving: Optional[bool] = None

    while now < end:
        if enabled is not None:
            controller.set_enabled(bool(enabled(now)))
        if motion is not None:
            moving = bool(motion(now))
            if moving != was_moving:
                controller.motion(now, moving)
                was_moving = moving

        daylight_lux = daylight.step(now, tick_s)
        true_lux = room.illuminance(daylight_lux)
        reported = sensor.observe(true_lux, tick_s)
        command = controller.step(now, None if reported is None else float(reported), tick_s)

        if command.action is Action.TURN_OFF:
            room.lights_on = False
            room.apply({})
        elif command.action in (Action.APPLY, Action.TURN_ON):
            room.lights_on = True
            room.apply(command.brightness)

        trace.rows.append(TraceRow(
            when=now,
            true_lux=true_lux,
            daylight_lux=daylight_lux,
            reported_lux=reported,
            master=command.master,
            mired=command.mired,
            action=command.action.value,
            reason=command.reason,
            scene=str(command.diagnostics.get("scene", "")),
            rung=command.diagnostics.get("rung"),
            transition_s=command.transition_s,
            lit=controller.lit,
        ))
        now += timedelta(seconds=tick_s)
    return trace
