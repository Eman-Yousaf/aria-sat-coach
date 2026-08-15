"""Intervention ranking, uncertainty, exploration and the time budget."""

import pytest

from conftest import run_episode, seed_attempts


def states_for(phone):
    import mastery
    return mastery.get_all_states(phone)


def warm(phone):
    """A student with enough history that skills are distinguishable."""
    seed_attempts(phone, "m_equivalent_expr", 6, 2)
    seed_attempts(phone, "rw_boundaries", 5, 3)
    seed_attempts(phone, "m_systems", 4, 1)


# --- the bridge to the score model ---------------------------------------

def test_points_per_mastery_prices_test_frequency(phone):
    """A skill that appears more often on the exam must be worth more per unit
    of mastery. This is the term the old engine already got right, and the new
    layer multiplies rather than replaces it."""
    import counterfactual
    warm(phone)
    states = states_for(phone)

    common = counterfactual.points_per_mastery(states, "m_equivalent_expr")
    rare = counterfactual.points_per_mastery(states, "m_probability")
    assert common > rare > 0


def test_points_per_mastery_is_zero_at_the_ceiling(phone):
    import counterfactual
    import simulator
    warm(phone)
    states = states_for(phone)
    states["m_systems"] = simulator._with_mastery(states["m_systems"], 0.999)
    assert counterfactual.points_per_mastery(states, "m_systems") == 0.0


# --- candidate generation -------------------------------------------------

def test_candidates_respect_the_minute_budget(phone):
    import counterfactual
    warm(phone)
    for minutes in (4, 8, 20):
        for c in counterfactual.candidates(phone, states_for(phone), minutes):
            assert c.minutes <= minutes + 1e-9


def test_setup_heavy_interventions_vanish_from_short_sessions(phone):
    """An explanation costs four minutes before the first question, so it
    cannot exist in a five-minute session. That constraint has to bite at
    candidate generation, not be discovered later by the tutor."""
    import counterfactual
    warm(phone)

    short = {c.intervention for c in counterfactual.candidates(
        phone, states_for(phone), 5)}
    long = {c.intervention for c in counterfactual.candidates(
        phone, states_for(phone), 25)}

    assert "direct_explanation" not in short
    assert "direct_explanation" in long
    assert "retrieval_practice" in short


def test_misconception_repair_needs_a_misconception(phone):
    import counterfactual
    import mastery
    warm(phone)

    available = {c.intervention for c in counterfactual.candidates(
        phone, states_for(phone), 25)}
    assert "misconception_repair" not in available

    for i in range(3):
        mastery.record_attempt(phone, "m_equivalent_expr", False,
                               question_id=f"m{i}", misconception="sign_error")
    available = {c.intervention for c in counterfactual.candidates(
        phone, states_for(phone), 25)}
    assert "misconception_repair" in available


def test_worked_example_budgets_the_item_it_demonstrates(phone):
    """A worked example burns one bank item before the student answers
    anything. Unbudgeted, it produced episodes with a demonstration and no
    practice on any skill the bank was thin on."""
    import bank
    import counterfactual
    import mastery
    warm(phone)

    skill = "rw_boundaries"
    total = bank.remaining_for(skill, set())
    # Leave exactly one unseen item on that skill.
    for i, q in enumerate(
            [q for q in bank.load() if q["skill_id"] == skill][:total - 1]):
        mastery.record_attempt(phone, skill, True, question_id=q["id"])

    cands = [c for c in counterfactual.candidates(phone, states_for(phone), 25)
             if c.skill_id == skill]
    assert all(c.intervention != "worked_example" for c in cands)


# --- uncertainty ----------------------------------------------------------

def test_ranking_reports_an_interval_not_a_point(phone, rng):
    import counterfactual
    warm(phone)
    ranked = counterfactual.rank(
        phone, counterfactual.candidates(phone, states_for(phone), 20), rng=rng)
    assert ranked
    for c in ranked:
        assert c.value_low <= c.value <= c.value_high or c.value_low <= c.value_high
        assert 0.0 <= c.p_best <= 1.0
        assert 0.0 <= c.p_failure <= 1.0


def test_uncertainty_narrows_as_evidence_arrives(phone, rng):
    import counterfactual
    warm(phone)

    def relative_spread():
        # Relative, not absolute. Evidence that an intervention is twice as
        # good raises the whole forecast, so the interval in points-per-minute
        # widens even as the *doubt* shrinks -- measuring the absolute width
        # would report the opposite of what happened.
        cands = [c for c in counterfactual.candidates(phone, states_for(phone), 20)
                 if c.intervention == "worked_example"]
        counterfactual.rank(phone, cands, rng=rng)
        return min((c.value_high - c.value_low) / max(c.value, 1e-9)
                   for c in cands)

    before = relative_spread()
    for _ in range(8):
        run_episode(phone, "worked_example", "m_systems", multiplier=1.6,
                    retained=True)
    assert relative_spread() < before


def test_intervention_probabilities_sum_to_one(phone, rng):
    import counterfactual
    warm(phone)
    ranked = counterfactual.rank(
        phone, counterfactual.candidates(phone, states_for(phone), 20), rng=rng)
    by_intervention = {c.intervention: c.p_best_intervention for c in ranked}
    assert sum(by_intervention.values()) == pytest.approx(1.0, abs=1e-9)


# --- exploration and exploitation ----------------------------------------

def test_a_new_student_explores(phone, rng):
    """With no evidence, any lead comes from the cost model rather than from
    the student, and claiming to know would be the overclaim this layer exists
    to prevent."""
    import counterfactual
    warm(phone)
    decision = counterfactual.decide(phone, states_for(phone), 20, rng=rng)
    assert decision.mode == "explore"
    assert decision.chosen.episodes == 0


def test_exploration_spreads_across_approaches(phone, rng):
    """Successive uncertain sessions must sweep the catalogue rather than
    re-confirm one arm, or the evidence base is one intervention deep."""
    import counterfactual
    warm(phone)

    picked = []
    for _ in range(4):
        decision = counterfactual.decide(phone, states_for(phone), 20, rng=rng)
        picked.append(decision.chosen.intervention)
        run_episode(phone, decision.chosen.intervention,
                    decision.chosen.skill_id, multiplier=1.0)
    assert len(set(picked)) >= 3


def test_strong_evidence_produces_exploitation(phone, rng):
    import counterfactual
    warm(phone)
    for _ in range(8):
        run_episode(phone, "worked_example", "m_equivalent_expr",
                    multiplier=2.6, retained=True)
    decision = counterfactual.decide(phone, states_for(phone), 20, rng=rng)
    assert decision.mode == "exploit"
    assert decision.chosen.intervention == "worked_example"


def test_an_experiment_stays_low_risk(phone, rng):
    """Whenever Aria deliberately passes over the front-runner, the option she
    takes instead has to be one that could plausibly be better."""
    import counterfactual
    warm(phone)

    for _ in range(6):
        decision = counterfactual.decide(phone, states_for(phone), 20, rng=rng)
        if decision.chosen.key != decision.greedy.key:
            assert decision.mode == "explore"
            assert decision.chosen.value_high >= (
                counterfactual.EXPERIMENT_VALUE_FLOOR * decision.greedy.value)
            assert decision.experiment_note
        run_episode(phone, decision.chosen.intervention,
                    decision.chosen.skill_id, multiplier=1.2)


def test_setup_heavy_options_remain_explorable(phone, rng):
    """Regression. Filtering experiments on *expected* value meant a worked
    example -- two thirds the value of cold retrieval under the neutral prior,
    purely because of its three minutes of setup -- could never be tried, and
    so could never accumulate the evidence that would have shown it was the
    best approach available for that student.

    The assertion is on the gate itself rather than on a sampled run: whether
    Thompson happens to draw a given arm within N rounds is luck, but whether
    the arm is *eligible* to be drawn at all is the property that broke.
    """
    import counterfactual
    warm(phone)

    cands = counterfactual.rank(
        phone, counterfactual.candidates(phone, states_for(phone), 25), rng=rng)
    greedy = cands[0]
    worked = max((c for c in cands if c.intervention == "worked_example"),
                 key=lambda c: c.value)

    # Under the old rule this was the failing comparison: its *mean* value is
    # about two thirds of the front-runner's, below any sensible floor.
    assert worked.value < counterfactual.EXPERIMENT_VALUE_FLOOR * greedy.value
    # Under the current rule its optimistic estimate clears the bar, so it can
    # be tested and can therefore earn its way to the top.
    assert worked.value_high >= counterfactual.EXPERIMENT_VALUE_FLOOR * greedy.value


# --- the time-constrained optimiser --------------------------------------

def test_the_budget_changes_the_chosen_approach(phone, rng):
    """The headline claim of the joint optimisation: with less time, a
    better-teaching but slower approach loses to a faster one.

    Asserted against `greedy` -- what pure exploitation would pick -- so the
    test measures the value model rather than the exploration policy, which is
    entitled to try something else on any given session and is covered by its
    own tests.
    """
    import random

    import counterfactual
    warm(phone)
    for _ in range(8):
        run_episode(phone, "worked_example", "m_equivalent_expr",
                    multiplier=2.6, retained=True)

    generous = counterfactual.decide(phone, states_for(phone), 25,
                                     rng=random.Random(7))
    # Four minutes cannot contain three minutes of setup plus a two-minute
    # question, so the approach this student learns most from is simply not
    # available -- and the engine has to spend the four minutes on something
    # it believes is worse rather than on nothing.
    cramped = counterfactual.decide(phone, states_for(phone), 4,
                                    rng=random.Random(7))
    assert generous.greedy.intervention == "worked_example"
    assert cramped is not None
    assert cramped.greedy.intervention != "worked_example"
    assert cramped.chosen.minutes <= 4


def test_plan_fits_the_session_and_does_not_repeat_a_skill(phone, rng):
    import counterfactual
    warm(phone)
    plan = counterfactual.plan(phone, states_for(phone), 25, rng=rng)
    assert not plan.is_empty
    assert plan.total_minutes <= 25 + 1e-9
    skills = [b.candidate.skill_id for b in plan.blocks]
    assert len(skills) == len(set(skills))


def test_plan_alloc_round_trips_into_a_session(phone, rng):
    import counterfactual
    from interventions import INTERVENTION_BY_ID
    warm(phone)
    plan = counterfactual.plan(phone, states_for(phone), 25, rng=rng)
    for skill_id, intervention, questions in plan.alloc():
        assert intervention in INTERVENTION_BY_ID
        assert questions > 0
        assert skill_id


def test_tiny_budget_yields_something_or_nothing_cleanly(phone, rng):
    import counterfactual
    warm(phone)
    plan = counterfactual.plan(phone, states_for(phone), 1, rng=rng)
    assert plan.is_empty or plan.total_minutes <= 1 + 1e-9


# --- the counterfactual table --------------------------------------------

def test_shadow_table_labels_estimates_as_estimates(phone, rng):
    import counterfactual
    warm(phone)
    decision = counterfactual.decide(phone, states_for(phone), 25, rng=rng)
    rows = counterfactual.shadow_table(decision)
    assert rows[0]["estimated"] is False
    assert all(r["estimated"] for r in rows[1:])
    assert len(rows) > 1


def test_alternatives_compare_approaches_on_the_chosen_skill(phone, rng):
    """The interesting counterfactual is a different approach to the same
    skill, not the same approach to a near-identical skill."""
    import counterfactual
    warm(phone)
    decision = counterfactual.decide(phone, states_for(phone), 25, rng=rng)
    same_skill = [a for a in decision.alternatives
                  if a.skill_id == decision.chosen.skill_id]
    assert same_skill
    interventions = [a.intervention for a in same_skill]
    assert len(interventions) == len(set(interventions))
    assert decision.chosen.intervention not in interventions


# --- degenerate inputs ----------------------------------------------------

def test_no_candidates_returns_none_rather_than_raising(phone, rng):
    import counterfactual
    warm(phone)
    assert counterfactual.decide(phone, states_for(phone), 0, rng=rng) is None


def test_student_with_no_history_still_gets_a_decision(phone, rng):
    import counterfactual
    decision = counterfactual.decide(phone, states_for(phone), 20, rng=rng)
    assert decision is not None
    assert decision.chosen.questions > 0
    assert decision.reasons
