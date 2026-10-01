"""Calm's control core, with no Home Assistant anywhere in it.

Everything that can be reasoned about, simulated and tested lives here: the
photometry, the day's rhythm, the sensor filter, the estimator, the rate
limits and the order in which a room decides what to do. The integration
around it feeds it readings and turns what it decides into service calls.

The split is what makes the promises checkable. A year of weather runs
through this in seconds, and a test can say that the lamps never moved
faster than a person notices, or that a person walking into a dark room is
never kept waiting, without a Home Assistant in sight.
"""

from .attribution import ChangeAttributor, Expectation
from .controller import (
    Action,
    Command,
    ControllerConfig,
    EmptyAction,
    RoomController,
    RoomSettings,
    Rung,
    Scene,
)
from .daycurve import DayCurve, DayCurveConfig, Targets
from .estimator import ContributionEstimator, EstimatorConfig
from .explain import Explanation, Why, explain
from .journal import COLUMNS as JOURNAL_COLUMNS, row as journal_row
from .labels import label_lamps
from .limiter import SlewLimiter
from .mapping import DaylightResponse
from .naming import slugify, unique_key
from .output import CommandPacer, Emission, LampGroup
from .overrides import (
    TEMPLATES,
    AutoMode,
    ColourMode,
    LampRole,
    LevelMode,
    Override,
    OverrideSet,
    Source,
    from_template,
)
from .photometry import (
    device_to_perceptual,
    kelvin_to_mired,
    luminous_to_perceptual,
    mired_to_kelvin,
    perceptual_to_device,
    perceptual_to_luminous,
)
from .presence import DEFAULT_HOLD_S, PresenceHold
from .profile import DimProfile, Lamp
from .sensorfilter import FilterReading, SensorFilter
from .sensorprofile import SensorProfile, SensorWatcher, Speed, suggested_filter_tau
from .sun import solar_elevation


def journal_columns():
    """The header of a room's own day file."""
    return list(JOURNAL_COLUMNS)


__all__ = [
    "Action",
    "AutoMode",
    "ColourMode",
    "Command",
    "ControllerConfig",
    "EmptyAction",
    "Explanation",
    "LampRole",
    "LevelMode",
    "Override",
    "OverrideSet",
    "RoomController",
    "RoomSettings",
    "Rung",
    "Scene",
    "Source",
    "TEMPLATES",
    "Why",
    "explain",
    "from_template",
    "ChangeAttributor",
    "CommandPacer",
    "ContributionEstimator",
    "DEFAULT_HOLD_S",
    "DayCurve",
    "DayCurveConfig",
    "DaylightResponse",
    "DimProfile",
    "Emission",
    "EstimatorConfig",
    "Expectation",
    "FilterReading",
    "JOURNAL_COLUMNS",
    "Lamp",
    "LampGroup",
    "PresenceHold",
    "SensorFilter",
    "SensorProfile",
    "SensorWatcher",
    "SlewLimiter",
    "Speed",
    "Targets",
    "device_to_perceptual",
    "journal_columns",
    "journal_row",
    "kelvin_to_mired",
    "label_lamps",
    "luminous_to_perceptual",
    "mired_to_kelvin",
    "perceptual_to_device",
    "perceptual_to_luminous",
    "slugify",
    "solar_elevation",
    "suggested_filter_tau",
    "unique_key",
]
