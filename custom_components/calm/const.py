"""Names shared across the integration. Nothing here does anything."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "calm"

#: The kind of subentry every room is. There is only one, but Home Assistant
#: asks for a name.
SUBENTRY_ROOM: Final = "room"

# -- global settings, on the main entry ----------------------------------

CONF_WHEN: Final = "when"
CONF_RHYTHM: Final = "rhythm"

# "Wanneer mag Calm de lampen aansturen?"
CONF_HOME: Final = "home"
CONF_ENTITY: Final = "entity"
CONF_ENTITY_STATE: Final = "entity_state"
CONF_WINDOW: Final = "window"
CONF_START: Final = "start"
CONF_END: Final = "end"
CONF_MANUAL: Final = "manual"

# The day's rhythm.
CONF_WAKE: Final = "wake"
CONF_BED: Final = "bed"
CONF_WARM_KELVIN: Final = "warm_kelvin"
CONF_COOL_KELVIN: Final = "cool_kelvin"

# -- a room, on its subentry ---------------------------------------------

CONF_AREA: Final = "area_id"
CONF_NAME: Final = "name"
#: Lamps of a room put together by hand. An area room works these out live.
CONF_LIGHTS: Final = "lights"
#: Lamps of the area that sit the ordinary day out: the worktop light that
#: is only for work.
CONF_EXCLUDED: Final = "excluded_lights"
#: Chosen by hand. Left empty, a room uses what its area has.
CONF_LIGHT_SENSOR: Final = "light_sensor"
CONF_MOTION_SENSOR: Final = "motion_sensor"

CONF_THRESHOLD: Final = "threshold_lux"
CONF_BRIGHTNESS: Final = "brightness"
CONF_EVENING_BRIGHTNESS: Final = "evening_brightness"
CONF_USE_MOTION: Final = "use_motion"
CONF_MOTION_HOLD: Final = "motion_hold_minutes"
CONF_WHEN_EMPTY: Final = "when_empty"
CONF_STANDBY_LEVEL: Final = "standby_level"
CONF_STANDBY_KELVIN: Final = "standby_kelvin"
CONF_MOTION_ALWAYS: Final = "motion_always"
CONF_NIGHTLIGHT_LEVEL: Final = "nightlight_level"
CONF_NIGHTLIGHT_KELVIN: Final = "nightlight_kelvin"
CONF_MANUAL_HOLD: Final = "manual_hold_minutes"
CONF_MIN_LEVEL: Final = "min_level"
CONF_MAX_LEVEL: Final = "max_level"
CONF_WEIGHTS: Final = "weights"
CONF_OVERRIDES: Final = "overrides"
#: Whether this room follows the global "when" and rhythm, or has its own.
CONF_OWN_WHEN: Final = "own_when"
CONF_OWN_RHYTHM: Final = "own_rhythm"

# -- defaults ------------------------------------------------------------

#: Dusk at a sensor on a wall indoors: where a real kitchen and a real living
#: room ended up after a fortnight of somebody adjusting it.
DEFAULT_THRESHOLD: Final = 25.0
DEFAULT_BRIGHTNESS: Final = 100
DEFAULT_EVENING_BRIGHTNESS: Final = 60
DEFAULT_MOTION_HOLD: Final = 10
DEFAULT_STANDBY_LEVEL: Final = 15
DEFAULT_STANDBY_KELVIN: Final = 2400
DEFAULT_NIGHTLIGHT_LEVEL: Final = 5
DEFAULT_NIGHTLIGHT_KELVIN: Final = 2000
#: Two hours, as the household chose.
DEFAULT_MANUAL_HOLD: Final = 120
DEFAULT_MIN_LEVEL: Final = 4
DEFAULT_MAX_LEVEL: Final = 100
DEFAULT_WAKE: Final = "07:00:00"
DEFAULT_BED: Final = "23:00:00"
DEFAULT_WARM_KELVIN: Final = 2200
DEFAULT_COOL_KELVIN: Final = 5000

#: How often each room recomputes. Commands go out far less often: only when
#: something has changed enough to be worth a command.
TICK: Final = timedelta(seconds=30)

#: Where each room keeps its own day file, under the configuration folder.
JOURNAL_DIR: Final = "calm"

#: Where the card lives, as the browser asks for it.
CARD_URL: Final = "/calm_static/calm-card.js"
