"""The loop, end to end.

    evidence -> knowledge -> learning response -> counterfactual ranking
      -> time-constrained decision -> tutor -> outcome -> policy update

The required proof is not that any single stage works -- the other files cover
that -- but that a decision Aria makes today is *caused* by what happened
yesterday, and would have been different had yesterday gone differently. A
policy layer that logs beautifully and changes nothing is the exact failure
mode worth testing against.
"""

import random

import pytest

from conftest import run_episode, seed_attempts


def states(phone):
    import mastery
    return mastery.get_all_states(phone)


def warm(phone):
    seed_attempts(phone, "m_equivalent_expr", 6, 2)
    seed_attempts(phone, "rw_boundaries", 5, 3)
    seed_attempts(phone, "m_systems", 4, 1)
    seed_attempts(phone, "rw_words_in_context", 5, 2)


def preference(phone, intervention, minutes=20, trials=8, believed=True):
    """How often this approach comes out on top across independent draws.

    The decision is stochastic by design -- Thompson sampling is the
    exploration policy -- so a single call proves nothing. This measures the
    thing that is actually claimed to move: the probability.

    `believed` selects which question is being asked, and the distinction
    matters. `greedy` is what Aria currently thinks is best; `chosen` is what
    she will actually run, which may deliberately be something else while she
    is still testing an approach she has never tried. Conflating them makes a
    working exploration policy look like a broken preference.
    """
    import counterfactual
    hits = 0
    for seed in range(trials):
        decision = counterfactual.decide(phone, states(phone), minutes,
                                         rng=random.Random(seed))
        if decision is None:
            continue
        candidate = decision.greedy if believed else decision.chosen
        if candidate.intervention == intervention:
            hits += 1
    return hits / trials


def try_the_alternatives(phone, poorly=True):
    """Let Aria run whatever she wants to test, and record how it went.

    Closing the loop is the point. Left untried, an approach keeps its wide
    prior and keeps winning the right to be experimented with, so exploration
    never ends -- in a test because nothing feeds the outcome back, and never
    in production, where the student answers.
    """
    import counterfactual
    for seed in range(12):
        decision = counterfactual.decide(phone, states(phone), 20,
                                         rng=random.Random(seed))
        if decision is None or decision.mode != "explore":
            continue
        run_episode(phone, decision.chosen.intervention,
                    decision.chosen.skill_id,
                    multiplier=0.3 if poorly else 2.0,
                    correct=0 if poorly else 3,
                    retained=not poorly)


# --- the required proof ---------------------------------------------------

def test_evidence_changes_what_aria_prescribes(phone):
    """1. unknown preferences -> 2. an approach is tried -> 3. outcome
    recorded -> 4. policy changes -> 5. future decisions change -> 6. a
    different approach can take over when the evidence says so."""
    import counterfactual
    import policy
    warm(phone)

    # 1. Aria knows nothing about how this student learns.
    assert not policy.has_evidence(phone)
    assert len({round(e.durable_effectiveness, 9)
                for e in policy.profile(phone)}) == 1
    opening = counterfactual.decide(phone, states(phone), 20,
                                    rng=random.Random(0))
    assert opening.mode == "explore"

    baseline = preference(phone, "worked_example")

    # 2 and 3. Worked examples are tried, and they work.
    for _ in range(6):
        run_episode(phone, "worked_example", "m_equivalent_expr",
                    multiplier=2.4, retained=True)

    # 4. The policy has moved, and it moved because of those episodes.
    estimate = policy.estimate(phone, "worked_example")
    assert estimate.episodes == 6
    assert estimate.multiplier.mean > policy.POP_MULTIPLIER_MEAN
    assert policy.profile(phone)[0].intervention == "worked_example"

    # 5. Future decisions are measurably more likely to use it.
    improved = preference(phone, "worked_example")
    assert improved > baseline
    assert improved >= 0.5

    # 5b. And once the approaches Aria still wanted to test have been tested
    # and found wanting, she stops testing and starts using what works. This
    # is the step that closes the loop: belief becoming behaviour.
    assert preference(phone, "worked_example", believed=False) < 1.0
    try_the_alternatives(phone, poorly=True)
    assert preference(phone, "worked_example", believed=False) >= 0.75

    # 6. Contradicting evidence takes it back off the top. A policy that only
    # ever accumulates confidence is a policy that cannot be wrong.
    for _ in range(12):
        run_episode(phone, "worked_example", "m_equivalent_expr",
                    multiplier=0.1, correct=0, retained=False)
    for _ in range(10):
        run_episode(phone, "socratic", "rw_boundaries", multiplier=2.6,
                    retained=True)

    assert policy.profile(phone)[0].intervention == "socratic"
    assert preference(phone, "worked_example") < improved
    assert preference(phone, "socratic") > 0.4


def test_the_decision_survives_a_restart(phone):
    """Nothing that drives a decision may live only in memory."""
    import counterfactual
    import policy
    warm(phone)
    for _ in range(6):
        run_episode(phone, "worked_example", "m_equivalent_expr",
                    multiplier=2.4, retained=True)

    before = counterfactual.decide(phone, states(phone), 20,
                                   rng=random.Random(3))
    policy._invalidate()
    after = counterfactual.decide(phone, states(phone), 20,
                                  rng=random.Random(3))

    assert after.chosen.intervention == before.chosen.intervention
    assert after.chosen.value == pytest.approx(before.chosen.value)
    assert after.mode == before.mode


def test_knowledge_and_learning_response_both_move_the_answer(phone):
    """The two models are separate and both matter.

    Same learning-response profile, different knowledge state -> different
    skill. Same knowledge state, different learning-response profile ->
    different approach. If either failed, one of the two models would be
    decoration.
    """
    import counterfactual
    warm(phone)
    for _ in range(6):
        run_episode(phone, "worked_example", "m_equivalent_expr",
                    multiplier=2.4, retained=True)

    first = counterfactual.decide(phone, states(phone), 20, rng=random.Random(5))

    # Knowledge changes: master the skill it wanted, and it must move on.
    import mastery
    for i in range(12):
        mastery.record_attempt(phone, first.chosen.skill_id, True,
                               question_id=f"mastered-{i}")
    second = counterfactual.decide(phone, states(phone), 20, rng=random.Random(5))
    assert second.chosen.skill_id != first.chosen.skill_id

    # Learning response changes: another student, same-shaped knowledge, but a
    # different history, gets a different approach.
    other = "15557770000"
    warm(other)
    for _ in range(8):
        run_episode(other, "timed_drill", "m_equivalent_expr", multiplier=2.6,
                    retained=True)
    other_decision = counterfactual.decide(other, states(other), 20,
                                           rng=random.Random(5))
    assert other_decision.greedy.intervention == "timed_drill"
    assert first.greedy.intervention == "worked_example"


def test_retention_alone_can_reverse_a_ranking(phone):
    """Two approaches, identical immediate performance, opposite durability.
    Only the delayed checks distinguish them -- and they must be decisive."""
    import policy
    warm(phone)

    for _ in range(5):
        run_episode(phone, "hint_first", "m_equivalent_expr", multiplier=2.2)
        run_episode(phone, "socratic", "rw_boundaries", multiplier=2.2)

    ranked = [e.intervention for e in policy.profile(phone)]
    top_two = set(ranked[:2])
    assert top_two == {"hint_first", "socratic"}

    for _ in range(5):
        run_episode(phone, "hint_first", "m_equivalent_expr", multiplier=2.2,
                    retained=False)
        run_episode(phone, "socratic", "rw_boundaries", multiplier=2.2,
                    retained=True)

    ranked = [e.intervention for e in policy.profile(phone)]
    assert ranked.index("socratic") < ranked.index("hint_first")


def test_regret_is_recorded_against_the_forecast_that_was_made(phone):
    """Policy feedback has to compare against what was actually predicted at
    the time, not a forecast reconstructed once the answer is known."""
    import counterfactual
    import policy
    from skills import SKILL_BY_ID
    warm(phone)

    decision = counterfactual.decide(phone, states(phone), 20,
                                     rng=random.Random(11))
    chosen = decision.chosen
    episode_id = policy.record_episode(
        phone, chosen.intervention, chosen.skill_id,
        SKILL_BY_ID[chosen.skill_id].domain, chosen.mastery_before,
        chosen.questions,
        expected_multiplier=chosen.expected_multiplier,
        expected_points_per_min=chosen.value,
        decision_mode=decision.mode,
        p_best_at_decision=chosen.p_best_intervention)
    # It went badly.
    policy.close_episode(episode_id, mastery_after=chosen.mastery_before + 0.005,
                         minutes=chosen.minutes, answered=chosen.questions,
                         correct=0, completed=True,
                         points_per_mastery=chosen.points_per_mastery)

    rows = policy.regret_log(phone)
    assert len(rows) == 1
    assert rows[0]["expected"] == pytest.approx(chosen.value)
    assert rows[0]["regret"] > 0
    assert rows[0]["mode"] == decision.mode


# --- autonomy -------------------------------------------------------------

def test_aria_proposes_an_experiment_when_she_is_unsure(phone):
    import autonomy
    warm(phone)
    triggers = {d.trigger for d in autonomy.evaluate(phone)}
    assert "policy_experiment" in triggers

    decision = next(d for d in autonomy.evaluate(phone)
                    if d.trigger == "policy_experiment")
    assert "don't know yet" in decision.reason.lower()
    assert "likely best" in decision.evidence


def test_a_due_check_outranks_an_experiment(phone):
    """One question that settles whether a whole approach is believed beats
    starting something new."""
    import autonomy
    import policy
    import retention
    from skills import SKILL_BY_ID
    warm(phone)

    episode_id = policy.record_episode(
        phone, "worked_example", "m_equivalent_expr",
        SKILL_BY_ID["m_equivalent_expr"].domain, 0.3, 3)
    policy.close_episode(episode_id, 0.6, 9.0, 3, 3, True, points_per_mastery=50.0)
    retention.schedule(phone, episode_id, "worked_example", "m_equivalent_expr",
                       0.6, gain=0.3, delay_days=-1.0)

    scored = {d.trigger: d.score for d in autonomy.evaluate(phone)}
    assert "retention_check" in scored
    assert scored["retention_check"] > scored.get("policy_experiment", 0)


def test_a_confident_student_is_not_offered_experiments(phone):
    import autonomy
    warm(phone)
    for _ in range(10):
        run_episode(phone, "worked_example", "m_equivalent_expr",
                    multiplier=2.6, retained=True)
    triggers = {d.trigger for d in autonomy.evaluate(phone)}
    assert "policy_experiment" not in triggers


# --- failure recovery -----------------------------------------------------

def test_an_orphaned_open_episode_does_not_block_the_next_decision(phone):
    """A process that dies mid-episode leaves a row with no outcome. It must
    not corrupt the model and must not stop the next session."""
    import counterfactual
    import policy
    from skills import SKILL_BY_ID
    warm(phone)

    policy.record_episode(phone, "socratic", "m_systems",
                          SKILL_BY_ID["m_systems"].domain, 0.25, 3)
    assert policy.open_episode_ids(phone)
    assert policy.multiplier_posterior(phone, "socratic").mean == pytest.approx(
        policy.POP_MULTIPLIER_MEAN)

    decision = counterfactual.decide(phone, states(phone), 20,
                                     rng=random.Random(2))
    assert decision is not None


def test_a_corrupt_session_payload_does_not_take_down_the_reply(phone):
    import database
    import tutor
    database.save_session(phone, "idle", "{not json", None)

    out = []
    tutor.handle(phone, "GO", out.append)
    assert out


def test_an_old_two_element_plan_is_widened_not_fatal(phone):
    """Sessions written before the plan carried an intervention must still
    load, or the first deploy drops every conversation in flight."""
    import json

    import database
    from conversation import get_session

    database.save_session(
        phone, "idle",
        json.dumps({"minutes": 20, "plan_alloc": [["rw_boundaries", 3]]}), None)
    session = get_session(phone)
    assert session.plan_alloc == [["rw_boundaries", None, 3]]

    import tutor
    out = []
    tutor.handle(phone, "GO", out.append)
    assert out
