"""The tutor as execution layer.

The claim these tests defend is that the policy engine's choice actually
reaches the student. An intervention the tutor renders identically to every
other one is an intervention whose effect cannot be measured, which would make
everything in policy.py an elaborate way of estimating noise.
"""

import pytest

from conftest import seed_attempts


def replies(phone, message):
    import tutor
    out = []
    tutor.handle(phone, message, out.append)
    return out


def onboard(phone, minutes=20):
    import bank
    if not bank.is_available():
        pytest.skip("question_bank.json not present")
    for message in ("hi", "Sam", "1300", f"{minutes} minutes"):
        replies(phone, message)


def start_block(phone, intervention, skill="rw_boundaries", questions=3):
    """Force a specific intervention, so rendering can be tested directly."""
    import mastery
    import tutor
    from conversation import get_session

    session = get_session(phone)
    session.plan_alloc = [[skill, intervention, questions]]
    session.save()
    out = []
    tutor._start_episode(phone, session, mastery.get_all_states(phone), out.append)
    return out


# --- the interventions render differently --------------------------------

def test_worked_example_solves_one_before_asking(phone):
    onboard(phone)
    out = start_block(phone, "worked_example")
    assert out
    assert "let me do one first" in out[0].lower()
    assert "Answer:" in out[0]
    assert "Your turn" in out[0]


def test_cold_retrieval_gives_no_scaffolding(phone):
    onboard(phone)
    out = start_block(phone, "retrieval_practice")
    assert "no warm-up" in out[0].lower()
    assert "Answer:" not in out[0]


def test_explanation_states_the_rule(phone):
    import interventions
    onboard(phone)
    out = start_block(phone, "direct_explanation")
    assert interventions.skill_principle("rw_boundaries")[:40] in out[0]


def test_hint_first_attaches_a_hint_to_the_question(phone):
    import interventions
    onboard(phone)
    out = start_block(phone, "hint_first")
    question = out[-1]
    assert "Hint:" in question
    assert interventions.skill_hint("rw_boundaries") in question


def test_timed_drill_states_a_clock(phone):
    onboard(phone)
    out = start_block(phone, "timed_drill", questions=5)
    assert "seconds each" in out[0]


def test_socratic_withholds_the_options_until_the_student_answers(phone):
    from conversation import TutoringState, get_session
    onboard(phone)
    out = start_block(phone, "socratic")
    question = out[-1]
    assert "what is this question actually asking" in question.lower()
    assert "\nA) " not in question

    assert get_session(phone).state == TutoringState.AWAITING_LEAD_IN
    shown = replies(phone, "it wants me to join two sentences")
    assert "A) " in shown[0]
    assert get_session(phone).state == TutoringState.AWAITING_ANSWER


def test_misconception_repair_names_the_students_own_error(phone):
    import mastery
    onboard(phone)
    for i in range(3):
        mastery.record_attempt(phone, "m_equivalent_expr", False,
                               question_id=f"seed-mis-{i}",
                               misconception="sign_error")
    out = start_block(phone, "misconception_repair", skill="m_equivalent_expr")
    assert "trap" in out[0].lower()


def test_two_interventions_produce_different_openings(phone):
    """The property everything else rests on."""
    onboard(phone)
    a = start_block(phone, "worked_example")[0]
    onboard(phone + "9")
    b = start_block(phone + "9", "retrieval_practice")[0]
    assert a != b


# --- the episode lifecycle ------------------------------------------------

def test_answering_a_block_closes_the_episode_and_records_evidence(phone):
    import bank
    import policy
    from conversation import get_session

    onboard(phone)
    start_block(phone, "retrieval_practice", questions=2)

    for _ in range(2):
        session = get_session(phone)
        question = bank.get(session.current_question_id)
        replies(phone, bank.index_to_letter(question["correct_index"]))
        if get_session(phone).state.value == "idle":
            replies(phone, "GO")

    episodes = policy.episodes_for(phone, "retrieval_practice")
    assert episodes
    assert episodes[0].answered >= 2
    assert episodes[0].learning_multiplier is not None


def test_stopping_mid_episode_still_closes_it(phone):
    import bank
    import policy
    from conversation import get_session

    onboard(phone)
    start_block(phone, "retrieval_practice", questions=4)
    session = get_session(phone)
    question = bank.get(session.current_question_id)
    replies(phone, bank.index_to_letter(question["correct_index"]))

    assert policy.open_episode_ids(phone)
    replies(phone, "STOP")
    assert not policy.open_episode_ids(phone)


def test_replanning_closes_the_block_it_abandons(phone):
    import bank
    import policy
    from conversation import get_session

    onboard(phone)
    start_block(phone, "retrieval_practice", questions=4)
    session = get_session(phone)
    replies(phone, bank.index_to_letter(
        bank.get(session.current_question_id)["correct_index"]))

    assert policy.open_episode_ids(phone)
    replies(phone, "PLAN")
    assert not policy.open_episode_ids(phone)


def test_an_episode_started_by_the_tutor_carries_a_forecast(phone):
    """Regression. `regret_log` passed its own unit tests while being empty in
    every real session, because the tutor opened episodes without recording
    what the engine had predicted. A feature that only works when a test calls
    it directly is not implemented."""
    import bank
    import policy
    from conversation import get_session

    onboard(phone)
    start_block(phone, "retrieval_practice", questions=2)

    open_ids = policy.open_episode_ids(phone)
    assert open_ids
    episode = policy.episode(open_ids[0])
    assert episode.expected_points_per_min is not None
    assert episode.expected_points_per_min > 0
    assert episode.expected_multiplier is not None

    for _ in range(10):
        session = get_session(phone)
        if session.current_question_id:
            question = bank.get(session.current_question_id)
            replies(phone, bank.index_to_letter(question["correct_index"]))
        else:
            replies(phone, "GO")
        if policy.episodes_for(phone):
            break

    rows = policy.regret_log(phone)
    assert rows, "a completed session must produce a regret row"
    assert rows[0]["expected"] is not None
    assert rows[0]["observed"] is not None


def test_a_demonstrated_item_is_never_served_as_a_question(phone):
    """Serving back the item Aria just solved would measure whether the student
    remembers the last five minutes."""
    import mastery
    import policy
    onboard(phone)
    start_block(phone, "worked_example")

    shown = policy.shown_item_ids(phone)
    assert shown
    answered = mastery.seen_question_ids(phone)
    assert not (shown & answered)


# --- retention probes through the conversation ---------------------------

def test_a_due_check_is_asked_before_new_work(phone):
    import bank
    import mastery
    import policy
    import retention
    import tutor
    from conversation import get_session
    from skills import SKILL_BY_ID

    onboard(phone)
    seed_attempts(phone, "rw_boundaries", 4, 3)

    episode_id = policy.record_episode(
        phone, "worked_example", "rw_boundaries",
        SKILL_BY_ID["rw_boundaries"].domain, 0.3, 3)
    policy.close_episode(episode_id, 0.6, 9.0, 3, 3, True, points_per_mastery=50.0)
    probe_id = retention.schedule(phone, episode_id, "worked_example",
                                  "rw_boundaries", 0.6, gain=0.3,
                                  delay_days=-1.0)
    assert retention.due(phone)

    session = get_session(phone)
    session.plan_alloc = []
    session.save()
    out = []
    tutor.advance(phone, session, out.append)

    assert any("stuck" in line.lower() for line in out)
    assert get_session(phone).probe_id == probe_id


def test_answering_a_check_resolves_it_and_moves_retention(phone):
    import bank
    import policy
    import retention
    import tutor
    from conversation import get_session
    from skills import SKILL_BY_ID

    onboard(phone)
    seed_attempts(phone, "rw_boundaries", 4, 3)
    episode_id = policy.record_episode(
        phone, "worked_example", "rw_boundaries",
        SKILL_BY_ID["rw_boundaries"].domain, 0.3, 3)
    policy.close_episode(episode_id, 0.6, 9.0, 3, 3, True, points_per_mastery=50.0)
    probe_id = retention.schedule(phone, episode_id, "worked_example",
                                  "rw_boundaries", 0.6, gain=0.3,
                                  delay_days=-1.0)

    session = get_session(phone)
    session.plan_alloc = []
    session.save()
    tutor.advance(phone, session, lambda _t: None)

    session = get_session(phone)
    question = bank.get(session.current_question_id)
    before = policy.retention_posterior(phone, "worked_example").mean
    replies(phone, bank.index_to_letter(question["correct_index"]))

    assert retention.get(probe_id).is_resolved
    assert retention.get(probe_id).correct == 1
    assert policy.retention_posterior(phone, "worked_example").mean > before
    assert get_session(phone).probe_id is None


# --- what the student is told --------------------------------------------

def test_the_plan_never_uses_policy_jargon(phone):
    onboard(phone)
    text = "\n".join(replies(phone, "PLAN")).lower()
    for word in ("posterior", "thompson", "bayes", "bandit", "multiplier",
                 "p_best", "beta", "prior", "variance"):
        assert word not in text


def test_the_profile_is_withheld_until_there_is_evidence(phone):
    import tutor
    onboard(phone)
    text = "\n".join(replies(phone, "PROFILE")).lower()
    assert "still learning" in text
    assert "best for you" not in text


def test_the_profile_appears_once_evidence_exists(phone):
    from conftest import run_episode
    onboard(phone)
    for _ in range(4):
        run_episode(phone, "worked_example", "m_systems", multiplier=2.0,
                    retained=True)
    text = "\n".join(replies(phone, "PROFILE"))
    assert "Best for you so far" in text
    assert "not a personality type" in text


def test_a_new_student_is_told_aria_does_not_know_yet(phone):
    onboard(phone)
    text = "\n".join(replies(phone, "PLAN")).lower()
    assert "don't yet know" in text or "still working out" in text
