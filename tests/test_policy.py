"""The learning-response model: priors, updates, pooling, persistence."""

import math

import pytest

from conftest import run_episode, seed_attempts


# --- the neutral prior ----------------------------------------------------

def test_every_intervention_starts_identical(phone):
    """The central commitment: no built-in favourite.

    If this fails, every demo of Aria "discovering" a preference is really her
    reciting one that was written into the priors.
    """
    import policy
    from interventions import INTERVENTIONS

    values = {e.intervention: e.durable_effectiveness for e in policy.profile(phone)}
    assert len(values) == len(INTERVENTIONS)
    assert len(set(round(v, 9) for v in values.values())) == 1


def test_prior_carries_no_evidence(phone):
    import policy
    est = policy.estimate(phone, "worked_example")
    assert est.episodes == 0
    assert est.confidence == pytest.approx(0.0, abs=1e-9)
    assert est.multiplier.mean == pytest.approx(policy.POP_MULTIPLIER_MEAN)
    assert est.multiplier.sd == pytest.approx(policy.POP_MULTIPLIER_SD)


# --- conjugate updating ---------------------------------------------------

def test_evidence_moves_the_mean_and_shrinks_the_variance(phone):
    import policy

    before = policy.multiplier_posterior(phone, "worked_example")
    for _ in range(4):
        run_episode(phone, "worked_example", "m_equivalent_expr", multiplier=2.0)
    after = policy.multiplier_posterior(phone, "worked_example")

    assert after.mean > before.mean
    assert after.sd < before.sd
    assert after.n == 4


def test_update_is_precision_weighted(phone):
    """Posterior mean must sit between the prior and the sample, nearer the
    sample as evidence accumulates. A model that jumped straight to the sample
    would be convinced by one episode."""
    import policy

    run_episode(phone, "socratic", "m_systems", multiplier=3.0)
    one = policy.multiplier_posterior(phone, "socratic").mean
    assert policy.POP_MULTIPLIER_MEAN < one < 3.0

    for _ in range(6):
        run_episode(phone, "socratic", "m_systems", multiplier=3.0)
    many = policy.multiplier_posterior(phone, "socratic").mean
    assert many > one
    assert many < 3.0


def test_evidence_is_intervention_specific(phone):
    import policy
    for _ in range(4):
        run_episode(phone, "timed_drill", "m_systems", multiplier=2.5)
    assert policy.multiplier_posterior(phone, "timed_drill").mean > 1.3
    assert policy.multiplier_posterior(phone, "hint_first").mean == pytest.approx(
        policy.POP_MULTIPLIER_MEAN)


def test_evidence_is_student_specific(phone):
    import policy
    other = "15559990000"
    for _ in range(4):
        run_episode(phone, "worked_example", "m_systems", multiplier=2.5)
    assert policy.multiplier_posterior(other, "worked_example").mean == pytest.approx(
        policy.POP_MULTIPLIER_MEAN)


def test_a_bad_intervention_is_learned_as_bad(phone):
    """Negative observations are allowed through. An intervention that leaves a
    student worse off has to be representable or it can never be demoted."""
    import policy
    for _ in range(5):
        run_episode(phone, "timed_drill", "m_systems", multiplier=-0.5,
                    correct=0)
    assert policy.multiplier_posterior(phone, "timed_drill").mean < 0.6


# --- the normalisation ----------------------------------------------------

def test_multiplier_is_scale_free_across_mastery(phone):
    """The same relative learning at different mastery levels must record as
    the same multiplier -- otherwise an intervention looks worse purely because
    it was used on a skill the student already half-knew."""
    import policy
    from skills import SKILL_BY_ID

    def observed_at(mastery_before: float) -> float:
        """Run an episode that learns 1.8x what an average question would."""
        step = policy.baseline_step(mastery_before)
        episode_id = policy.record_episode(
            phone, "socratic", "m_systems", SKILL_BY_ID["m_systems"].domain,
            mastery_before, 3)
        policy.close_episode(
            episode_id, mastery_after=mastery_before + 1.8 * 3 * step,
            minutes=10.0, answered=3, correct=2, completed=True)
        return policy.episodes_for(phone, "socratic")[-1].learning_multiplier

    assert observed_at(0.25) == pytest.approx(1.8, rel=1e-6)
    assert observed_at(0.55) == pytest.approx(1.8, rel=1e-6)
    assert observed_at(0.80) == pytest.approx(1.8, rel=1e-6)


def test_baseline_step_never_divides_by_zero(phone):
    import policy
    assert policy.baseline_step(0.999) >= policy.MIN_BASELINE_STEP
    assert policy.baseline_step(0.0) > 0


def test_saturated_mastery_cannot_manufacture_a_huge_multiplier(phone):
    """Near mastery 1.0 one average question buys almost nothing, so an
    unfloored denominator would turn a rounding-sized gain into a spectacular
    learning multiplier and hand the intervention a permanent lead."""
    import policy
    from skills import SKILL_BY_ID

    episode_id = policy.record_episode(
        phone, "worked_example", "m_circles",
        SKILL_BY_ID["m_circles"].domain, 0.997, 3)
    policy.close_episode(episode_id, mastery_after=0.999, minutes=9.0,
                         answered=3, correct=3, completed=True,
                         points_per_mastery=50.0)
    observed = policy.episodes_for(phone, "worked_example")[0].learning_multiplier
    assert observed < 1.0


def test_zero_answer_episodes_are_not_evidence(phone):
    """A block the student never answered is not an observation about the
    approach -- it is usually the bank running out of unseen items."""
    import policy
    from skills import SKILL_BY_ID

    episode_id = policy.record_episode(
        phone, "worked_example", "m_systems",
        SKILL_BY_ID["m_systems"].domain, 0.25, 3)
    policy.close_episode(episode_id, mastery_after=0.25, minutes=3.0,
                         answered=0, correct=0, completed=False,
                         points_per_mastery=50.0)

    assert policy.episodes_for(phone, "worked_example") == []
    assert policy.multiplier_posterior(phone, "worked_example").mean == pytest.approx(
        policy.POP_MULTIPLIER_MEAN)


# --- partial pooling ------------------------------------------------------

def test_one_sided_evidence_transfers_but_carries_more_doubt(phone):
    """With evidence in one domain only, the best guess for every other domain
    is the same number -- what differs is how sure Aria is of it.

    That equality is the point of pooling, not a bug: refusing to transfer
    would leave grammar at the population prior and waste five sessions of
    evidence about how this student responds.
    """
    import policy

    for _ in range(5):
        run_episode(phone, "worked_example", "m_systems", multiplier=2.5)

    algebra = policy.multiplier_posterior(phone, "worked_example", "Algebra")
    grammar = policy.multiplier_posterior(
        phone, "worked_example", "Standard English Conventions")

    assert grammar.mean == pytest.approx(algebra.mean, rel=1e-6)
    assert grammar.mean > policy.POP_MULTIPLIER_MEAN
    # The domain the evidence came from is the one Aria is sure about.
    assert algebra.sd < grammar.sd


def test_pooling_never_widens_beyond_the_population_prior(phone):
    """With no evidence at all, asking about a domain must not be *less*
    certain than asking about nothing. Getting this wrong put the tenth
    percentile of every forecast at zero."""
    import policy
    for domain in ("Algebra", "Craft and Structure", "Geometry and Trigonometry"):
        posterior = policy.multiplier_posterior(phone, "hint_first", domain)
        assert posterior.sd <= policy.POP_MULTIPLIER_SD + 1e-9


def test_domain_can_disagree_with_the_student_average(phone):
    import policy
    for _ in range(4):
        run_episode(phone, "hint_first", "m_systems", multiplier=2.4)
    for _ in range(4):
        run_episode(phone, "hint_first", "rw_boundaries", multiplier=0.2)

    algebra = policy.multiplier_posterior(phone, "hint_first", "Algebra")
    conventions = policy.multiplier_posterior(
        phone, "hint_first", "Standard English Conventions")
    assert algebra.mean > conventions.mean + 0.4


# --- retention ------------------------------------------------------------

def test_retention_separates_durable_from_immediate(phone):
    """Two interventions with identical immediate performance must be ranked by
    what survived. This is the whole thesis in one assertion."""
    import policy

    for _ in range(4):
        run_episode(phone, "hint_first", "m_systems", multiplier=2.0,
                    retained=False)
    for _ in range(4):
        run_episode(phone, "retrieval_practice", "rw_boundaries", multiplier=2.0,
                    retained=True)

    shallow = policy.estimate(phone, "hint_first")
    durable = policy.estimate(phone, "retrieval_practice")

    assert shallow.multiplier.mean == pytest.approx(durable.multiplier.mean, rel=0.2)
    assert durable.retention.mean > shallow.retention.mean
    assert durable.durable_effectiveness > shallow.durable_effectiveness

    ranked = [e.intervention for e in policy.profile(phone)]
    assert ranked.index("retrieval_practice") < ranked.index("hint_first")


def test_retention_prior_is_uninformative(phone):
    import policy
    assert policy.retention_posterior(phone, "socratic").mean == pytest.approx(0.5)


# --- engagement -----------------------------------------------------------

def test_abandoning_an_episode_lowers_engagement(phone):
    import policy
    from skills import SKILL_BY_ID

    before = policy.engagement_posterior(phone, "direct_explanation").mean
    for _ in range(4):
        episode_id = policy.record_episode(
            phone, "direct_explanation", "m_systems",
            SKILL_BY_ID["m_systems"].domain, 0.25, 3)
        policy.close_episode(episode_id, mastery_after=0.28, minutes=6.0,
                             answered=1, correct=0, completed=False,
                             points_per_mastery=50.0)
    assert policy.engagement_posterior(phone, "direct_explanation").mean < before


# --- persistence and recovery --------------------------------------------

def test_estimates_survive_a_restart(phone):
    """Everything is derived from rows, so a fresh process must reach the same
    numbers. Nothing may live only in memory."""
    import policy

    for _ in range(3):
        run_episode(phone, "worked_example", "m_systems", multiplier=2.2,
                    retained=True)
    before = policy.estimate(phone, "worked_example")

    policy._invalidate()          # simulate a cold process
    after = policy.estimate(phone, "worked_example")

    assert after.multiplier.mean == pytest.approx(before.multiplier.mean)
    assert after.retention.mean == pytest.approx(before.retention.mean)
    assert after.episodes == before.episodes


def test_open_episodes_are_excluded_until_closed(phone):
    import policy
    from skills import SKILL_BY_ID

    policy.record_episode(phone, "socratic", "m_systems",
                          SKILL_BY_ID["m_systems"].domain, 0.25, 3)
    assert policy.episodes_for(phone, "socratic") == []
    assert policy.open_episode_ids(phone)
    assert policy.multiplier_posterior(phone, "socratic").mean == pytest.approx(
        policy.POP_MULTIPLIER_MEAN)


def test_unknown_intervention_is_rejected(phone):
    import policy
    with pytest.raises(ValueError):
        policy.record_episode(phone, "hypnosis", "m_systems", "Algebra", 0.3, 3)


def test_closing_a_missing_episode_is_not_fatal(phone):
    import policy
    assert policy.close_episode(999999, 0.5, 5.0, 3, 2, True) is None


# --- regret ---------------------------------------------------------------

def test_regret_compares_forecast_to_outcome(phone):
    import policy
    from skills import SKILL_BY_ID

    episode_id = policy.record_episode(
        phone, "timed_drill", "m_systems", SKILL_BY_ID["m_systems"].domain,
        0.25, 5, expected_points_per_min=2.0, decision_mode="exploit")
    policy.close_episode(episode_id, mastery_after=0.26, minutes=7.0,
                         answered=5, correct=1, completed=True,
                         points_per_mastery=50.0)

    rows = policy.regret_log(phone)
    assert len(rows) == 1
    assert rows[0]["expected"] == pytest.approx(2.0)
    assert rows[0]["regret"] == pytest.approx(
        rows[0]["expected"] - rows[0]["observed"])
    assert rows[0]["regret"] > 0      # forecast beat reality


def test_regret_is_absent_without_a_forecast(phone):
    import policy
    run_episode(phone, "socratic", "m_systems", multiplier=1.0)
    assert policy.regret_log(phone) == []


# --- the distributions themselves ----------------------------------------

def test_gaussian_precision_adds():
    import policy
    prior = policy.Gaussian(1.0, 0.45)
    posterior = prior.updated([2.0, 2.0, 2.0], obs_sd=0.55)

    tau = 1 / 0.45 ** 2 + 3 / 0.55 ** 2
    assert posterior.sd == pytest.approx(math.sqrt(1 / tau))
    expected_mean = ((1 / 0.45 ** 2) * 1.0 + (3 / 0.55 ** 2) * 2.0) / tau
    assert posterior.mean == pytest.approx(expected_mean)


def test_beta_moves_with_observations():
    import policy
    beta = policy.Beta(2.0, 2.0)
    assert beta.mean == pytest.approx(0.5)
    assert beta.observed(6, 0).mean > 0.7
    assert beta.observed(0, 6).mean < 0.3
