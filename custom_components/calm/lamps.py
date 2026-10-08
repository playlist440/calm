"""Turning a room's commands into service calls, and reading the lamps back.

Owns everything that touches the lamps' entities: sending commands under
contexts Calm can recognise, putting out lamps a scene does not drive,
checking once that an off command arrived, reading the level and colour of
lamps found burning, and classifying a change somebody else made.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Coroutine
from datetime import datetime
from enum import Enum
from typing import Any, NamedTuple

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_SUPPORTED_COLOR_MODES,
    ATTR_TRANSITION,
)
from homeassistant.components.light.const import ColorMode
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import Context, HomeAssistant, State, callback
from homeassistant.exceptions import HomeAssistantError

from .const import PUT_OUT_GRACE, STRAY_MAX_TRANSITION_S, STRAY_RETRY
from .core import (
    Action,
    ChangeAttributor,
    Command,
    CommandPacer,
    DimProfile,
    device_to_perceptual,
)

_LOGGER = logging.getLogger(__name__)

UNAVAILABLE = {STATE_UNAVAILABLE, STATE_UNKNOWN}

#: Colour modes in which a lamp takes a colour temperature. Any other lamp
#: gets brightness only.
_TAKES_KELVIN = {ColorMode.COLOR_TEMP, ColorMode.HS, ColorMode.XY, ColorMode.RGB,
                 ColorMode.RGBW, ColorMode.RGBWW}


class Burning(NamedTuple):
    """Lamps found on: where Calm's scale and colour stand."""

    master: float
    mired: float | None


class Change(Enum):
    """What a reported lamp change means for the room."""

    NOTHING = "nothing"
    TURNED_OFF = "turned_off"
    TAKEN_OVER = "taken_over"


class LampDriver:
    """The lamps of one room."""

    def __init__(
        self,
        hass: HomeAssistant,
        name: str,
        lights: list[str],
        spawn: Callable[[Coroutine[Any, Any, None], str], None],
        on_call_result: Callable[[str | None], None],
    ) -> None:
        self.hass = hass
        self.name = name
        self.lights = lights
        self.attribution = ChangeAttributor()
        self.pacer = CommandPacer()
        self._spawn = spawn
        self._on_call_result = on_call_result
        self._extinguished: dict[str, datetime] = {}
        #: When the room was put out, and how slowly: checked once afterwards.
        self._put_out: tuple[datetime, float] | None = None

    # -- sending ----------------------------------------------------------

    def apply(self, now: datetime, command: Command, lit: bool, manual: bool, interval_s: float) -> int:
        """Send what the command asks for. Returns how many calls went out."""
        if not self.lights:
            return 0
        if command.action is Action.NONE:
            return self._check_put_out(now, lit, manual)
        if command.action is Action.TURN_OFF:
            _LOGGER.debug("%s: turn_off in %.0f s, reason %s", self.name, command.transition_s, command.reason)
            self.attribution.expect(now, {}, command.mired, command.transition_s)
            self._call(SERVICE_TURN_OFF, {ATTR_ENTITY_ID: list(self.lights), ATTR_TRANSITION: command.transition_s})
            self.pacer.reset()
            self._put_out = (now, command.transition_s)
            return 1
        self._put_out = None

        snapping = command.action is Action.TURN_ON or command.transition_s != interval_s
        brightness = {k: v for k, v in command.brightness.items() if k in self.lights}
        emission = self.pacer.emit(
            now=now, brightness=brightness, mired=command.mired,
            transition_s=command.transition_s if snapping else None, force=snapping,
        )
        if emission.groups:
            _LOGGER.debug("%s: %s at %.2f, %.0f K, reason %s", self.name, command.action.value,
                          command.master, 1_000_000.0 / command.mired, command.reason)
            # Recorded before sending: the first report can beat the call's return.
            self.attribution.expect(now, brightness, command.mired, emission.transition_s)

        calls = 0
        stray = self._stray_lamps(now, brightness)
        if stray:
            # A lamp the scene does not drive is put out, not left burning.
            self._call(SERVICE_TURN_OFF, {
                ATTR_ENTITY_ID: stray, ATTR_TRANSITION: min(emission.transition_s, STRAY_MAX_TRANSITION_S)})
            calls += 1
        for group in emission.groups:
            kelvin = round(1_000_000 / group.mired)
            takes, plain = self._split_by_colour(list(group.lamp_ids))
            if takes:
                self._call(SERVICE_TURN_ON, {
                    ATTR_ENTITY_ID: takes, ATTR_BRIGHTNESS: group.brightness,
                    ATTR_COLOR_TEMP_KELVIN: kelvin, ATTR_TRANSITION: emission.transition_s})
                calls += 1
            if plain:
                self._call(SERVICE_TURN_ON, {
                    ATTR_ENTITY_ID: plain, ATTR_BRIGHTNESS: group.brightness,
                    ATTR_TRANSITION: emission.transition_s})
                calls += 1
        return calls

    def _split_by_colour(self, lamp_ids: list[str]) -> tuple[list[str], list[str]]:
        takes: list[str] = []
        plain: list[str] = []
        for entity_id in lamp_ids:
            state = self.hass.states.get(entity_id)
            modes = set(state.attributes.get(ATTR_SUPPORTED_COLOR_MODES) or []) if state else set()
            (takes if modes & _TAKES_KELVIN else plain).append(entity_id)
        return takes, plain

    def _check_put_out(self, now: datetime, lit: bool, manual: bool) -> int:
        """After an off command's fade, put out once more whatever still burns."""
        if self._put_out is None:
            return 0
        when, transition_s = self._put_out
        if (now - when).total_seconds() < transition_s + PUT_OUT_GRACE.total_seconds():
            return 0
        self._put_out = None
        if lit or manual:
            return 0
        burning = [entity_id for entity_id in self.lights if self._is_on(self.hass.states.get(entity_id))]
        if not burning:
            return 0
        _LOGGER.info("%s: %s still on after being put out; sending off again", self.name, ", ".join(burning))
        self._call(SERVICE_TURN_OFF, {ATTR_ENTITY_ID: burning, ATTR_TRANSITION: 2.0})
        return 1

    def _stray_lamps(self, now: datetime, addressed: dict[str, int]) -> list[str]:
        """Lamps of this room that are on but the scene does not drive.

        Read off Home Assistant's state, so it survives reloads; each lamp is
        asked at most once per STRAY_RETRY.
        """
        stray = []
        for entity_id in self.lights:
            if entity_id in addressed:
                self._extinguished.pop(entity_id, None)
                continue
            if not self._is_on(self.hass.states.get(entity_id)):
                continue
            last = self._extinguished.get(entity_id)
            if last is not None and now - last < STRAY_RETRY:
                continue
            self._extinguished[entity_id] = now
            stray.append(entity_id)
        return stray

    @callback
    def _call(self, service: str, data: dict[str, Any]) -> None:
        context = Context()
        self.attribution.issued(context.id)
        self._spawn(self._async_call(service, data, context), f"calm {self.name} light.{service}")

    async def _async_call(self, service: str, data: dict[str, Any], context: Context) -> None:
        """Send one command; a failure is reported once, not every tick."""
        try:
            await self.hass.services.async_call("light", service, data, blocking=False, context=context)
        except HomeAssistantError as error:
            self._on_call_result(str(error))
        else:
            self._on_call_result(None)

    # -- reading ----------------------------------------------------------

    def known(self) -> bool:
        """Whether any lamp has reported a state yet."""
        return any(
            (state := self.hass.states.get(entity_id)) is not None and state.state not in UNAVAILABLE
            for entity_id in self.lights
        )

    def burning(self, profile: DimProfile) -> Burning | None:
        """Level and colour of lamps found on, or None when none is."""
        levels: list[float] = []
        mireds: list[float] = []
        for lamp in profile.active:
            state = self.hass.states.get(lamp.id)
            if not self._is_on(state) or not lamp.weight:
                continue
            assert state is not None
            device = state.attributes.get(ATTR_BRIGHTNESS)
            if device:
                levels.append(device_to_perceptual(int(device)) / lamp.weight)
            kelvin = state.attributes.get(ATTR_COLOR_TEMP_KELVIN) or kelvin_from_xy(state.attributes.get("xy_color"))
            if kelvin:
                mireds.append(1_000_000.0 / float(kelvin))
        if not levels:
            return None
        return Burning(min(1.0, max(levels)), sum(mireds) / len(mireds) if mireds else None)

    def classify(
        self, now: datetime, entity_id: str, old: State | None, new: State | None,
        context_id: str | None, wanted_on: bool,
    ) -> Change:
        """What a reported change of one lamp means."""
        if new is None or old is None or new.state in UNAVAILABLE or old.state in UNAVAILABLE:
            return Change.NOTHING
        if new.state != STATE_ON and old.state == STATE_ON:
            # Out is only a hand when Calm wanted the lamp on.
            if wanted_on and not self.attribution.is_ours(now, entity_id, None, None, context_id):
                return Change.TURNED_OFF
            return Change.NOTHING
        if new.state != STATE_ON:
            return Change.NOTHING
        kelvin = new.attributes.get(ATTR_COLOR_TEMP_KELVIN)
        mired = 1_000_000.0 / kelvin if kelvin else None
        if self.attribution.is_ours(now, entity_id, new.attributes.get(ATTR_BRIGHTNESS), mired, context_id):
            return Change.NOTHING
        return Change.TAKEN_OVER

    @staticmethod
    def _is_on(state: State | None) -> bool:
        return state is not None and state.state == STATE_ON


def kelvin_from_xy(xy: Any) -> float | None:
    """Colour temperature from an xy colour (McCamy), for lamps reporting xy."""
    try:
        x, y = float(xy[0]), float(xy[1])
    except (TypeError, ValueError, IndexError):
        return None
    if abs(0.1858 - y) < 1e-6:
        return None
    n = (x - 0.3320) / (0.1858 - y)
    kelvin = 449.0 * n ** 3 + 3525.0 * n ** 2 + 6823.3 * n + 5520.33
    return kelvin if 1000.0 <= kelvin <= 10000.0 else None
