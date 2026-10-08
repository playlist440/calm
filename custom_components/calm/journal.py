"""Each room's optional day file: one CSV per day, two weeks kept.

Off unless the household turns it on (Calm → Configure → Day file). It
records every reading, every arrival and departure and the reason behind
each decision, which Home Assistant's recorder keeps no trace of. Because
it records when rooms are occupied, it is opt-in.
"""

from __future__ import annotations

import csv
import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant

from .const import JOURNAL_DIR
from .core import Command, journal_columns, journal_row

_LOGGER = logging.getLogger(__name__)

#: Long enough for "it has been odd since the weekend", short enough that
#: nobody has to think about the disk.
RETENTION_DAYS = 14

#: A line even without a reading after this long. A sensor gone quiet is a
#: thing worth seeing afterwards.
HEARTBEAT = timedelta(minutes=5)


class Journal:
    """The day file for one room."""

    def __init__(self, hass: HomeAssistant, folder: str) -> None:
        self.hass = hass
        self.directory = journal_directory(hass, folder)
        self._last_written: datetime | None = None
        self._last_presence: tuple[bool | None, bool | None] = (None, None)
        self._last_reason: str | None = None
        self._pruned_on: date | None = None

    def should_write(
        self, when: datetime, measured_lux: float | None,
        motion: bool | None, occupied: bool | None, reason: str | None,
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
        self, when: datetime, measured_lux: float | None, command: Command, commands_sent: int,
        lit: bool, motion: bool | None, occupied: bool | None,
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
            _LOGGER.warning("Calm: day file not written: %s", error)

    def _append(self, when: datetime, row: list[Any], prune: bool) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / (f"{when.date().isoformat()}.csv")
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
            path.rename(path.with_name(f"{path.stem}-previous.csv"))

    def _prune(self, today: date) -> None:
        oldest = today - timedelta(days=RETENTION_DAYS)
        for path in self.directory.glob("*.csv"):
            try:
                when = date.fromisoformat(path.stem[:10])
            except ValueError:
                continue  # not one of ours
            if when < oldest:
                path.unlink(missing_ok=True)


def journal_directory(hass: HomeAssistant, folder: str) -> Path:
    """Where a room's day files live: one folder per room, by its id."""
    return Path(hass.config.path(JOURNAL_DIR)) / folder

