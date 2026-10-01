"""The estimator is the part that makes closing the loop safe, so it gets
the most direct tests: feed it a room whose truth we know and check what it
works out, including when we change that truth underneath it.
"""

from __future__ import annotations

import pytest

from custom_components.calm.core import ContributionEstimator, DimProfile, Lamp
from custom_components.calm.core.estimator import EstimatorConfig

from .helpers import PRIOR_PER_LAMP, make_profile

PRIOR_TOTAL = PRIOR_PER_LAMP * 5


def drive(
    estimator: ContributionEstimator,
    profile: DimProfile,
    true_total: float,
    schedule,
    daylight=lambda i: 0.0,
    dt: float = 30.0,
):
    """Run a list of master levels past the estimator using a known truth."""
    shape = lambda m: profile.luminous_at(m) / profile.full_contribution
    for i, master in enumerate(schedule):
        measured = daylight(i) + true_total * shape(master)
        estimator.update(
            measured_lux=measured, master=master, lights_on=True, dt_seconds=dt
        )
    return estimator


def sweep(cycles: int = 12):
    """Level changes of the kind normal operation produces anyway."""
    out = []
    for _ in range(cycles):
        for level in (0.8, 0.55, 0.75, 0.35, 0.9, 0.5):
            out.extend([level] * 3)
    return out


def test_it_starts_out_believing_the_catalogue(profile):
    estimator = ContributionEstimator(profile)
    assert estimator.full_contribution == pytest.approx(PRIOR_TOTAL)
    assert estimator.confidence < 0.2


def test_it_finds_the_truth_when_the_catalogue_is_wrong(profile):
    """The prior is 200 lux, the room really gives 120. It has to notice."""
    estimator = drive(ContributionEstimator(profile), profile, 120.0, sweep())
    assert estimator.full_contribution == pytest.approx(120.0, rel=0.1)
    assert estimator.confidence > 0.8


def test_it_works_when_the_catalogue_was_too_pessimistic(profile):
    estimator = drive(ContributionEstimator(profile), profile, 340.0, sweep())
    assert estimator.full_contribution == pytest.approx(340.0, rel=0.1)


def test_drifting_daylight_does_not_bias_it(profile):
    """Daylight moves while the lamps do; the estimate must survive that."""
    estimator = drive(
        ContributionEstimator(profile),
        profile,
        120.0,
        sweep(),
        daylight=lambda i: 90.0 + 0.35 * i,
    )
    assert estimator.full_contribution == pytest.approx(120.0, rel=0.15)


def test_the_daylight_estimate_is_what_is_left_over(profile):
    estimator = drive(
        ContributionEstimator(profile), profile, 120.0, sweep(), daylight=lambda i: 140.0
    )
    assert estimator.daylight_lux == pytest.approx(140.0, rel=0.12)


def test_lamps_off_gives_a_clean_daylight_reading(profile):
    """The best measurement this system ever gets, and it is free."""
    estimator = ContributionEstimator(profile)
    estimator.update(measured_lux=310.0, master=0.0, lights_on=False, dt_seconds=30.0)
    assert estimator.daylight_lux == pytest.approx(310.0)


def test_a_moved_sensor_is_noticed_and_recovered_from(profile):
    """No one presses anything. The estimate has to come back on its own.

    The shape of the recovery is the point, not just the endpoint: the model
    should stop trusting itself, and then earn that trust back. A system that
    stayed confident throughout would keep steering hard on a model that had
    become wrong about everything.
    """
    estimator = drive(ContributionEstimator(profile), profile, 120.0, sweep())
    settled = estimator.relative_uncertainty
    assert estimator.confidence > 0.8

    # Same room, sensor rehung: every coupling changes at once.
    shape = lambda m: profile.luminous_at(m) / profile.full_contribution
    worst = settled
    for master in sweep(3):
        estimator.update(
            measured_lux=40.0 * shape(master),
            master=master,
            lights_on=True,
            dt_seconds=30.0,
        )
        worst = max(worst, estimator.relative_uncertainty)

    assert estimator.changes_detected >= 1
    assert worst > settled * 3

    drive(estimator, profile, 40.0, sweep(14))
    assert estimator.full_contribution == pytest.approx(40.0, rel=0.15)
    assert estimator.confidence > 0.8


def test_confidence_follows_the_evidence_not_the_clock(profile):
    """A system that trusts itself because time passed will be confidently
    wrong the day after someone moves the sensor."""
    estimator = ContributionEstimator(profile)
    for _ in range(400):
        estimator.update(
            measured_lux=200.0, master=0.5, lights_on=True, dt_seconds=30.0
        )
    assert estimator.confidence < 0.2


def test_it_ignores_samples_it_was_told_not_to_trust(profile):
    """Direct sun on the sensor must never teach it about the lamps."""
    estimator = ContributionEstimator(profile)
    for master in sweep():
        estimator.update(
            measured_lux=9000.0,
            master=master,
            lights_on=True,
            dt_seconds=30.0,
            trustworthy=False,
        )
    assert estimator.full_contribution == pytest.approx(PRIOR_TOTAL)
    assert estimator.observations == 0


def test_tiny_level_changes_teach_it_nothing(profile):
    """Below the noise there is no information, only the illusion of it."""
    estimator = ContributionEstimator(profile)
    drive(estimator, profile, 120.0, [0.5, 0.501, 0.5, 0.499] * 40)
    assert estimator.observations == 0


def test_a_mode_keeps_what_the_room_already_taught_it(profile):
    estimator = drive(ContributionEstimator(profile), profile, 120.0, sweep())
    learned = estimator.full_contribution

    cinema = profile.with_enabled(["spot_0", "spot_1"])
    estimator.rebind(cinema)
    assert estimator.full_contribution == pytest.approx(learned * 2 / 5, rel=1e-6)


def test_a_profile_without_priors_is_refused():
    bare = DimProfile.from_lamps([Lamp(id="a", weight=1.0, full_contribution=0.0)])
    with pytest.raises(ValueError):
        ContributionEstimator(bare)


def test_forgetting_lets_it_track_a_slow_drift(profile):
    """Dust, ageing LEDs, a lighter rug — nothing announces itself.

    Deliberately slower than the change detector: a gradual decline should be
    followed, not treated as an event. Lagging behind a drift is fine; the
    thing that must not happen is the estimate freezing at its first answer.
    """
    estimator = drive(ContributionEstimator(profile), profile, 120.0, sweep())
    for step in range(80):
        drive(estimator, profile, 120.0 - step * 0.75, sweep(1))
    assert estimator.full_contribution < 100.0
    assert estimator.full_contribution == pytest.approx(60.0, rel=0.35)


def test_what_it_learned_survives_a_restart(profile):
    """Found on a live install, and it had been hiding all afternoon.

    The estimate walked from 103 lux down towards 44 over an evening and
    jumped back to 103 at every restart, because what was being saved were
    the catalogue priors on each lamp rather than the scale learned on top of
    them. Six restarts, six fresh starts, and a graph that looked like the
    thing could not make up its mind.
    """
    estimator = drive(ContributionEstimator(profile), profile, 120.0, sweep())
    learned = estimator.full_contribution
    assert learned == pytest.approx(120.0, rel=0.1)

    revived = ContributionEstimator(profile)
    assert revived.full_contribution != pytest.approx(learned, rel=0.01)
    revived.restore(estimator.snapshot())
    assert revived.full_contribution == pytest.approx(learned)
    assert revived.confidence == pytest.approx(estimator.confidence)


def test_a_step_that_had_to_be_cut_short_does_not_buy_certainty(profile):
    """Confidently wrong is worse than visibly unsure.

    Each update may move the estimate by at most a third, so a first
    observation against a badly wrong prior is always truncated. If the
    variance collapsed anyway the model would report full confidence while
    still three steps from the truth — which is exactly what a real
    installation did, reporting 100% before it had finished moving.
    """
    estimator = ContributionEstimator(profile)
    shape = lambda m: profile.luminous_at(m) / profile.full_contribution
    for master in (0.2, 0.9, 0.2, 0.9):
        estimator.update(
            measured_lux=8.0 * shape(master),
            master=master,
            lights_on=True,
            dt_seconds=30.0,
        )
    assert estimator.full_contribution > 20.0, "nog niet aangekomen"
    assert estimator.confidence < 0.95, "beweert zekerheid die er niet is"


def test_a_room_that_is_darker_than_the_model_says_loosens_up(profile):
    """Daylight cannot be negative, so a run of negative residuals is proof
    the lamps are being overestimated — and evidence worth acting on."""
    estimator = drive(ContributionEstimator(profile), profile, 120.0, sweep())
    settled = estimator.relative_uncertainty

    for _ in range(6):
        estimator.update(
            measured_lux=5.0, master=0.9, lights_on=True, dt_seconds=30.0
        )
    assert estimator.daylight_lux == 0.0
    assert estimator.relative_uncertainty > settled


def test_a_measured_profile_is_adopted_rather_than_corrected():
    """What cost a real kitchen 29 percent.

    The scale exists to correct a catalogue figure nobody measured. Once
    somebody has switched the lamps off and on and read the meter, the
    profile *is* the measurement — so a scale of 1.29 left in place turned a
    measured 13 lux into 16.8, a correction applied to a correction.
    """
    profile = DimProfile.from_lamps(
        [Lamp(id="a", weight=1.0, full_contribution=6.5),
         Lamp(id="b", weight=1.0, full_contribution=6.5)]
    )
    estimator = ContributionEstimator(profile)
    estimator.restore({"scale": 1.29, "variance": 0.2, "observations": 9,
                       "changes_detected": 0})
    assert estimator.full_contribution == pytest.approx(16.8, abs=0.1)

    estimator.adopt_profile(1.0 / 12.3)      # authority of a good run
    assert estimator.full_contribution == pytest.approx(13.0, abs=0.01)


def test_how_far_the_measurement_cleared_the_noise_sets_how_much_it_is_trusted():
    """A run that beat the noise twelve times over is worth more than one
    that scraped past by three, and the confidence should say so."""
    profile = DimProfile.from_lamps([Lamp(id="a", weight=1.0, full_contribution=10.0)])
    clean, scraped = ContributionEstimator(profile), ContributionEstimator(profile)
    clean.adopt_profile(1.0 / 12.3)
    scraped.adopt_profile(1.0 / 3.1)
    assert clean.confidence > scraped.confidence


def test_adopting_never_claims_more_certainty_than_is_credible():
    """A perfect ratio would otherwise divide by nothing and report a model
    that can never be argued with again."""
    profile = DimProfile.from_lamps([Lamp(id="a", weight=1.0, full_contribution=10.0)])
    estimator = ContributionEstimator(profile)
    estimator.adopt_profile(0.0)
    assert estimator.confidence <= 1.0
    assert estimator.relative_uncertainty > 0
