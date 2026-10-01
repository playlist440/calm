"""Learning what the lamps contribute, so the loop can be closed safely.

This is the heart of it. A controller that reads a light sensor and drives
the lamps that shine on that sensor has a positive feedback path: dim, the
reading drops, so it brightens, so the reading rises, so it dims. The lamps
hunt up and down, which is the single most uncomfortable thing a lighting
system can do.

The fix is not a cleverer controller. It is subtraction. Light adds up:

    measured = daylight + room(master)

Know ``room(master)`` and you can subtract it, leaving an estimate of the
daylight — a quantity the lamps do not affect. The controller then steers by
something outside its own loop, and the feedback path is simply gone.

What has to be learned is one number: how much light this room puts on this
sensor at full. Not one per lamp — the fixed dim profile means the shape of
the room's response is known and only its scale is unknown. That is why the
profile decision made this problem smaller rather than larger.

The estimate comes from *changes*. When the master level moves and the
daylight has had no time to, the change in the reading is almost entirely
the lamps, and that is a direct measurement of the scale.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from .profile import DimProfile


@dataclass
class EstimatorConfig:
    """Tuning for the recursive estimate. Defaults are deliberately cautious."""

    #: How quickly old evidence stops counting. Below 1.0 the estimate keeps
    #: drifting with the room: dust on the lamps, ageing LEDs, a new rug.
    forgetting: float = 0.995
    #: Assumed sensor noise, as a fraction of the reading.
    relative_noise: float = 0.04
    #: How fast daylight is assumed to wander, as a fraction of the current
    #: daylight per second. This is what tells the estimator how much of a
    #: change it may *not* attribute to the lamps, and it has to be relative:
    #: a fixed number of lux per second is far too generous at dusk, when the
    #: daylight is nearly gone and the estimator should be paying attention,
    #: and far too strict at noon. Assuming too much drift is the quiet
    #: failure — every observation gets buried in assumed noise and the
    #: estimate simply stops learning.
    daylight_drift_per_s: float = 0.002
    #: Floor under that, so a pitch-dark room still assumes some uncertainty.
    daylight_drift_floor_lux: float = 8.0
    #: Smallest change in room output worth learning from, relative to full.
    min_signal_fraction: float = 0.03
    #: Least a lamp step must stand out above the daylight drift that could
    #: plausibly have happened alongside it, in standard deviations.
    #:
    #: Deliberately low, after measuring what a higher one does. It was set
    #: to 3 to stop the loop learning from dusk, where the lamps ramp up as
    #: the daylight falls and the two are correlated. It turned out to be
    #: protecting nothing — a normal room converges to within 3% of the truth
    #: at every setting from 0 to 3, because the weighting inside the update
    #: already discounts a weak observation properly.
    #:
    #: What it did do was lock a room out of learning in exactly the case
    #: that matters most. The gate compares the *current* estimate against
    #: the noise, so a room whose lamps barely reach the sensor stops
    #: learning the moment its estimate drops far enough — and stays wrong by
    #: a factor of fourteen, permanently, in the one situation where it most
    #: needs to find out. A gate that closes behind you is worse than none.
    min_snr: float = 0.5
    #: CUSUM threshold for deciding the room itself has changed.
    change_threshold: float = 5.0
    #: Slack in the CUSUM, in units of standard deviation.
    change_slack: float = 0.5
    #: How much uncertainty to add back when a change is detected.
    change_inflation: float = 20.0
    #: Ceiling on the uncertainty. Without it, inflating after a change hands
    #: the next observation an enormous gain, and one noisy sample can throw
    #: the estimate somewhere it will not come back from. Re-learning should
    #: be quick, not violent.
    max_variance: float = 1.0
    #: Most the estimate may move in one step, relative to itself. A second
    #: guard on the same failure: even with a large gain, no single reading
    #: gets to halve or double the model.
    max_relative_step: float = 0.35
    #: Longest gap between two readings that may still be compared. Beyond
    #: this the daylight could have done anything in between, so the pair
    #: carries no information about the lamps — only the illusion of some.
    max_pairing_gap_s: float = 900.0
    #: Relative standard deviation at which the estimate counts as trusted.
    confident_rsd: float = 0.08


@dataclass
class EstimatorState:
    """What the estimator currently believes, for diagnostics and tests."""

    full_contribution: float
    variance: float
    confidence: float
    daylight_lux: float
    observations: int
    changes_detected: int


class ContributionEstimator:
    """Recursive least squares on a single scalar, with change detection."""

    def __init__(
        self,
        profile: DimProfile,
        prior_relative_uncertainty: float = 0.8,
        config: EstimatorConfig = None,
    ) -> None:
        """The profile carries the prior; this class learns how wrong it is.

        Each lamp arrives with a contribution guessed from its model's rated
        output and the size of the room. That guess has the right shape — the
        relative weights are known exactly, because we set them — and the
        wrong scale. So there is exactly one unknown: a single multiplier on
        the whole room. ``prior_relative_uncertainty`` says how far off the
        table is allowed to be, and 0.8 is honest about a guess made from a
        catalogue.
        """
        if profile.full_contribution <= 0:
            raise ValueError(
                "every lamp needs a prior contribution before the estimator can run"
            )
        self.config = config or EstimatorConfig()
        self._profile = profile
        self._scale = 1.0
        # The unknown is a dimensionless multiplier on a known shape, so its
        # variance is dimensionless too. Seeding this in lux² — the units of
        # the thing being predicted rather than of the parameter — is an easy
        # mistake and produces gains tens of thousands of times too large.
        self._variance = min(prior_relative_uncertainty ** 2, self.config.max_variance)

        self._last_shape: Optional[float] = None
        self._last_measured: Optional[float] = None
        self._last_seen: Optional[datetime] = None
        self._daylight = 0.0
        self._cusum_up = 0.0
        self._cusum_down = 0.0
        self.observations = 0
        self.changes_detected = 0
        self._overshooting = 0

    # -- what it believes ----------------------------------------------

    @property
    def full_contribution(self) -> float:
        """Lux this room puts on the sensor with the profile at full."""
        return self._profile.full_contribution * self._scale

    @property
    def daylight_lux(self) -> float:
        """Best estimate of the daylight alone — the loop's actual input."""
        return self._daylight

    @property
    def confidence(self) -> float:
        """0..1. Drives how hard the controller is willing to push.

        Deliberately wired to the *variance* rather than to elapsed time. A
        system that grows confident just because it has been running is a
        system that will still be confident the day after someone moves the
        sensor.
        """
        rsd = math.sqrt(max(self._variance, 0.0)) / max(self._scale, 1e-9)
        return max(0.0, min(1.0, self.config.confident_rsd / max(rsd, 1e-9)))

    @property
    def relative_uncertainty(self) -> float:
        """Standard deviation of the estimate, relative to the estimate.

        The raw number behind ``confidence``, which saturates at 1.0 once the
        model is good enough. Useful when what you want to see is how much
        better or worse it got, not whether it is past the bar.
        """
        return math.sqrt(max(self._variance, 0.0)) / max(self._scale, 1e-9)

    def state(self) -> EstimatorState:
        return EstimatorState(
            full_contribution=self.full_contribution,
            variance=self._variance,
            confidence=self.confidence,
            daylight_lux=self._daylight,
            observations=self.observations,
            changes_detected=self.changes_detected,
        )

    def shape(self, master: float, lights_on: bool) -> float:
        """The room's response at ``master``, before the unknown scale.

        Kept separate from :meth:`room_output` on purpose. The regressor has
        to be scale-free: comparing two predictions made with different
        scales — which is what happens the moment an update lands between
        them — silently mixes the thing being measured into the measurement,
        and the estimate walks away instead of converging.
        """
        if not lights_on:
            return 0.0
        return self._profile.luminous_at(master)

    def room_output(self, master: float, lights_on: bool) -> float:
        """Predicted lux from the lamps at a given master level."""
        return self.shape(master, lights_on) * self._scale

    # -- learning ------------------------------------------------------

    def update(
        self,
        measured_lux: float,
        master: float,
        lights_on: bool,
        dt_seconds: float,
        trustworthy: bool = True,
        smoothed_lux: Optional[float] = None,
        now: Optional[datetime] = None,
    ) -> None:
        """Fold one sample in.

        ``measured_lux`` must be *prompt* — spike-rejected but not smoothed.
        Learning happens across the step when the lamps change, and a value
        that has been averaged over fifteen minutes has not seen that step
        yet: the regressor says the lamps moved a long way while the reading
        says nothing happened, and the model duly concludes the lamps are
        feeble. It will be wrong, and its confidence will be high, which is
        the worst combination available.

        ``smoothed_lux`` is the calm version, used only for the daylight
        figure the controller steers by. Two signals, two jobs.

        ``now`` is the wall clock, and it is not decoration. The interval
        between two readings has to come from the clock rather than from
        whatever the caller passed as ``dt_seconds``: a loop that stops for
        the night and starts again in the morning will happily report thirty
        seconds, and the estimator would then pair last night's reading with
        this evening's and treat the difference as something the lamps did.

        ``trustworthy`` is how the caller says the sensor is currently
        describing something other than the room — direct sun on it, most
        often. Those samples still tell us the daylight is high, but they
        must never be allowed to teach the model about the lamps.
        """
        shape = self.shape(master, lights_on)

        gap = dt_seconds
        if now is not None and self._last_seen is not None:
            gap = (now - self._last_seen).total_seconds()

        if (
            trustworthy
            and self._last_shape is not None
            and self._last_measured is not None
            and 0 < gap <= self.config.max_pairing_gap_s
        ):
            self._learn(measured_lux, shape, gap)

        for_control = measured_lux if smoothed_lux is None else smoothed_lux
        residual = for_control - shape * self._scale
        self._daylight = max(0.0, residual)
        self._note_overshoot(residual, shape)
        self._last_shape = shape
        self._last_measured = measured_lux
        if now is not None:
            self._last_seen = now

    def _learn(self, measured_lux: float, shape: float, dt_seconds: float) -> None:
        cfg = self.config

        # How much the lamps moved, in the scale-free units of the profile.
        # Judged against the profile, not against the current estimate. Gate
        # it on the estimate and a collapsed one locks the door behind itself:
        # nothing looks big enough to learn from, so it can never recover.
        regressor = shape - self._last_shape
        if abs(regressor) < cfg.min_signal_fraction * self._profile.full_contribution:
            # The lamps barely moved. Nothing here is about them.
            return

        observed_delta = measured_lux - self._last_measured

        noise = (cfg.relative_noise * max(measured_lux, 1.0)) ** 2 * 2.0
        wander = cfg.daylight_drift_per_s * dt_seconds * max(
            self._daylight, cfg.daylight_drift_floor_lux
        )
        r = noise + wander * wander

        if abs(regressor * self._scale) < cfg.min_snr * math.sqrt(r):
            # The lamps did move, but not by more than the weather could have.
            # Learning from this would be learning the weather.
            return

        innovation = observed_delta - regressor * self._scale
        denominator = cfg.forgetting * r + regressor * regressor * self._variance
        if denominator <= 0:
            return

        self._track_change(innovation, r, regressor)

        gain = self._variance * regressor / denominator
        step = gain * innovation
        ceiling = cfg.max_relative_step * self._scale
        clamped = max(-ceiling, min(ceiling, step))

        self._scale = max(1e-4, self._scale + clamped)
        shrunk = max(
            1e-9, (self._variance - gain * regressor * self._variance) / cfg.forgetting
        )

        if abs(clamped) < abs(step) - 1e-12:
            # The update was cut short, so the estimate has *not* arrived and
            # must not start claiming it has. Left alone, the variance
            # collapses on the first strong observation and the model reports
            # full confidence while it is still three steps away from the
            # truth — confidently wrong, which is worse than visibly unsure.
            self._variance = min(cfg.max_variance, max(shrunk, self._variance * 0.9))
        else:
            self._variance = min(cfg.max_variance, shrunk)
        self.observations += 1

    def _note_overshoot(self, residual: float, shape: float) -> None:
        """Daylight cannot be negative, so a negative residual is a message.

        It says the model thinks the lamps deliver more than the sensor can
        see. Clamping it to zero and moving on throws that away — and the
        symptom is a room reporting "daylight 0" all evening while quietly
        being wrong about its own lamps.

        Counted rather than acted on directly: one negative reading is noise,
        a run of them is a model that needs to loosen up and let the next
        observation move it further.
        """
        if shape <= 0:
            self._overshooting = 0
            return
        if residual < -max(2.0, 0.05 * shape * self._scale):
            self._overshooting += 1
            if self._overshooting >= 4:
                self._variance = min(
                    self.config.max_variance, self._variance * 4.0
                )
                self._overshooting = 0
        else:
            self._overshooting = 0

    def _track_change(self, innovation: float, r: float, regressor: float) -> None:
        """CUSUM on the prediction error, to notice the room being rearranged.

        A moved sensor, a replaced lamp, a repainted wall and a curtain that
        was not there yesterday all look identical from here: the predictions
        start being wrong in a consistent direction. Which of them it was
        does not matter, because the response is the same — trust the old
        estimate less and let the new evidence back in.
        """
        cfg = self.config
        sigma = math.sqrt(max(r + regressor * regressor * self._variance, 1e-9))
        normalised = innovation / sigma

        self._cusum_up = max(0.0, self._cusum_up + normalised - cfg.change_slack)
        self._cusum_down = max(0.0, self._cusum_down - normalised - cfg.change_slack)

        if max(self._cusum_up, self._cusum_down) > cfg.change_threshold:
            self._variance = min(cfg.max_variance, self._variance * cfg.change_inflation)
            self._cusum_up = 0.0
            self._cusum_down = 0.0
            self.changes_detected += 1

    def master_for(self, wanted_lux: float) -> Optional[float]:
        """Master level that would put ``wanted_lux`` on the sensor.

        The profile knows the shape, this class knows the scale, and the
        caller should never have to hold both.
        """
        if wanted_lux <= 0:
            return None
        return self._profile.master_for_lux(wanted_lux / max(self._scale, 1e-9))

    @property
    def profile(self) -> DimProfile:
        return self._profile

    def snapshot(self) -> dict:
        """What was learned, so a restart does not throw it away.

        Found the hard way: the estimate walked from 103 lux down towards 44
        over an afternoon and jumped back to 103 on every restart, because
        what got saved was the *prior* on each lamp and not the scale that
        had been learned on top of it. Six restarts, six fresh starts, and a
        sensor graph that looked like the thing could not make up its mind.
        """
        return {
            "scale": self._scale,
            "variance": self._variance,
            "observations": self.observations,
            "changes_detected": self.changes_detected,
        }

    def restore(self, data: dict) -> None:
        if not data:
            return
        self._scale = max(1e-4, float(data.get("scale", self._scale)))
        self._variance = min(
            self.config.max_variance,
            max(1e-9, float(data.get("variance", self._variance))),
        )
        self.observations = int(data.get("observations", 0))
        self.changes_detected = int(data.get("changes_detected", 0))

    def rebind(self, profile: DimProfile) -> None:
        """Point at a different profile — how a mode switch is absorbed.

        Cinema mode takes three lamps out, so the room's shape changes. The
        *scale* does not: it describes the room, not the subset, and carrying
        it across is why switching modes costs nothing in accuracy.
        """
        if profile.full_contribution <= 0:
            raise ValueError("the new profile has no lamps that contribute")
        self._profile = profile
        self._last_shape = None
        self._last_measured = None

    def adopt_profile(self, relative_uncertainty: float) -> None:
        """The profile has just been measured directly. Start from it.

        Not the same as noting an external change, and the difference cost a
        real kitchen 29 percent. The scale exists to correct a *catalogue*
        figure that nobody measured; once somebody has switched the lamps off
        and on and read the meter, the profile is the measurement and the
        scale has nothing left to correct. Leaving it at the 1.29 it had
        learned turned a measured 13 lux into 16.8 — a correction applied to
        a correction.

        The uncertainty comes from the measurement itself: a calibration that
        cleared the noise by a factor of twelve is worth believing far more
        than one that scraped past by three.
        """
        self._scale = 1.0
        self._variance = min(
            self.config.max_variance,
            max(relative_uncertainty, 0.02) ** 2,
        )

    def note_external_change(self) -> None:
        """Told from outside that the room changed — a lamp swapped, say."""
        self._variance = min(
            self.config.max_variance, self._variance * self.config.change_inflation
        )
        self._cusum_up = 0.0
        self._cusum_down = 0.0
