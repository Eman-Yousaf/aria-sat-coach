"""Test fixtures.

Every test runs against its own SQLite file in a temp directory. The modules
read `config.DB_PATH` at call time rather than at import, so pointing that at a
fresh path per test is enough to isolate them -- no mocking, and the code under
test is the code that ships.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(autouse=True)
def db(tmp_path, monkeypatch):
    """A private database for one test."""
    import config
    path = str(tmp_path / "test.db")
    monkeypatch.setattr(config, "DB_PATH", path)

    # The modules bound DB_PATH at import time, so rebind each of them.
    for name in ("mastery", "student", "autonomy", "policy", "retention",
                 "database"):
        module = __import__(name)
        if hasattr(module, "DB_PATH"):
            monkeypatch.setattr(module, "DB_PATH", path)

    import policy
    from database import init_db
    init_db()
    policy._invalidate()

    import conversation
    conversation._sessions.clear()
    yield path
    policy._invalidate()
    conversation._sessions.clear()


@pytest.fixture
def phone():
    return "15551230000"


@pytest.fixture
def rng():
    import random
    return random.Random(1234)


def seed_attempts(phone, skill_id, n, correct):
    """Give a student a real answer history on one skill."""
    import mastery
    for i in range(n):
        mastery.record_attempt(phone, skill_id, i < correct,
                               question_id=f"seed-{skill_id}-{i}")


def run_episode(phone, intervention, skill_id, multiplier, questions=3,
                correct=2, retained=None, minutes=None):
    """Synthesise one closed episode with a chosen observed multiplier.

    Drives the real `policy.close_episode`, so the observation it produces is
    computed by the shipping normalisation rather than written in by hand -- a
    test that wrote the multiplier directly would pass even if the formula were
    removed.
    """
    import mastery
    import policy
    import retention
    from interventions import INTERVENTION_BY_ID
    from skills import SKILL_BY_ID

    state = mastery.get_state(phone, skill_id)
    before = state.p_mastery
    step = policy.baseline_step(before)
    iv = INTERVENTION_BY_ID[intervention]

    episode_id = policy.record_episode(
        phone, intervention, skill_id, SKILL_BY_ID[skill_id].domain,
        before, questions)
    after = min(0.98, before + multiplier * questions * step)
    policy.close_episode(
        episode_id, mastery_after=after,
        minutes=minutes if minutes is not None else iv.minutes_for(questions),
        answered=questions, correct=correct, completed=True,
        points_per_mastery=50.0)

    if retained is not None:
        probe_id = retention.schedule(phone, episode_id, intervention, skill_id,
                                      after, gain=after - before)
        if probe_id:
            retention.resolve(probe_id, retained)
    return episode_id
