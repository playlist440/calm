"""One room inside Home Assistant: where the sensors come in and the lamps go out.

No decision about light is made here. That all lives in the core, where it
can be tested without Home Assistant. This file listens, keeps time, turns
commands into service calls, tells our own doing from a hand on the dimmer,
writes the day file and remembers what the room has learned.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_SUPPORTED_COLOR_MODES,
    ATTR_TRANSITION,
    ColorMode,
)
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import Context, Event, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from . import discovery
from .conditions import When, switch_off_reason
from .const import (
    CONF_BRIGHTNESS,
    CONF_EVENING_BRIGHTNESS,
    CONF_EXCLUDED,
    CONF_MANUAL_HOLD,
    CONF_MAX_LEVEL,
    CONF_MIN_LEVEL,
    CONF_MOTION_ALWAYS,
    CONF_MOTION_HOLD,
    CONF_NAME,
    CONF_NIGHTLIGHT_KELVIN,
    CONF_NIGHTLIGHT_LEVEL,
    CONF_OVERRIDES,
    CONF_STANDBY_KELVIN,
    CONF_STANDBY_LEVEL,
    CONF_THRESHOLD,
    CONF_USE_MOTION,
    CONF_WEIGHTS,
    CONF_WHEN_EMPTY,
    DEFAULT_BRIGHTNESS,
    DEFAULT_EVENING_BRIGHTNESS,
    DEFAULT_MANUAL_HOLD,
    DEFAULT_MAX_LEVEL,
    DEFAULT_MIN_LEVEL,
    DEFAULT_MOTION_HOLD,
    DEFAULT_NIGHTLIGHT_KELVIN,
    DEFAULT_NIGHTLIGHT_LEVEL,
    DEFAULT_STANDBY_KELVIN,
    DEFAULT_STANDBY_LEVEL,
    DEFAULT_THRESHOLD,
    DOMAIN,
    TICK,
)
from .core import (
    Action,
    ChangeAttributor,
    CommandPacer,
    ControllerConfig,
    DayCurve,
    DayCurveConfig,
    DimProfile,
    EmptyAction,
    Explanation,
    Lamp,
    OverrideSet,
    RoomController,
    RoomSettings,
    device_to_perceptual,
)
from .journal import Journal

if TYPE_CHECKING:
    from .hub import CalmHub

_LOGGER = logging.getLogger(__name__)

UNAVAILABLE = {STATE_UNAVAILABLE, STATE_UNKNOWN}
STORE_VERSION = 1
#: How long the first reading waits for the lamps to report at a start.
LAMPS_WAIT = timedelta(minutes=2)
#: How long after an off command's own fade a lamp may still say it is on.
PUT_OUT_GRACE_S = 60.0
#: Before the conditions were first looked at: any answer is a change.
_UNASKED = "unasked"
SAVE_EVERY = timedelta(minutes=15)

#: Colour modes in which a lamp takes a colour temperature. A lamp in any
#: other mode gets brightness only: asking a plain dimmable bulb for 2700 K
#: is an error in the log every thirty seconds.
_TAKES_KELVIN = {ColorMode.COLOR_TEMP, ColorMode.HS, ColorMode.XY, ColorMode.RGB,
                 ColorMode.RGBW, ColorMode.RGBWW}


def room_settings(data: Dict[str, Any], has_motion_sensor: bool) -> RoomSettings:
    """The core's view of a room's stored settings: percent to 0..1, minutes to seconds."""
    return RoomSettings(
        threshold_lux=float(data.get(CONF_THRESHOLD, DEFAULT_THRESHOLD)),
        brightness=float(data.get(CONF_BRIGHTNESS, DEFAULT_BRIGHTNESS)) / 100.0,
        evening_brightness=float(data.get(CONF_EVENING_BRIGHTNESS, DEFAULT_EVENING_BRIGHTNESS)) / 100.0,
        has_motion_sensor=has_motion_sensor,
        use_motion=bool(data.get(CONF_USE_MOTION, False)) and has_motion_sensor,
        motion_hold_s=float(data.get(CONF_MOTION_HOLD, DEFAULT_MOTION_HOLD)) * 60.0,
        when_empty=(
            EmptyAction.STANDBY if data.get(CONF_WHEN_EMPTY) == "standby" else EmptyAction.OFF
        ),
        standby_level=float(data.get(CONF_STANDBY_LEVEL, DEFAULT_STANDBY_LEVEL)) / 100.0,
        standby_kelvin=float(data.get(CONF_STANDBY_KELVIN, DEFAULT_STANDBY_KELVIN)),
        motion_always=bool(data.get(CONF_MOTION_ALWAYS, False)) and has_motion_sensor,
        nightlight_level=float(data.get(CONF_NIGHTLIGHT_LEVEL, DEFAULT_NIGHTLIGHT_LEVEL)) / 100.0,
        nightlight_kelvin=float(data.get(CONF_NIGHTLIGHT_KELVIN, DEFAULT_NIGHTLIGHT_KELVIN)),
        manual_hold_s=float(data.get(CONF_MANUAL_HOLD, DEFAULT_MANUAL_HOLD)) * 60.0,
    )


class CalmRoom:
    """One room, running."""

    def __init__(self, hub: "CalmHub", subentry_id: str, data: Dict[str, Any]) -> None:
        self.hub = hub
        self.hass: HomeAssistant = hub.hass
        self.subentry_id = subentry_id
        self.data = dict(data)
        self.name: str = self.data.get(CONF_NAME) or "Calm"

        hass = self.hass
        self.lights: List[str] = discovery.room_lights(hass, self.data)
        self.excluded = set(self.data.get(CONF_EXCLUDED) or [])
        self.light_sensor: Optional[str] = discovery.room_light_sensor(hass, self.data)
        self.motion_sensor: Optional[str] = discovery.room_motion_sensor(hass, self.data)
        self.when: When = hub.when_for(self.data)
        self.overrides = OverrideSet.from_list(self.data.get(CONF_OVERRIDES) or [])

        self.controller = self._build()
        self.pacer = CommandPacer()
        self.attribution = ChangeAttributor()
        self.journal = Journal(hass, self.name)
        self.store: Store = Store(hass, STORE_VERSION, f"{DOMAIN}.{hub.entry.entry_id}.{subentry_id}")

        #: The Calm switch: whether Calm runs this room right now. Driven by
        #: the conditions when they change, and by a person in between.
        self.switch_on = False
        #: Which condition failed last time: None when all held, _UNASKED
        #: before the first look.
        self._failing: Optional[str] = _UNASKED
        self._because: Optional[str] = None
        self._last_run: Optional[datetime] = None
        self._last_save: Optional[datetime] = None
        self._unsubscribe: List[Callable[[], None]] = []
        self._extinguished: Dict[str, datetime] = {}
        #: When Calm last put the room out, and how slowly: checked once
        #: afterwards, because an off command that got lost leaves a lamp
        #: burning all day with Calm believing the room dark.
        self._put_out: Optional[tuple] = None
        #: The first reading, held until the lamps have said whether they are
        #: on; see _seed.
        self._seeded = False
        self._held: Optional[float] = None
        self._started: Optional[datetime] = None
        self.commands_sent = 0
        self._call_failed: Optional[str] = None

    # -- building -------------------------------------------------------

    def _build(self) -> RoomController:
        data = self.data
        count = max(len(self.lights), 1)
        threshold = float(data.get(CONF_THRESHOLD, DEFAULT_THRESHOLD))
        weights = data.get(CONF_WEIGHTS) or {}
        floor = float(data.get(CONF_MIN_LEVEL, DEFAULT_MIN_LEVEL)) / 100.0
        ceiling = float(data.get(CONF_MAX_LEVEL, DEFAULT_MAX_LEVEL)) / 100.0
        lamps = [
            Lamp(
                id=entity_id,
                weight=float(weights.get(entity_id, 100)) / 100.0,
                floor=floor,
                ceiling=ceiling,
                # A placeholder until the room has measured it. The loop leans
                # on its own uncertainty until then rather than on this.
                full_contribution=max(threshold / count, 1.0),
                enabled=entity_id not in self.excluded,
            )
            for entity_id in self.lights
        ]
        if not any(lamp.enabled and lamp.weight > 0 for lamp in lamps):
            # A room with nothing to drive still has to exist, so its screen can
            # say what is missing. One placeholder that no command reaches.
            lamps = [Lamp(id="light.calm_none", enabled=True)]
        profile = DimProfile.from_lamps(lamps)
        return RoomController(
            profile=profile,
            settings=room_settings(data, has_motion_sensor=self.motion_sensor is not None),
            day_curve=DayCurve(self.hub.rhythm_for(data)),
            config=ControllerConfig(
                latitude=self.hass.config.latitude, longitude=self.hass.config.longitude
            ),
            overrides=self.overrides,
        )

    @property
    def language(self) -> str:
        return "nl" if (self.hass.config.language or "nl").startswith("nl") else "en"

    # -- lifecycle ------------------------------------------------------

    async def async_start(self) -> None:
        stored = await self.store.async_load()
        self._restore(stored or {})
        hass = self.hass
        if self.light_sensor:
            self._unsubscribe.append(async_track_state_change_event(
                hass, [self.light_sensor], self._sensor_changed))
        if self.motion_sensor:
            self._seed_presence()
            self._unsubscribe.append(async_track_state_change_event(
                hass, [self.motion_sensor], self._motion_changed))
        if self.lights:
            self._unsubscribe.append(async_track_state_change_event(
                hass, self.lights, self._light_changed))
        watched = self.when.watched() + [
            o.auto_entity for o in self.overrides if o.auto_entity
        ]
        if watched:
            self._unsubscribe.append(async_track_state_change_event(
                hass, sorted(set(watched)), self._context_changed))
        self._unsubscribe.append(async_track_time_interval(hass, self._tick, TICK))
        self._started = dt_util.now()
        # The first reading is whatever the sensor says now. Waiting for it to
        # speak can take hours in a dark room, because nought stays nought.
        self._run(self._current_lux())

    async def async_stop(self) -> None:
        for remove in self._unsubscribe:
            remove()
        self._unsubscribe.clear()
        # The settings may be changing; whoever still misses it says so again.
        self._report_missing(False)
        await self._save()

    # -- the switch -----------------------------------------------------

    def restore_switch(self, was_on: Optional[bool]) -> None:
        """What the switch was before a restart. Only matters when the
        household runs it themselves; otherwise the conditions decide."""
        if self.when.manual and was_on is not None:
            self.switch_on = was_on

    @callback
    def set_switch(self, on: bool) -> None:
        """A person or an automation flipped the Calm switch. Holds until
        the conditions next change."""
        self.switch_on = on
        self._because = None if on else switch_off_reason(self.language)
        self._run(None)

    def _follow_conditions(self, now: datetime) -> None:
        if self.when.manual:
            self.controller.set_enabled(
                self.switch_on, None if self.switch_on else switch_off_reason(self.language))
            return
        failing, because = self.when.check(self.hass, now, self.language)
        if failing != self._failing:
            # The conditions changed: the switch follows them, whatever a person
            # set it to in between. A different condition failing is a change
            # too: switched on by hand in night mode, and then everybody left,
            # the room goes out.
            self._failing = failing
            self.switch_on = failing is None
            self._because = because
            self._report_missing(failing == "entity_missing")
        elif failing is not None:
            self._because = because
        reason = self._because if not self.switch_on else None
        if not self.switch_on and reason is None:
            reason = switch_off_reason(self.language)
        self.controller.set_enabled(self.switch_on, reason)

    def _report_missing(self, missing: bool) -> None:
        """Put a gone condition entity under Repairs, where it is seen.

        Without it the room just stays dark and the only trace is a sentence
        on a sensor nobody is looking at.
        """
        entity = self.when.entity
        if not entity:
            return
        issue_id = _missing_issue_id(entity)
        if missing:
            ir.async_create_issue(
                self.hass, DOMAIN, issue_id, is_fixable=False, is_persistent=False,
                severity=ir.IssueSeverity.WARNING, translation_key="condition_missing",
                translation_placeholders={"entity": entity},
            )
        else:
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)

    # -- what people press ----------------------------------------------

    @callback
    def set_override(self, key: str, on: bool) -> None:
        if self.controller.set_override(key, on):
            self._run(None)

    @callback
    def set_threshold(self, lux: float) -> None:
        self.controller.set_threshold(lux)
        self.data[CONF_THRESHOLD] = float(lux)
        self.hub.remember_live(self.subentry_id, CONF_THRESHOLD, float(lux))
        self._run(None)

    @callback
    def set_brightness(self, percent: float) -> None:
        self.controller.set_brightness(percent / 100.0)
        self.data[CONF_BRIGHTNESS] = float(percent)
        self.hub.remember_live(self.subentry_id, CONF_BRIGHTNESS, float(percent))
        self._run(None)

    @callback
    def set_evening_brightness(self, percent: float) -> None:
        self.controller.set_evening_brightness(percent / 100.0)
        self.data[CONF_EVENING_BRIGHTNESS] = float(percent)
        self.hub.remember_live(self.subentry_id, CONF_EVENING_BRIGHTNESS, float(percent))
        self._run(None)

    @callback
    def release_manual(self) -> None:
        self.controller.release_manual()
        self._run(None)

    # -- what the house tells us ----------------------------------------

    def _current_lux(self) -> Optional[float]:
        if not self.light_sensor:
            return None
        state = self.hass.states.get(self.light_sensor)
        if state is None or state.state in UNAVAILABLE:
            return None
        try:
            return float(state.state)
        except (TypeError, ValueError):
            return None

    @callback
    def _sensor_changed(self, event: Event) -> None:
        new = event.data.get("new_state")
        if new is None or new.state in UNAVAILABLE:
            return
        try:
            lux = float(new.state)
        except (TypeError, ValueError):
            return
        self._run(lux)

    def _seed_presence(self) -> None:
        state = self.hass.states.get(self.motion_sensor)
        if state is None or state.state in UNAVAILABLE:
            return
        # When it last changed, not now: a room that has been clear for an
        # hour is known to have been clear for an hour.
        self.controller.motion(state.last_changed, state.state == STATE_ON)

    @callback
    def _motion_changed(self, event: Event) -> None:
        new = event.data.get("new_state")
        if new is None or new.state in UNAVAILABLE:
            return
        self.controller.motion(new.last_changed, new.state == STATE_ON)
        # Straight away: somebody walked in and the light should be there
        # before they have crossed the room.
        self._run(None)

    @callback
    def _context_changed(self, event: Event) -> None:
        self._run(None)

    @callback
    def _light_changed(self, event: Event) -> None:
        """Tell our own doing from somebody else's hand on the dimmer."""
        new = event.data.get("new_state")
        old = event.data.get("old_state")
        if not self._seeded and new is not None and new.state not in UNAVAILABLE:
            # The lamps' integration has loaded: the held first reading can count.
            self._run(None)
        if new is None or old is None or new.state in UNAVAILABLE or old.state in UNAVAILABLE:
            return
        entity_id = event.data["entity_id"]
        now = dt_util.now()
        if new.state != STATE_ON and old.state == STATE_ON:
            if not self._wanted_on(entity_id):
                # Out is what Calm wanted too, however late the lamp says so.
                return
            if not self.attribution.is_ours(now, entity_id, None, None, event.context.id):
                _LOGGER.info("%s: met de hand uitgezet (%s)", self.name, entity_id)
                self.controller.notify_external_off()
                self.hub.notify(self.subentry_id)
            return
        if new.state != STATE_ON:
            return
        kelvin = new.attributes.get(ATTR_COLOR_TEMP_KELVIN)
        mired = 1_000_000.0 / kelvin if kelvin else None
        if not self.attribution.is_ours(
            now, entity_id, new.attributes.get(ATTR_BRIGHTNESS), mired, event.context.id
        ):
            # Said at warning level, with what was asked for: September had
            # two hours of "manual" nobody could explain, and the next one
            # should explain itself.
            _LOGGER.warning(
                "%s: met de hand overgenomen: %s ging naar helderheid %s (Calm vroeg %s)",
                self.name, entity_id, new.attributes.get(ATTR_BRIGHTNESS),
                (self.controller.last_command.brightness or {}).get(entity_id)
                if self.controller.last_command else "niets",
            )
            self.controller.notify_manual_change()
            self.hub.notify(self.subentry_id)

    # -- the loop -------------------------------------------------------

    @callback
    def _tick(self, _now=None) -> None:
        self._run(None)

    def _elapsed(self, now: datetime) -> float:
        """Real seconds since the last run, not the number of runs.

        The loop wakes on the clock and on every reading and movement; a
        fixed thirty either way credited a busy kitchen with minutes it never
        had, and its ten-minute wait ran out in under three.
        """
        last, self._last_run = self._last_run, now
        if last is None:
            return TICK.total_seconds()
        seconds = (now - last).total_seconds()
        return seconds if seconds >= 0 else TICK.total_seconds()

    @callback
    def _run(self, measured_lux: Optional[float]) -> None:
        now = dt_util.now()
        if not self._seeded:
            measured_lux = self._seed(now, measured_lux)
        dt_seconds = self._elapsed(now)
        self._follow_conditions(now)
        self.controller.set_auto_overrides({
            o.key for o in self.overrides
            if o.wants_on(now, self._state_of(o.auto_entity))
        })
        command = self.controller.step(now, measured_lux, dt_seconds)
        sent = self._apply(now, command)
        self.commands_sent += sent
        motion = occupied = None
        if self.motion_sensor:
            motion = self.controller.presence.detecting
            occupied = self.controller.occupied
        self.hass.async_create_task(self.journal.async_write(
            now, measured_lux, command, sent, self.controller.lit, motion, occupied))
        if self._last_save is None or now - self._last_save >= SAVE_EVERY:
            self._last_save = now
            self.hass.async_create_task(self._save())
        self.hub.notify(self.subentry_id)

    def _state_of(self, entity_id: Optional[str]) -> Optional[str]:
        if not entity_id:
            return None
        state = self.hass.states.get(entity_id)
        return state.state if state is not None else None

    # -- the lamps ------------------------------------------------------

    def _apply(self, now: datetime, command) -> int:
        """Send what the command asks for. Returns how many calls went out."""
        if not self.lights:
            return 0
        if command.action is Action.NONE:
            return self._check_put_out(now)
        if command.action is Action.TURN_OFF:
            _LOGGER.debug(
                "%s: turn_off | daglicht %.0f lx, lijn %.0f lx, in %.0f s, reden %s",
                self.name, float(command.diagnostics.get("daylight_lux") or 0),
                float(command.diagnostics.get("threshold_lux") or 0),
                command.transition_s, command.reason,
            )
            self.attribution.expect(now, {}, command.mired, command.transition_s)
            self._call(SERVICE_TURN_OFF, {
                ATTR_ENTITY_ID: list(self.lights), ATTR_TRANSITION: command.transition_s})
            self.pacer.reset()
            self._put_out = (now, command.transition_s)
            return 1
        self._put_out = None

        snapping = command.action is Action.TURN_ON or command.transition_s != self.controller.config.interval_s
        brightness = {k: v for k, v in command.brightness.items() if k in self.lights}
        emission = self.pacer.emit(
            now=now, brightness=brightness, mired=command.mired,
            transition_s=command.transition_s if snapping else None, force=snapping,
        )
        if emission.groups:
            _LOGGER.debug(
                "%s: %s | %s, daglicht %.0f lx, lijn %.0f lx, %.0f K, stand %.2f, reden %s",
                self.name, command.action.value, command.diagnostics.get("scene"),
                float(command.diagnostics.get("daylight_lux") or 0),
                float(command.diagnostics.get("threshold_lux") or 0),
                1_000_000.0 / command.mired, command.master, command.reason,
            )
            # Say what was asked for before sending it: the first report back
            # can arrive before the service call returns.
            self.attribution.expect(now, brightness, command.mired, emission.transition_s)

        calls = 0
        stray = self._stray_lamps(now, brightness)
        if stray:
            # A lamp this scene does not drive has to be put out, not just left
            # alone: otherwise leaving it out of an override leaves it burning.
            self._call(SERVICE_TURN_OFF, {
                ATTR_ENTITY_ID: stray, ATTR_TRANSITION: min(emission.transition_s, 4.0)})
            calls += 1
        for group in emission.groups:
            kelvin = int(round(1_000_000 / group.mired))
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

    def _split_by_colour(self, lamp_ids: List[str]):
        takes, plain = [], []
        for entity_id in lamp_ids:
            state = self.hass.states.get(entity_id)
            modes = set(state.attributes.get(ATTR_SUPPORTED_COLOR_MODES) or []) if state else set()
            (takes if modes & _TAKES_KELVIN else plain).append(entity_id)
        return takes, plain

    def _check_put_out(self, now: datetime) -> int:
        """Once the room has had time to go out, put out what still burns.

        Once only: a lamp that never reports itself off must not become a
        command every thirty seconds.
        """
        if self._put_out is None:
            return 0
        when, transition_s = self._put_out
        if now - when < timedelta(seconds=transition_s + PUT_OUT_GRACE_S):
            return 0
        self._put_out = None
        if self.controller.lit or self.controller.manual:
            return 0
        burning = [
            entity_id for entity_id in self.lights
            if (state := self.hass.states.get(entity_id)) is not None and state.state == STATE_ON
        ]
        if not burning:
            return 0
        _LOGGER.info("%s: %s brandde nog na het uitzetten, opnieuw uit", self.name, ", ".join(burning))
        self._call(SERVICE_TURN_OFF, {ATTR_ENTITY_ID: burning, ATTR_TRANSITION: 2.0})
        return 1

    def _seed(self, now: datetime, measured_lux: Optional[float]) -> Optional[float]:
        """Before the first reading counts, know what the lamps are doing.

        Lamps already burning are Calm's own light, not daylight. 30
        September, 21:14: Calm started fifteen seconds before the Hue lamps
        had a state, and took their light for daylight. So the first reading
        is held until a lamp has said whether it is on, or two minutes have
        gone by. Only when Calm may run the room: otherwise the lamps are
        somebody else's to keep.
        """
        if measured_lux is not None:
            self._held = measured_lux
        if self._held is None:
            return None
        started = self._started or now
        if not self._lamps_known() and now - started < LAMPS_WAIT:
            return None
        self._seeded = True
        self._follow_conditions(now)
        if self.controller.enabled and (burning := self._burning()) is not None:
            self.controller.assume_lit(*burning)
        held, self._held = self._held, None
        return held

    def _lamps_known(self) -> bool:
        return any(
            (state := self.hass.states.get(entity_id)) is not None and state.state not in UNAVAILABLE
            for entity_id in self.lights
        )

    def _burning(self) -> Optional[tuple]:
        """Roughly where Calm's own scale and colour stand, read off lamps
        already on: (master, mired), or None when none is."""
        levels, mireds = [], []
        for lamp in self.controller.profile.active:
            state = self.hass.states.get(lamp.id)
            if state is None or state.state != STATE_ON or not lamp.weight:
                continue
            device = state.attributes.get(ATTR_BRIGHTNESS)
            if device:
                levels.append(device_to_perceptual(int(device)) / lamp.weight)
            if kelvin := state.attributes.get(ATTR_COLOR_TEMP_KELVIN):
                mireds.append(1_000_000.0 / float(kelvin))
        if not levels:
            return None
        return min(1.0, max(levels)), (sum(mireds) / len(mireds) if mireds else None)

    def _wanted_on(self, entity_id: str) -> bool:
        """Whether Calm has this lamp on right now."""
        command = self.controller.last_command
        return bool(
            self.controller.lit and command is not None
            and (command.brightness or {}).get(entity_id, 0) > 0
        )

    def _stray_lamps(self, now: datetime, addressed: Dict[str, int]) -> List[str]:
        """Lamps of this room that are lit but this scene does not drive.

        Read off Home Assistant's state rather than remembered, so it survives
        reloads and a lamp somebody switched on by hand.
        """
        cutoff = now - timedelta(seconds=300)
        stray = []
        for entity_id in self.lights:
            if entity_id in addressed:
                self._extinguished.pop(entity_id, None)
                continue
            state = self.hass.states.get(entity_id)
            if state is None or state.state != STATE_ON:
                continue
            # A lamp that never reports itself off should not become a
            # command every thirty seconds.
            last = self._extinguished.get(entity_id)
            if last is not None and last > cutoff:
                continue
            self._extinguished[entity_id] = now
            stray.append(entity_id)
        return stray

    @callback
    def _call(self, service: str, data: Dict[str, Any]) -> None:
        context = Context()
        self.attribution.issued(context.id)
        self.hass.async_create_task(self._async_call(service, data, context))

    async def _async_call(self, service: str, data: Dict[str, Any], context: Context) -> None:
        """Send one command, and say so if it could not be sent.

        Fire and forget, but never into the void: a lamp removed from Home
        Assistant, or a light integration still loading, is worth one line in
        the log and not an unhandled error every thirty seconds.
        """
        try:
            await self.hass.services.async_call("light", service, data, blocking=False, context=context)
        except HomeAssistantError as error:
            if self._call_failed != str(error):
                _LOGGER.warning("%s: lampcommando niet verstuurd: %s", self.name, error)
            self._call_failed = str(error)
        else:
            self._call_failed = None

    # -- explaining -----------------------------------------------------

    def explanation(self) -> Explanation:
        return self.controller.explanation(self.language)

    # -- memory ---------------------------------------------------------

    async def _save(self) -> None:
        await self.store.async_save({
            "mapping": self.controller.mapping.snapshot(),
            "estimate": self.controller.estimator.snapshot(),
            "contributions": {
                lamp.id: lamp.full_contribution for lamp in self.controller.base_profile.lamps
            },
        })

    def _restore(self, stored: Dict[str, Any]) -> None:
        if stored.get("mapping"):
            self.controller.mapping.restore(stored["mapping"])
        for lamp in self.controller.base_profile.lamps:
            value = (stored.get("contributions") or {}).get(lamp.id)
            if value:
                lamp.full_contribution = float(value)
        # After the lamps, so the scale lands on the profile it was learned against.
        if stored.get("estimate"):
            self.controller.estimator.restore(stored["estimate"])


def _missing_issue_id(entity_id: str) -> str:
    return f"condition_missing_{entity_id}"

