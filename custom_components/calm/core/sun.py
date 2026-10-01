"""Where the sun is, without pulling in a dependency for it.

A NOAA-style solar position calculation, accurate to well under a degree
for our purposes. We only ever need the elevation: it drives the colour
curve and the daylight prediction, and it is what makes the controller able
to start moving before the room actually gets darker.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone


def solar_elevation(when: datetime, latitude: float, longitude: float) -> float:
    """Solar elevation in degrees above the horizon; negative after sunset.

    ``when`` may be naive (treated as UTC) or timezone-aware.
    """
    moment = when.astimezone(timezone.utc) if when.tzinfo else when.replace(tzinfo=timezone.utc)

    julian_day = _julian_day(moment)
    t = (julian_day - 2451545.0) / 36525.0

    mean_long = (280.46646 + t * (36000.76983 + t * 0.0003032)) % 360.0
    mean_anom = 357.52911 + t * (35999.05029 - 0.0001537 * t)
    eccentricity = 0.016708634 - t * (0.000042037 + 0.0000001267 * t)

    anom_rad = math.radians(mean_anom)
    center = (
        math.sin(anom_rad) * (1.914602 - t * (0.004817 + 0.000014 * t))
        + math.sin(2 * anom_rad) * (0.019993 - 0.000101 * t)
        + math.sin(3 * anom_rad) * 0.000289
    )
    true_long = mean_long + center
    omega = 125.04 - 1934.136 * t
    apparent_long = true_long - 0.00569 - 0.00478 * math.sin(math.radians(omega))

    mean_obliquity = (
        23.0
        + (26.0 + ((21.448 - t * (46.815 + t * (0.00059 - t * 0.001813)))) / 60.0) / 60.0
    )
    obliquity = mean_obliquity + 0.00256 * math.cos(math.radians(omega))

    declination = math.degrees(
        math.asin(
            math.sin(math.radians(obliquity)) * math.sin(math.radians(apparent_long))
        )
    )

    var_y = math.tan(math.radians(obliquity / 2.0)) ** 2
    mean_long_rad = math.radians(mean_long)
    equation_of_time = 4.0 * math.degrees(
        var_y * math.sin(2 * mean_long_rad)
        - 2.0 * eccentricity * math.sin(anom_rad)
        + 4.0 * eccentricity * var_y * math.sin(anom_rad) * math.cos(2 * mean_long_rad)
        - 0.5 * var_y * var_y * math.sin(4 * mean_long_rad)
        - 1.25 * eccentricity * eccentricity * math.sin(2 * anom_rad)
    )

    minutes_utc = moment.hour * 60.0 + moment.minute + moment.second / 60.0
    true_solar_minutes = (minutes_utc + equation_of_time + 4.0 * longitude) % 1440.0
    hour_angle = true_solar_minutes / 4.0 - 180.0

    lat_rad = math.radians(latitude)
    dec_rad = math.radians(declination)
    zenith = math.degrees(
        math.acos(
            max(
                -1.0,
                min(
                    1.0,
                    math.sin(lat_rad) * math.sin(dec_rad)
                    + math.cos(lat_rad) * math.cos(dec_rad) * math.cos(math.radians(hour_angle)),
                ),
            )
        )
    )
    return 90.0 - zenith


def _julian_day(moment: datetime) -> float:
    year = moment.year
    month = moment.month
    day = (
        moment.day
        + (moment.hour + (moment.minute + moment.second / 60.0) / 60.0) / 24.0
    )
    if month <= 2:
        year -= 1
        month += 12
    a = year // 100
    b = 2 - a + a // 4
    return (
        math.floor(365.25 * (year + 4716))
        + math.floor(30.6001 * (month + 1))
        + day
        + b
        - 1524.5
    )
