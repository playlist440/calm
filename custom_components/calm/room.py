"""One room inside Home Assistant: where the sensors come in and the lamps go out.

No decision about light is made here. That all lives in the core, where it
can be tested without Home Assistant. This file listens, keeps time, turns
commands into service calls, tells our own doing from a hand on the dimmer,
writes the day file and remembers what the room has learned.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Coroutine
from datetime import datetime
from typing import TYPE_CHECKING, Any

from homeassistant.const import STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.restore_state import async_get as async_get_restore_data
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from . import discovery
from .conditions import When, switch_off_reason
from .const import (
    CONF_BRIGHTNESS,
    CONF_EVENING_BRIGHTNESS,
    CONF_EXCLUDED,
    CONF_JOURNAL,
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
    FAILED_CALLS_BEFORE_ISSUE,
    SAVE_EVERY,
    STARTUP_WAIT,
    TICK,
)
from .core import (
    ChangeAttributor,
    ControllerConfig,
    DayCurve,
    DimProfile,
    EmptyAction,
    Explanation,
    Lamp,
    OverrideSet,
    RoomController,
    RoomSettings,
)
from .journal import Journal
from .lamps import Change, LampDriver

if TYPE_CHECKING:
    from .hub import CalmHub

_LOGGER = logging.getLogger(__name__)

UNAVAILABLE = {STATE_UNAVAILABLE, STATE_UNKNOWN}
STORE_VERSION = 1
#: Before the conditions were first looked at: any answer is a change.
_UNASKED = "unasked"


def room_settings(data: dict[str, Any], has_motion_sensor: bool) -> RoomSettings:
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

    def __init__(self, hub: CalmHub, subentry_id: str, data: dict[str, Any]) -> None:
        self.hub = hub
        self.hass: HomeAssistant = hub.hass
        self.subentry_id = subentry_id
        self.data = dict(data)
        self.name: str = self.data.get(CONF_NAME) or "Calm"

        hass = self.hass
        self.lights: list[str] = discovery.room_lights(hass, self.data)
        self.excluded = set(self.data.get(CONF_EXCLUDED) or [])
        self.light_sensor: str | None = discovery.room_light_sensor(hass, self.data)
        self.motion_sensor: str | None = discovery.room_motion_sensor(hass, self.data)
        self.when: When = hub.when_for(self.data)
        self.overrides = OverrideSet.from_list(self.data.get(CONF_OVERRIDES) or [])

        self.controller: RoomController = self._build()
        self.lamps = LampDriver(hass, self.name, self.lights, self._spawn, self._call_result)
        self.journal: Journal | None = (
            Journal(hass, subentry_id) if hub.entry.options.get(CONF_JOURNAL) else None
        )
        self.store: Store[dict[str, Any]] = Store(hass, STORE_VERSION, store_key(hub.entry.entry_id, subentry_id))

        #: The Calm switch: whether Calm runs this room right now. Driven by
        #: the conditions when they change, and by a person in between.
        self.switch_on = False
        #: Which condition failed last time: None when all held, _UNASKED
        #: before the first look.
        self._failing: str | None = _UNASKED
        self._because: str | None = None
        self._last_run: datetime | None = None
        self._last_save: datetime | None = None
        self._unsubscribe: list[Callable[[], None]] = []
        self._tasks: set[Any] = set()
        #: The first reading, held until the lamps and the conditions have
        #: reported; see _seed.
        self._seeded = False
        self._held: float | None = None
        self._started: datetime | None = None
        self.commands_sent: int = 0
        self._failed_calls = 0

    @property
    def attribution(self) -> ChangeAttributor:
        return self.lamps.attribution

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
                # A placeholder until the room has measured it.
                full_contribution=max(threshold / count, 1.0),
                enabled=entity_id not in self.excluded,
            )
            for entity_id in self.lights
        ]
        if not any(lamp.enabled and lamp.weight > 0 for lamp in lamps):
            # A room with nothing to drive still exists, so its screen can say
            # what is missing: one placeholder no command reaches.
            lamps = [Lamp(id="light.calm_none", enabled=True)]
        return RoomController(
            profile=DimProfile.from_lamps(lamps),
            settings=room_settings(data, has_motion_sensor=self.motion_sensor is not None),
            day_curve=DayCurve(self.hub.rhythm_for(data)),
            config=ControllerConfig(latitude=self.hass.config.latitude, longitude=self.hass.config.longitude),
            overrides=self.overrides,
        )

    @property
    def language(self) -> str:
        """Dutch for a Dutch house, English otherwise."""
        return "nl" if (self.hass.config.language or "en").startswith("nl") else "en"

    # -- lifecycle ------------------------------------------------------

    async def async_start(self, previous: CalmRoom | None = None) -> None:
        """Start listening. ``previous`` is the room this one replaces after
        its area changed: its switch carries over."""
        stored = await self.store.async_load()
        self._restore(stored or {})
        if previous is not None:
            self.switch_on, self._failing, self._because = (
                previous.switch_on, previous._failing, previous._because)
        else:
            self._restore_switch()
        hass = self.hass
        if self.light_sensor:
            self._unsubscribe.append(async_track_state_change_event(
                hass, [self.light_sensor], self._sensor_changed))
        if self.motion_sensor:
            self._seed_presence()
            self._unsubscribe.append(async_track_state_change_event(
                hass, [self.motion_sensor], self._motion_changed))
        if self.lights:
            self._unsubscribe.append(async_track_state_change_event(hass, self.lights, self._light_changed))
            self._unsubscribe.append(async_track_time_interval(hass, self._tick, TICK))
        watched = self.when.watched() + [o.auto_entity for o in self.overrides if o.auto_entity]
        if watched:
            self._unsubscribe.append(async_track_state_change_event(
                hass, sorted(set(watched)), self._context_changed))
        self._started = dt_util.now()
        # The first reading is whatever the sensor says now: in a dark room
        # nought stays nought, and the sensor may not speak for hours.
        self._run(self._current_lux())

    async def async_stop(self, save: bool = True) -> None:
        for remove in self._unsubscribe:
            remove()
        self._unsubscribe.clear()
        for task in list(self._tasks):
            task.cancel()
        self._tasks.clear()
        # The settings may be changing; whoever still misses it says so again.
        self._report_missing(False)
        if save:
            await self._save()

    @callback
    def _spawn(self, coro: Coroutine[Any, Any, None], name: str) -> None:
        """Run a coroutine tied to this room: cancelled when the room stops,
        and with the config entry when it unloads."""
        task = self.hub.entry.async_create_background_task(self.hass, coro, name)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _restore_switch(self) -> None:
        """The switch as it was before a restart, read before the first run.

        Only used when the household runs the switch itself; otherwise the
        conditions decide on the first run.
        """
        if not self.when.manual:
            return
        entity_id = er.async_get(self.hass).async_get_entity_id("switch", DOMAIN, f"{self.subentry_id}_calm")
        if not entity_id:
            return
        try:
            stored = _stored_state(async_get_restore_data(self.hass), entity_id)
        except Exception:
            _LOGGER.warning("%s: could not read the Calm switch's state from before the restart", self.name,
                            exc_info=True)
            return
        if stored is not None:
            self.switch_on = stored.state.state == STATE_ON

    # -- the switch -----------------------------------------------------

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
            # The conditions changed, so the switch follows them, whatever a
            # person set it to in between. A different condition failing is a
            # change too.
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
        """A condition entity that no longer exists goes under Repairs."""
        entity = self.when.entity
        if not entity:
            return
        issue_id = f"condition_missing_{self.subentry_id}"
        if missing:
            ir.async_create_issue(
                self.hass, DOMAIN, issue_id, is_fixable=False, is_persistent=False,
                severity=ir.IssueSeverity.WARNING, translation_key="condition_missing",
                translation_placeholders={"entity": entity, "room": self.name},
            )
        else:
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)

    @callback
    def _call_result(self, error: str | None) -> None:
        """Count failed lamp commands; a run of them goes under Repairs."""
        issue_id = f"commands_failing_{self.subentry_id}"
        if error is None:
            if self._failed_calls >= FAILED_CALLS_BEFORE_ISSUE:
                ir.async_delete_issue(self.hass, DOMAIN, issue_id)
            self._failed_calls = 0
            return
        self._failed_calls += 1
        if self._failed_calls == 1:
            _LOGGER.warning("%s: lamp command failed: %s", self.name, error)
        if self._failed_calls == FAILED_CALLS_BEFORE_ISSUE:
            ir.async_create_issue(
                self.hass, DOMAIN, issue_id, is_fixable=False, is_persistent=False,
                severity=ir.IssueSeverity.WARNING, translation_key="commands_failing",
                translation_placeholders={"room": self.name, "error": error},
            )

    # -- what people press ----------------------------------------------

    @callback
    def set_override(self, key: str, on: bool) -> None:
        if self.controller.set_override(key, on):
            self._run(None)

    @callback
    def set_threshold(self, lux: float) -> None:
        self.controller.set_threshold(lux)
        self._remember(CONF_THRESHOLD, float(lux))

    @callback
    def set_brightness(self, percent: float) -> None:
        self.controller.set_brightness(percent / 100.0)
        self._remember(CONF_BRIGHTNESS, float(percent))

    @callback
    def set_evening_brightness(self, percent: float) -> None:
        self.controller.set_evening_brightness(percent / 100.0)
        self._remember(CONF_EVENING_BRIGHTNESS, float(percent))

    def _remember(self, key: str, value: float) -> None:
        self.data[key] = value
        self.hub.remember_live(self.subentry_id, key, value)
        self._run(None)

    @callback
    def release_manual(self) -> None:
        self.controller.release_manual()
        self._run(None)

    # -- what the house tells us ----------------------------------------

    def _current_lux(self) -> float | None:
        if not self.light_sensor:
            return None
        return _number(self.hass.states.get(self.light_sensor))

    @callback
    def _sensor_changed(self, event: Event[EventStateChangedData]) -> None:
        lux = _number(event.data["new_state"])
        if lux is not None:
            self._run(lux)

    def _seed_presence(self) -> None:
        assert self.motion_sensor is not None
        state = self.hass.states.get(self.motion_sensor)
        if state is None or state.state in UNAVAILABLE:
            return
        # Since it last changed, not since now: a room clear for an hour is
        # known to have been clear for an hour.
        self.controller.motion(state.last_changed, state.state == STATE_ON)

    @callback
    def _motion_changed(self, event: Event[EventStateChangedData]) -> None:
        new = event.data["new_state"]
        if new is None or new.state in UNAVAILABLE:
            return
        self.controller.motion(new.last_changed, new.state == STATE_ON)
        # At once: the light should be there before they have crossed the room.
        self._run(None)

    @callback
    def _context_changed(self, event: Event[EventStateChangedData]) -> None:
        self._run(None)

    @callback
    def _light_changed(self, event: Event[EventStateChangedData]) -> None:
        """Tell Calm's own doing from somebody else's hand on the dimmer."""
        new, old = event.data["new_state"], event.data["old_state"]
        if not self._seeded and new is not None and new.state not in UNAVAILABLE:
            # The lamps' integration has loaded: the held first reading can count.
            self._run(None)
        entity_id = event.data["entity_id"]
        change = self.lamps.classify(
            dt_util.now(), entity_id, old, new, event.context.id, self._wanted_on(entity_id))
        if change is Change.TURNED_OFF:
            _LOGGER.info("%s: %s switched off by something other than Calm", self.name, entity_id)
            self.controller.notify_external_off()
            self.hub.notify(self.subentry_id)
        elif change is Change.TAKEN_OVER:
            asked = (self.controller.last_command.brightness or {}).get(entity_id) \
                if self.controller.last_command else None
            _LOGGER.info("%s: taken over: %s went to brightness %s (Calm asked %s)",
                         self.name, entity_id, new.attributes.get("brightness") if new else None, asked)
            self.controller.notify_manual_change()
            self.hub.notify(self.subentry_id)

    def _wanted_on(self, entity_id: str) -> bool:
        """Whether Calm has this lamp on right now."""
        command = self.controller.last_command
        return bool(
            self.controller.lit and command is not None
            and (command.brightness or {}).get(entity_id, 0) > 0
        )

    # -- the loop -------------------------------------------------------

    @callback
    def _tick(self, _now: datetime | None = None) -> None:
        self._run(None)

    def _elapsed(self, now: datetime) -> float:
        """Real seconds since the last run, not the number of runs.

        A clock that jumped backwards counts as no time at all.
        """
        last, self._last_run = self._last_run, now
        if last is None:
            return TICK.total_seconds()
        return max((now - last).total_seconds(), 0.0)

    @callback
    def _run(self, measured_lux: float | None) -> None:
        now = dt_util.now()
        if not self._seeded:
            measured_lux = self._seed(now, measured_lux)
        dt_seconds = self._elapsed(now)
        self._follow_conditions(now)
        self.controller.set_auto_overrides({
            o.key for o in self.overrides if o.wants_on(now, self._state_of(o.auto_entity))
        })
        controller = self.controller
        command = controller.step(now, measured_lux, dt_seconds)
        sent = self.lamps.apply(now, command, controller.lit, controller.manual, controller.config.interval_s)
        self.commands_sent += sent
        if self.journal is not None:
            motion = controller.presence.detecting if self.motion_sensor else None
            occupied = controller.occupied if self.motion_sensor else None
            self._spawn(self.journal.async_write(now, measured_lux, command, sent, controller.lit, motion, occupied),
                        f"calm {self.name} journal")
        if self._last_save is None or now - self._last_save >= SAVE_EVERY:
            self._last_save = now
            self._spawn(self._save(), f"calm {self.name} save")
        self.hub.notify(self.subentry_id)

    def _state_of(self, entity_id: str | None) -> str | None:
        if not entity_id:
            return None
        state = self.hass.states.get(entity_id)
        return state.state if state is not None else None

    # -- starting -------------------------------------------------------

    def _seed(self, now: datetime, measured_lux: float | None) -> float | None:
        """Hold the first reading until the lamps and conditions are known.

        Lamps already burning are Calm's own light, not daylight, and they
        are taken over as they are. The reading is held until a lamp has
        reported and no condition is still loading, or STARTUP_WAIT has gone
        by. Lamps are only taken over when Calm may run the room.
        """
        if measured_lux is not None:
            self._held = measured_lux
        if self._held is None:
            return None
        started = self._started or now
        settled = self.lamps.known() and not self._conditions_unsettled(now)
        if not settled and now - started < STARTUP_WAIT:
            return None
        self._seeded = True
        self._follow_conditions(now)
        if self.controller.enabled and (burning := self.lamps.burning(self.controller.profile)) is not None:
            self.controller.assume_lit(burning.master, burning.mired)
            _LOGGER.debug("%s: took over burning lamps at %.0f%%, %s", self.name, burning.master * 100,
                          f"{1_000_000.0 / burning.mired:.0f} K" if burning.mired else "colour unknown")
        held, self._held = self._held, None
        return held

    def _conditions_unsettled(self, now: datetime) -> bool:
        """Whether a condition entity is still loading with Home Assistant.

        At a start, an entity that has not loaded yet looks the same as one
        that no longer exists.
        """
        if self.when.manual:
            return False
        failing, _ = self.when.check(self.hass, now, self.language)
        return failing in ("entity_unavailable", "entity_missing")

    # -- explaining -----------------------------------------------------

    def explanation(self) -> Explanation:
        return self.controller.explanation(self.language)

    # -- memory ---------------------------------------------------------

    async def _save(self) -> None:
        await self.store.async_save({
            "mapping": self.controller.mapping.snapshot(),
            "estimate": self.controller.estimator.snapshot(),
            "contributions": {lamp.id: lamp.full_contribution for lamp in self.controller.base_profile.lamps},
        })

    def _restore(self, stored: dict[str, Any]) -> None:
        if stored.get("mapping"):
            self.controller.mapping.restore(stored["mapping"])
        for lamp in self.controller.base_profile.lamps:
            value = (stored.get("contributions") or {}).get(lamp.id)
            if value:
                lamp.full_contribution = float(value)
        # After the lamps, so the scale lands on the profile it was learned against.
        if stored.get("estimate"):
            self.controller.estimator.restore(stored["estimate"])


def store_key(entry_id: str, subentry_id: str) -> str:
    return f"{DOMAIN}.{entry_id}.{subentry_id}"


def _number(state: Any) -> float | None:
    if state is None or state.state in UNAVAILABLE:
        return None
    try:
        return float(state.state)
    except (TypeError, ValueError):
        return None


def _stored_state(data: Any, entity_id: str) -> Any:
    """An entity's state from before the restart, across Home Assistant versions.

    2026.11 replaced the ``last_states`` dictionary with ``async_get_stored_state``.
    """
    if hasattr(data, "async_get_stored_state"):
        return data.async_get_stored_state(entity_id)
    return data.last_states.get(entity_id)

