"""Delayed checks: scheduling, resolution, and what they do to the ranking."""

from datetime import timedelta

import pytest

from conftest import run_episode, seed_attempts


def open_and_close(phone, intervention="worked_example", skill="m_systems",
                   gain=0.3):
    import policy
    from skills import SKILL_BY_ID
    before = 0.3
    episode_id = policy.record_episode(
        phone, intervention, skill, SKILL_BY_ID[skill].domain, before, 3)
    policy.close_episode(episode_id, mastery_after=before + gain, minutes=9.0,
                         answered=3, correct=2, completed=True,
                         points_per_mastery=50.0)
    return episode_id, before + gain


def test_a_learning_episode_books_a_check(phone):
    import retention
    episode_id, after = open_and_close(phone)
    probe_id = retention.schedule(phone, episode_id, "worked_example",
                                  "m_systems", after, gain=0.3)
    assert probe_id
    probe = retention.get(probe_id)
    assert probe.skill_id == "m_systems"
    assert probe.mastery_at_close == pytest.approx(after)
    assert not probe.is_resolved


def test_an_episode_that_taught_nothing_books_nothing(phone):
    """A check costs the student a minute two days from now. Spending it to
    measure the retention of a gain that never happened is worse than not
    measuring."""
    import retention
    episode_id, after = open_and_close(phone, gain=0.001)
    assert retention.schedule(phone, episode_id, "worked_example", "m_systems",
                              after, gain=0.001) is None


def test_a_check_is_not_due_before_its_time(phone):
    import retention
    episode_id, after = open_and_close(phone)
    retention.schedule(phone, episode_id, "worked_example", "m_systems", after,
                       gain=0.3)
    assert retention.due(phone) == []
    assert len(retention.pending(phone)) == 1


def test_a_check_comes_due_once_the_days_pass(phone):
    import retention
    from mastery import _now
    episode_id, after = open_and_close(phone)
    retention.schedule(phone, episode_id, "worked_example", "m_systems", after,
                       gain=0.3, delay_days=2.0)
    later = _now() + timedelta(days=2.5)
    assert len(retention.due(phone, now=later)) == 1


def test_resolution_is_recorded_and_stops_the_check_recurring(phone):
    import retention
    from mastery import _now
    episode_id, after = open_and_close(phone)
    probe_id = retention.schedule(phone, episode_id, "worked_example",
                                  "m_systems", after, gain=0.3)
    retention.resolve(probe_id, True)

    probe = retention.get(probe_id)
    assert probe.is_resolved
    assert probe.correct == 1
    later = _now() + timedelta(days=10)
    assert retention.due(phone, now=later) == []
    assert retention.outcome_counts(phone, "worked_example") == (1, 0)


def test_a_retired_check_is_not_counted_as_forgotten(phone):
    """When there is nothing left in the bank to ask with, the student never
    saw a question -- so the approach that taught them has not been shown to
    have failed. Recording that as a loss would be the easy lie."""
    import retention
    episode_id, after = open_and_close(phone)
    probe_id = retention.schedule(phone, episode_id, "worked_example",
                                  "m_systems", after, gain=0.3)
    retention.retire(probe_id)

    assert retention.outcome_counts(phone, "worked_example") == (0, 0)
    assert retention.get(probe_id).is_resolved
    assert retention.pending(phone) == []


def test_retention_counts_are_per_intervention(phone):
    import retention
    a, after_a = open_and_close(phone, "worked_example", "m_systems")
    b, after_b = open_and_close(phone, "timed_drill", "rw_boundaries")
    retention.resolve(retention.schedule(phone, a, "worked_example",
                                        "m_systems", after_a, gain=0.3), True)
    retention.resolve(retention.schedule(phone, b, "timed_drill",
                                        "rw_boundaries", after_b, gain=0.3), False)

    assert retention.outcome_counts(phone, "worked_example") == (1, 0)
    assert retention.outcome_counts(phone, "timed_drill") == (0, 1)
    assert retention.outcome_counts(phone) == (1, 1)


def test_a_failed_check_demotes_the_intervention_that_caused_it(phone, rng):
    """The mechanism the whole retention layer exists for: shallow learning is
    detected days later and changes what Aria prescribes next."""
    import counterfactual
    import mastery
    import policy

    seed_attempts(phone, "m_equivalent_expr", 6, 2)

    for _ in range(6):
        run_episode(phone, "hint_first", "m_equivalent_expr", multiplier=2.4)
    before = policy.estimate(phone, "hint_first").durable_effectiveness
    ranked_before = [e.intervention for e in policy.profile(phone)]
    assert ranked_before[0] == "hint_first"

    # Two days later, none of it is there.
    import retention
    for _ in range(5):
        episode_id, after = open_and_close(phone, "hint_first",
                                           "m_equivalent_expr")
        probe_id = retention.schedule(phone, episode_id, "hint_first",
                                      "m_equivalent_expr", after, gain=0.3)
        retention.resolve(probe_id, False)

    after_estimate = policy.estimate(phone, "hint_first")
    assert after_estimate.retention.mean < 0.4
    assert after_estimate.durable_effectiveness < before
    assert policy.profile(phone)[0].intervention != "hint_first"


def test_summary_reports_what_is_outstanding(phone):
    import retention
    episode_id, after = open_and_close(phone)
    probe_id = retention.schedule(phone, episode_id, "worked_example",
                                  "m_systems", after, gain=0.3)
    assert retention.summary(phone)["pending"] == 1
    retention.resolve(probe_id, True)
    summary = retention.summary(phone)
    assert summary == {"resolved": 1, "kept": 1, "lost": 0,
                       "pending": 0, "due_now": 0}
