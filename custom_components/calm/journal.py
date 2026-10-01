"""Writing each room's own record to disk, one file per day.

Home Assistant's recorder keeps the sensor reading without the reasoning
that went with it, and may be a database nobody can open. So a room keeps
its own record next to the configuration: one line per sensor reading, one
whenever somebody walks in or leaves, a heartbeat when nothing happens, and
a fortnight kept. Every fix in September was found in these files.
"""

from __future__ import annotations

import csv
import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from homeassistant.core import HomeAssistant

from .const import JOURNAL_DIR
from .core import journal_columns, journal_row, slugify

_LOGGER = logging.getLogger(__name__)

#: Long enough for "it has been odd since the weekend", short enough that
#: nobody has to think about the disk.
RETENTION_DAYS = 14

#: A line even without a reading after this long. A sensor gone quiet is a
#: thing worth seeing afterwards.
HEARTBEAT = timedelta(minutes=5)


class Journal:
    """The day file for one room."""

    def __init__(self, hass: HomeAssistant, room: str) -> None:
        self.hass = hass
        self.directory = Path(hass.config.path(JOURNAL_DIR)) / (slugify(room) or "room")
        self._last_written: Optional[datetime] = None
        self._last_presence: tuple = (None, None)
        self._last_reason: Optional[str] = None
        self._pruned_on: Optional[date] = None

    def should_write(
        self, when: datetime, measured_lux: Optional[float],
        motion: Optional[bool], occupied: Optional[bool], reason: Optional[str],
    ) -> bool:
        if measured_lux is not None:
            return True
        if (motion, occupied) != self._last_presence:
            # Somebody walked in or the room gave up on them: exactly the lines
            # anybody reading the file back is looking for.
            return True
        if reason != self._last_reason:
            return True
        return self._last_written is None or when - self._last_written >= HEARTBEAT

    async def async_write(
        self, when: datetime, measured_lux: Optional[float], command, commands_sent: int,
        lit: bool, motion: Optional[bool], occupied: Optional[bool],
    ) -> None:
        reason = getattr(command, "reason", None)
        if not self.should_write(when, measured_lux, motion, occupied, reason):
            return
        self._last_presence = (motion, occupied)
        self._last_reason = reason
        # Marked before the job runs: two ticks can be in flight at once.
        self._last_written = when
        row = journal_row(when, measured_lux, command, commands_sent, lit, motion, occupied)
        prune = self._pruned_on != when.date()
        self._pruned_on = when.date()
        try:
            await self.hass.async_add_executor_job(self._append, when, row, prune)
        except OSError as error:
            _LOGGER.warning("Calm: dagverslag niet geschreven: %s", error)

    def _append(self, when: datetime, row: list, prune: bool) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / ("%s.csv" % when.date().isoformat())
        if prune:
            self._set_aside_if_foreign(path)
        with path.open("a", encoding="utf-8", newline="") as handle:
            # Asked of the open file, not of the path: two writes in flight
            # would both find the path missing and both write a header.
            if handle.tell() == 0:
                csv.writer(handle).writerow(journal_columns())
            csv.writer(handle).writerow(row)
        if prune:
            self._prune(when.date())

    def _set_aside_if_foreign(self, path: Path) -> None:
        """A day file written with other columns is moved out of the way.

        The day Calm 2 replaced Calm 1, the same file would otherwise hold
        two kinds of line under one header, and anybody reading it back would
        be reading half of it under the wrong column names.
        """
        if not path.exists():
            return
        with path.open(encoding="utf-8", newline="") as handle:
            header = next(csv.reader(handle), None)
        if header is not None and header != journal_columns():
            path.rename(path.with_name(f"{path.stem}-eerder.csv"))

    def _prune(self, today: date) -> None:
        oldest = today - timedelta(days=RETENTION_DAYS)
        for path in self.directory.glob("*.csv"):
            try:
                when = date.fromisoformat(path.stem[:10])
            except ValueError:
                continue  # not one of ours
            if when < oldest:
                path.unlink(missing_ok=True)
