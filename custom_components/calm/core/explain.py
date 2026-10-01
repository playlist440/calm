"""Saying why the light is the way it is, in one sentence.

The most common complaint about home automation is not that it works badly.
It is that people feel shut out of it: something happens and there is no way
to find out why. Calm already knows why, because every decision comes from
one rung of a short ladder. This turns the rung into a sentence.

Plain words, no lux. The numbers are in the details for whoever wants them;
the sentence is for somebody standing in the kitchen.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from enum import Enum
from typing import Any, Dict, Mapping, Optional


class Why(Enum):
    NO_READING = "no_reading"
    MANUAL = "manual"
    SWITCHED_OFF = "switched_off"
    OVERRIDE = "override"
    OVERRIDE_AUTO = "override_auto"
    DAYLIGHT = "daylight"
    RETURNING = "returning"
    STANDBY = "standby"
    EMPTY = "empty"
    INACTIVE = "inactive"
    NIGHTLIGHT = "nightlight"
    MOTION_WHILE_OFF = "motion_while_off"
    MORNING = "morning"
    DAYTIME = "daytime"
    EVENING = "evening"
    NIGHT = "night"


@dataclass(frozen=True)
class Explanation:
    why: Why
    sentence: str
    detail: Dict[str, Any]


_WORDS = {
    "nl": {
        Why.NO_READING: "Calm wacht op de eerste meting van de lichtsensor.",
        Why.MANUAL: "Met de hand ingesteld. Calm neemt het om {resume} weer over.",
        Why.SWITCHED_OFF: "Met de hand uitgezet. Calm laat ze uit tot de ruimte opnieuw begint.",
        Why.OVERRIDE: "{mode} staat aan.",
        Why.OVERRIDE_AUTO: "{mode} ging vanzelf aan.",
        Why.DAYLIGHT: "Uit: er is genoeg daglicht.",
        Why.RETURNING: "Nog even uit: het daglicht is net weggevallen, en Calm wacht {wait} om zeker te weten dat het geen wolk is.",
        Why.STANDBY: "Standby: er is al een tijd niemand gezien. Bij de eerste beweging staat de ruimte er weer.",
        Why.EMPTY: "Uit: er is al een tijd niemand gezien. Bij de eerste beweging gaan ze aan.",
        Why.INACTIVE: "Uit: {because}.",
        Why.NIGHTLIGHT: "Nachtlampje: er is beweging terwijl Calm deze ruimte niet regelt.",
        Why.MOTION_WHILE_OFF: "Licht door beweging, terwijl Calm deze ruimte niet regelt.",
        Why.MORNING: "De dag begint: het licht loopt op van warm naar helder.",
        Why.DAYTIME: "De lampen vullen het daglicht aan.",
        Why.EVENING: "Warmer en zachter licht: het is {clock}, en om {bed} ga je naar bed.",
        Why.NIGHT: "Nacht: zo weinig licht als kan, voor je slaap.",
    },
    "en": {
        Why.NO_READING: "Calm is waiting for the first reading from the light sensor.",
        Why.MANUAL: "Set by hand. Calm takes over again at {resume}.",
        Why.SWITCHED_OFF: "Switched off by hand. Calm leaves them off until the room starts again.",
        Why.OVERRIDE: "{mode} is on.",
        Why.OVERRIDE_AUTO: "{mode} switched on by itself.",
        Why.DAYLIGHT: "Off: there is enough daylight.",
        Why.RETURNING: "Off a little longer: the daylight has just gone, and Calm is waiting {wait} to be sure it is not a cloud.",
        Why.STANDBY: "Standby: nobody has been seen for a while. The first movement brings the room back.",
        Why.EMPTY: "Off: nobody has been seen for a while. The first movement brings them on.",
        Why.INACTIVE: "Off: {because}.",
        Why.NIGHTLIGHT: "Nightlight: there is movement while Calm is not running this room.",
        Why.MOTION_WHILE_OFF: "Light from movement, while Calm is not running this room.",
        Why.MORNING: "The day is starting: the light rises from warm to bright.",
        Why.DAYTIME: "The lamps are topping up the daylight.",
        Why.EVENING: "Warmer and softer light: it is {clock}, and bedtime is at {bed}.",
        Why.NIGHT: "Night: as little light as will do, for your sleep.",
    },
}

#: Said after the sentence when the lamps could come on at once but have not
#: yet: the reassurance that walking in is enough.
_WALK_IN = {
    "nl": " Loop je binnen, dan gaan ze meteen aan.",
    "en": " Walk in and they come on at once.",
}

#: Said after the sentence while the light sensor is not to be believed.
_STALE = {
    "nl": " De lichtsensor zegt al een tijd niets, dus Calm gaat uit van donker.",
    "en": " The light sensor has been quiet for a while, so Calm assumes it is dark.",
}

_BECAUSE = {
    "nl": "Calm regelt deze ruimte nu niet",
    "en": "Calm is not running this room right now",
}

_SPANS = {
    "nl": {"minute": "minuut", "minutes": "minuten", "moment": "nog even", "another": "nog"},
    "en": {"minute": "minute", "minutes": "minutes", "moment": "a moment longer", "another": "another"},
}


def explain(
    *,
    now: datetime,
    diagnostics: Mapping[str, Any],
    lit: bool,
    kelvin: float,
    bed: time,
    morning: bool,
    night: bool,
    has_motion_sensor: bool = False,
    standby_level: float = 0.0,
    language: str = "nl",
) -> Explanation:
    """One sentence that answers "why is it like this", from the last decision."""
    lang = language if language in _WORDS else "nl"
    words = _WORDS[lang]
    reason = str(diagnostics.get("reason") or "")
    rung = diagnostics.get("rung")
    scene = diagnostics.get("scene")

    why = _classify(reason, rung, scene, lit, morning, night, now, bed)

    detail = {
        key: diagnostics.get(key)
        for key in (
            "reason", "rung", "scene", "mode", "daylight_lux", "threshold_lux",
            "target_lux", "confidence", "unreachable", "stale", "occupied",
            "returning_in_s", "manual_remaining_s", "inactive_because",
        )
        if key in diagnostics
    }
    detail["kelvin"] = round(kelvin)

    remaining = diagnostics.get("manual_remaining_s")
    resume = (now + timedelta(seconds=float(remaining))).strftime("%H:%M") if remaining else "straks"
    sentence = words[why].format(
        mode=diagnostics.get("mode") or "",
        wait=_countdown(diagnostics.get("returning_in_s"), lang),
        because=diagnostics.get("inactive_because") or _BECAUSE[lang],
        clock=now.strftime("%H:%M"),
        bed=bed.strftime("%H:%M"),
        resume=resume,
    )
    if why is Why.RETURNING and has_motion_sensor:
        sentence += _WALK_IN[lang]
    if diagnostics.get("stale") and lit:
        sentence += _STALE[lang]
    return Explanation(why=why, sentence=sentence, detail=detail)


def _classify(reason, rung, scene, lit, morning, night, now, bed) -> Why:
    if reason == "no_reading_yet":
        return Why.NO_READING
    if reason == "switched_off_by_hand":
        return Why.SWITCHED_OFF
    if rung == "manual":
        return Why.MANUAL
    if scene == "override" and lit:
        return Why.OVERRIDE_AUTO if reason == "override_auto" else Why.OVERRIDE
    if reason == "waiting_to_return":
        return Why.RETURNING
    if rung == "daylight" and not lit:
        return Why.DAYLIGHT
    if scene == "standby" and lit:
        return Why.STANDBY
    if reason == "nobody_here":
        return Why.EMPTY
    if scene == "nightlight" and lit:
        return Why.NIGHTLIGHT
    if reason == "motion_while_off" and lit:
        return Why.MOTION_WHILE_OFF
    if not lit:
        return Why.INACTIVE if rung == "inactive" else Why.DAYLIGHT
    if morning:
        return Why.MORNING
    if night:
        return Why.NIGHT
    if _hours_until(now, bed) <= 3.0:
        return Why.EVENING
    return Why.DAYTIME


def _countdown(seconds: Optional[float], language: str) -> str:
    """How much longer, rounded up so a wait still running never says zero."""
    words = _SPANS[language]
    if seconds is None or seconds < 60:
        return words["moment"]
    minutes = int(seconds // 60) + (1 if seconds % 60 else 0)
    unit = words["minute"] if minutes == 1 else words["minutes"]
    return "%s %d %s" % (words["another"], minutes, unit)


def _hours_until(now: datetime, bed: time) -> float:
    now_h = now.hour + now.minute / 60.0
    bed_h = bed.hour + bed.minute / 60.0
    delta = bed_h - now_h
    return delta + 24.0 if delta < 0 else delta
