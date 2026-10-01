"""One line per reading: what the room saw, and what it did about it.

Written because "why did it do that this afternoon" otherwise depends on
Home Assistant's recorder, which keeps the sensor reading and none of the
reasoning that went with it. A room that cannot explain its own day is a room
nobody can improve, and every fix in September was found in these files.

The line carries the rung of the ladder that decided, because that is the
first thing anybody reading it back wants to know.

The columns are not translated. It is a data file: opened in a spreadsheet,
pasted into an issue, compared against last week's.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, List, Optional

COLUMNS = (
    "time",
    #: What the sensor actually said, before any smoothing. Empty on a line
    #: written without a fresh reading, which is itself worth seeing.
    "measured_lux",
    #: What the room took the daylight to be, and the line it compares that
    #: with. The two numbers the slider shows.
    "daylight_lux",
    "threshold_lux",
    #: What the lamps were filling up to.
    "target_lux",
    "master",
    "kelvin",
    "lamp_contribution_lux",
    "confidence",
    "visibility",
    #: Which rung decided, what the lamps were for, and the finer reason.
    "rung",
    "scene",
    "reason",
    "override",
    "lit",
    "unreachable",
    "stale",
    "commands",
    "motion",
    "occupied",
)


def _number(value: Any, decimals: int = 1) -> str:
    if value is None:
        return ""
    try:
        return "%.*f" % (decimals, float(value))
    except (TypeError, ValueError):
        return ""


def _flag(value: Optional[bool]) -> str:
    """A yes, a no, or nothing at all, which is not the same as a no."""
    return "" if value is None else ("1" if value else "0")


def row(
    when: datetime,
    measured_lux: Optional[float],
    command: Any,
    commands_sent: int,
    lit: Optional[bool] = None,
    motion: Optional[bool] = None,
    occupied: Optional[bool] = None,
) -> List[str]:
    """One record, in the order of :data:`COLUMNS`.

    Takes the command rather than the controller, so what is written is
    exactly what was acted on and not whatever state the room has moved on to
    by the time the line is written.
    """
    diagnostics = dict(getattr(command, "diagnostics", {}) or {})
    mired = getattr(command, "mired", None)
    return [
        when.isoformat(timespec="seconds"),
        _number(measured_lux),
        _number(diagnostics.get("daylight_lux")),
        _number(diagnostics.get("threshold_lux")),
        _number(diagnostics.get("target_lux")),
        _number(getattr(command, "master", None), 3),
        _number(1_000_000.0 / mired, 0) if mired else "",
        _number(diagnostics.get("full_contribution")),
        _number(diagnostics.get("confidence"), 3),
        _number(diagnostics.get("visibility"), 3),
        str(diagnostics.get("rung") or ""),
        str(diagnostics.get("scene") or ""),
        str(diagnostics.get("reason") or ""),
        str(diagnostics.get("mode") or ""),
        _flag(lit),
        str(diagnostics.get("unreachable") or ""),
        _flag(diagnostics.get("stale")),
        str(int(commands_sent)),
        _flag(motion),
        _flag(occupied),
    ]
