"""Score projection and study planning.

The mastery model in `mastery.py` is generative: given a student's per-skill
mastery vector you can *simulate them sitting the SAT*. That unlocks three
things a normal tutor app cannot do:

  1. project() ......... a score distribution, not a point estimate
  2. marginal_gains() .. counterfactual "what is each skill actually worth?"
  3. plan_session() .... solve a knapsack for the best use of the minutes the
                         student actually has tonight

(3) is the product. Free SAT content is everywhere; what a paid tutor sells is
knowing which forty minutes to spend. That is the part this file gives away.

Caveat, stated honestly because the README repeats it: the real digital SAT is
module-adaptive, so Module 2 difficulty depends on Module 1 performance. This
models the non-adaptive approximation and converts raw->scaled through a
published-style anchor table. It is a prep-grade estimate, not a College Board
score report, and the student-facing copy always shows a range.
"""

import random
from dataclasses import dataclass

from mastery import SkillState, decay_by_days, expected_step
from skills import (
    SECTION_MATH,
    SECTION_RW,
    SECTION_QUESTION_COUNT,
    SKILL_BY_ID,
    SKILLS,
    question_weight,
    skills_in_section,
)

# Proportion-correct -> section scaled score (200-800). Piecewise linear through
# anchors that approximate published digital SAT raw-to-scaled behaviour: the
# curve is compressed at the tails and near-linear through the middle.
_SCALE_ANCHORS = [
    (0.00, 200), (0.25, 320), (0.40, 420), (0.50, 480),
    (0.60, 540), (0.70, 600), (0.80, 670), (0.90, 740), (1.00, 800),
]

# Effective minutes per practice question, including reading Aria's feedback.
MINUTES_PER_QUESTION = 2.0

# A single session aims for "solid" (0.90). Long-horizon projections have to
# aim higher, because 0.90 across every skill only projects to about 1390 --
# targeting 0.90 would put anything above that out of reach by construction.
SESSION_TARGET_MASTERY = 0.90
HORIZON_TARGET_MASTERY = 0.97


def _scale(proportion: float) -> float:
    p = min(max(proportion, 0.0), 1.0)
    for i in range(len(_SCALE_ANCHORS) - 1):
        p0, s0 = _SCALE_ANCHORS[i]
        p1, s1 = _SCALE_ANCHORS[i + 1]
        if p0 <= p <= p1:
            t = 0.0 if p1 == p0 else (p - p0) / (p1 - p0)
            return s0 + t * (s1 - s0)
    return 800.0


def _round10(x: float) -> int:
    """SAT scores are reported in multiples of 10."""
    return int(round(x / 10.0) * 10)


@dataclass
class Projection:
    total: int              # median simulated total, 400-1600
    total_low: int          # 10th percentile
    total_high: int         # 90th percentile
    rw: int
    math: int
    samples: list[int]

    def probability_at_least(self, target: int) -> float:
        if not self.samples:
            return 0.0
        return sum(1 for s in self.samples if s >= target) / len(self.samples)


def expected_section_score(states: dict[str, SkillState], section: str) -> float:
    """Analytic expected scaled score for one section. Fast, no sampling."""
    total_q = SECTION_QUESTION_COUNT[section]
    expected_correct = sum(
        question_weight(s.id) * states[s.id].p_correct()
        for s in skills_in_section(section)
    )
    return _scale(expected_correct / total_q)


def expected_total(states: dict[str, SkillState]) -> float:
    return (expected_section_score(states, SECTION_RW)
            + expected_section_score(states, SECTION_MATH))


def project(states: dict[str, SkillState], n_sims: int = 1000,
            seed: int | None = None) -> Projection:
    """Monte Carlo: sit `n_sims` simulated SATs using this mastery vector."""
    rng = random.Random(seed)
    totals: list[int] = []
    rw_scores: list[float] = []
    math_scores: list[float] = []

    # Pre-compute integer question allocations so every simulated test has the
    # right shape (54 R&W / 44 Math) rather than fractional questions.
    allocations = {}
    for section in (SECTION_RW, SECTION_MATH):
        alloc = []
        for s in skills_in_section(section):
            alloc.append((s.id, question_weight(s.id)))
        allocations[section] = alloc

    for _ in range(n_sims):
        section_scaled = {}
        for section in (SECTION_RW, SECTION_MATH):
            correct = 0.0
            for skill_id, weight in allocations[section]:
                p = states[skill_id].p_correct()
                # Split the fractional weight into a guaranteed integer part
                # plus a Bernoulli remainder, then sample each question.
                whole = int(weight)
                frac = weight - whole
                n_q = whole + (1 if rng.random() < frac else 0)
                for _q in range(n_q):
                    if rng.random() < p:
                        correct += 1
            scaled = _scale(correct / SECTION_QUESTION_COUNT[section])
            section_scaled[section] = scaled
        rw_scores.append(section_scaled[SECTION_RW])
        math_scores.append(section_scaled[SECTION_MATH])
        totals.append(_round10(section_scaled[SECTION_RW] + section_scaled[SECTION_MATH]))

    totals.sort()
    n = len(totals)
    return Projection(
        total=totals[n // 2],
        total_low=totals[int(n * 0.10)],
        total_high=totals[int(n * 0.90)],
        rw=_round10(sum(rw_scores) / n),
        math=_round10(sum(math_scores) / n),
        samples=totals,
    )


@dataclass
class SkillGain:
    skill_id: str
    name: str
    section: str
    domain: str
    current_mastery: float
    target_mastery: float
    points_gained: float     # expected scaled-score points from closing the gap
    questions_needed: int
    minutes_needed: float
    points_per_minute: float


MAX_QUESTIONS_PER_SKILL = 40


def questions_to_reach(p_current: float, p_target: float) -> int:
    """How many practice questions to lift mastery from current to target.

    Steps the *expected* BKT update, so this accounts for the student getting
    some of them wrong. Assuming every answer is correct would understate the
    cost of every skill and make the whole plan over-promise.
    """
    if p_current >= p_target:
        return 0
    p = p_current
    for n in range(1, MAX_QUESTIONS_PER_SKILL + 1):
        nxt = expected_step(p)
        if nxt <= p:  # converged below target: no further progress available
            return MAX_QUESTIONS_PER_SKILL
        p = nxt
        if p >= p_target:
            return n
    return MAX_QUESTIONS_PER_SKILL


def marginal_gains(states: dict[str, SkillState],
                   target_mastery: float = SESSION_TARGET_MASTERY,
                   subject_filter: str | None = None) -> list[SkillGain]:
    """Counterfactual value of each skill.

    For every skill, re-score the whole test as if that one skill were mastered
    and leave everything else untouched. The difference is what that skill is
    worth in scaled points. Sorting by points-per-minute gives the true study
    priority, which is often *not* the weakest skill -- a skill you are bad at
    but that appears 0.7 times per test is worth less than a skill you are
    mediocre at that appears 7 times.
    """
    from skills import skills_for_subject

    baseline = expected_total(states)
    allowed = None
    if subject_filter:
        allowed = {s.id for s in skills_for_subject(subject_filter)}

    gains: list[SkillGain] = []
    for skill in SKILLS:
        if allowed is not None and skill.id not in allowed:
            continue
        state = states[skill.id]
        if state.p_mastery >= target_mastery:
            continue

        boosted = dict(states)
        boosted[skill.id] = SkillState(
            skill_id=skill.id,
            p_mastery=target_mastery,
            p_mastery_raw=target_mastery,
            attempts=state.attempts,
            correct=state.correct,
            last_seen=state.last_seen,
        )
        delta = expected_total(boosted) - baseline

        n_q = questions_to_reach(state.p_mastery, target_mastery)
        minutes = n_q * MINUTES_PER_QUESTION
        gains.append(SkillGain(
            skill_id=skill.id,
            name=skill.name,
            section=skill.section,
            domain=skill.domain,
            current_mastery=state.p_mastery,
            target_mastery=target_mastery,
            points_gained=delta,
            questions_needed=n_q,
            minutes_needed=minutes,
            points_per_minute=(delta / minutes) if minutes > 0 else 0.0,
        ))

    gains.sort(key=lambda g: -g.points_per_minute)
    return gains


@dataclass
class StudyPlan:
    minutes_available: int
    skills: list[SkillGain]
    expected_points: float
    total_questions: int
    total_minutes: float

    @property
    def is_empty(self) -> bool:
        return not self.skills


def plan_session(states: dict[str, SkillState], minutes_available: int,
                 subject_filter: str | None = None,
                 target_mastery: float = SESSION_TARGET_MASTERY) -> StudyPlan:
    """Pick the set of skills that maximises expected score gain in the time given.

    This is a 0/1 knapsack: each skill has a minute cost and a point value, and
    we cannot spend more minutes than the student said they have. Solved exactly
    by DP -- with ~29 skills and a two-hour ceiling the table is tiny.
    """
    gains = marginal_gains(states, target_mastery, subject_filter)
    if not gains or minutes_available <= 0:
        return StudyPlan(minutes_available, [], 0.0, 0, 0.0)

    # Work in whole minutes for the DP table.
    items = [(g, max(1, int(round(g.minutes_needed)))) for g in gains]
    cap = int(minutes_available)

    # dp[c] = best achievable points using capacity c; keep back-pointers.
    dp = [0.0] * (cap + 1)
    chosen: list[list[int]] = [[] for _ in range(cap + 1)]

    for idx, (gain, cost) in enumerate(items):
        if cost > cap:
            continue
        for c in range(cap, cost - 1, -1):
            candidate = dp[c - cost] + gain.points_gained
            if candidate > dp[c]:
                dp[c] = candidate
                chosen[c] = chosen[c - cost] + [idx]

    best_c = max(range(cap + 1), key=lambda c: dp[c])
    picked = [items[i][0] for i in chosen[best_c]]
    # Present in study order: biggest win first.
    picked.sort(key=lambda g: -g.points_gained)

    return StudyPlan(
        minutes_available=minutes_available,
        skills=picked,
        expected_points=dp[best_c],
        total_questions=sum(g.questions_needed for g in picked),
        total_minutes=sum(g.minutes_needed for g in picked),
    )


def next_skill(states: dict[str, SkillState], subject_filter: str | None = None,
               minutes_available: int = 30) -> str | None:
    """The single skill to practise right now.

    Prefers the current plan's top item, so consecutive questions follow a
    coherent strategy instead of hopping randomly around the syllabus.
    """
    plan = plan_session(states, minutes_available, subject_filter)
    if plan.skills:
        return plan.skills[0].skill_id
    gains = marginal_gains(states, subject_filter=subject_filter)
    return gains[0].skill_id if gains else None


def days_to_target(states: dict[str, SkillState], target_score: int,
                   minutes_per_day: int = 20) -> int | None:
    """Days of consistent practice needed to reach a target score.

    Walks the plan forward day by day -- applying both the gains the student
    earns and the forgetting they suffer on everything they skip -- until the
    projection clears the target. Returns None if the target is out of reach
    within a year at this pace.
    """
    sim_states = dict(states)
    questions_per_day = max(1, int(minutes_per_day / MINUTES_PER_QUESTION))

    for day in range(1, 366):
        if expected_total(sim_states) >= target_score:
            return day - 1

        # A day passes for every skill, whether or not it gets practised.
        # Without this the projection quietly assumes perfect retention and
        # promises gains that evaporate between sessions.
        for skill_id, st in sim_states.items():
            decayed = decay_by_days(st.p_mastery, st.correct, 1.0)
            if decayed != st.p_mastery:
                sim_states[skill_id] = SkillState(
                    skill_id=skill_id,
                    p_mastery=decayed,
                    p_mastery_raw=decayed,
                    attempts=st.attempts,
                    correct=st.correct,
                    last_seen=st.last_seen,
                )

        gains = marginal_gains(sim_states, target_mastery=HORIZON_TARGET_MASTERY)
        if not gains:
            return day - 1

        budget = questions_per_day
        for gain in gains:
            if budget <= 0:
                break
            spend = min(budget, gain.questions_needed)
            budget -= spend
            state = sim_states[gain.skill_id]
            p = state.p_mastery
            expected_correct = 0.0
            for _ in range(spend):
                expected_correct += state.p_correct()
                p = expected_step(p)
            sim_states[gain.skill_id] = SkillState(
                skill_id=gain.skill_id,
                p_mastery=p,
                p_mastery_raw=p,
                attempts=state.attempts + spend,
                # Only successes lengthen the retention half-life, so count the
                # expected number of them rather than assuming a clean sweep.
                correct=state.correct + int(round(expected_correct)),
                last_seen=state.last_seen,
            )
    return None
