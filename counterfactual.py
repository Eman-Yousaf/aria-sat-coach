"""Choosing between interventions that were not run.

`simulator.py` already answers "which skill is worth the most per minute". That
was the old product. It is only half the question, because a minute spent on a
skill is not a fixed quantity of learning -- it depends entirely on what Aria
does with it, and what Aria should do with it depends on the student.

So this module ranks *actions*, where an action is a triple:

    (skill, intervention, duration)

For each one it estimates the expected durable score gain, carries the
uncertainty explicitly, and picks -- sometimes the option it believes is best,
and sometimes, deliberately, one it is unsure about.

--------------------------------------------------------------------------
The value model
--------------------------------------------------------------------------

    gain      = min(m * q * step(p), headroom)       mastery this would buy
    durable   = gain * rho                           what survives to test day
    points    = durable * dScore/dMastery * eta      scaled SAT points
    value     = points / minutes                     the ranking quantity

where, from policy.py's posteriors for this student and this intervention:

    m ........ learning multiplier vs an average question   (Normal)
    rho ...... share retained at a delayed check            (Beta)
    eta ...... probability the episode is completed at all  (Beta)

and q is the number of questions the intervention fits into the budget,
step(p) is what one average question buys at the student's current mastery, and
dScore/dMastery is the existing simulator's counterfactual: re-score the whole
exam with this one skill nudged and hold everything else fixed. That last term
is the reason a skill the student is *better* at can still win -- it prices how
often the skill actually appears on the test.

Minutes appear once, as the denominator. That is the asymmetry that makes the
ranking mean something: an intervention with four minutes of setup has to earn
those minutes back in learning, and a fast one that teaches poorly gets no
credit for being fast. Modelling the numerator per-minute instead would cancel
the two and make every intervention identical, which is exactly the bug this
formula is written the way it is to avoid.

`headroom` is what stops the model promising gains above mastery 1.0, which is
where an unclamped linear rate model quietly starts printing money.

--------------------------------------------------------------------------
Uncertainty, and what it is for
--------------------------------------------------------------------------

Every term above is a posterior, not a number. Drawing one joint sample of
(lambda, rho, eta) for every candidate and taking the argmax gives one opinion
about what is best; doing it a few hundred times gives the *probability* each
candidate is best. Two things fall out of that single mechanism:

  * an honest interval to show, instead of a decimal point pretending to be a
    measurement
  * Thompson sampling, which is the exploration policy -- when Aria is unsure,
    the draws disagree, and a candidate she has barely tried gets chosen in
    proportion to how plausibly it might be the best one

Nothing here is causal inference. These are model-based estimates from one
student's history, and the alternatives shown alongside the chosen action are
labelled as estimates because that is what they are: nobody ran them.
"""

import math
import random
from dataclasses import dataclass, field

import interventions as iv_mod
import mastery
import policy
import retention
import simulator
from interventions import INTERVENTION_BY_ID, Intervention
from skills import SKILL_BY_ID

__all__ = [
    "Candidate", "InterventionDecision", "Block", "PolicyPlan",
    "points_per_mastery", "candidates", "rank", "decide", "plan",
]

# Draws for the Thompson pass. Enough that p_best is stable to about a
# percentage point, cheap enough to run inside a WhatsApp reply.
THOMPSON_DRAWS = 400

# Above this probability of being best, Aria stops shopping around. Set below
# the conventional 0.95 on purpose: this is a study session, not a drug trial,
# and the cost of occasionally running the second-best intervention on a
# teenager is ten minutes.
EXPLOIT_THRESHOLD = 0.65

# An experiment has to be able to pay for itself: its *optimistic* estimate --
# the top of its 80% interval -- must reach this share of the front-runner's
# expected value. "Small, low-risk" has to mean something checkable.
#
# Comparing means here instead was a real bug, and an instructive one. A worked
# example spends three minutes before the first question, so under the neutral
# prior its expected value is about two thirds of cold retrieval's. That is
# below any sensible floor, which meant the intervention could never be tried
# -- and could therefore never accumulate the evidence that would have shown it
# was the best one available for that student. An exploration rule that only
# explores options already believed to be good is not an exploration rule.
# Optimism is the entire point: test what could plausibly be best, not what is
# currently expected to be.
EXPERIMENT_VALUE_FLOOR = 0.70

# A candidate needs at least this probability of being best before it is worth
# experimenting on. Below it, the uncertainty is not about *this* option.
EXPERIMENT_MIN_P_BEST = 0.08

# Mastery ceiling used for headroom. Not 1.0: BKT saturates, and the last
# fraction of a percent is not purchasable with practice questions.
MASTERY_CAP = 0.98

# Below this many durable points per minute an episode counts as a failure --
# roughly "twelve minutes of study bought less than a point".
FAILURE_FLOOR_PPM = 0.08

MAX_BLOCKS_PER_SESSION = 2


# --- the score-model bridge ----------------------------------------------

def points_per_mastery(states: dict, skill_id: str, eps: float = 0.05) -> float:
    """Scaled SAT points per unit of mastery on this skill.

    The derivative of the existing projection with respect to one skill. This
    is the only place the learning-policy layer touches the score model, and it
    is deliberately the existing counterfactual rather than a new one -- if the
    two disagreed about what a skill is worth, the plan and the projection
    would be telling the student different stories.
    """
    state = states[skill_id]
    p = state.p_mastery
    bumped = min(MASTERY_CAP, p + eps)
    if bumped <= p:
        return 0.0
    probe = dict(states)
    probe[skill_id] = simulator._with_mastery(state, bumped)
    return (simulator.expected_total(probe) - simulator.expected_total(states)) / (bumped - p)


# --- candidates -----------------------------------------------------------

@dataclass
class Candidate:
    skill_id: str
    skill_name: str
    domain: str
    intervention: str
    intervention_name: str
    student_label: str
    questions: int
    minutes: float

    mastery_before: float
    points_per_mastery: float
    baseline_step: float            # mastery one average question buys here

    expected_multiplier: float      # 1.0 == an average question
    expected_gain: float            # mastery
    expected_retention: float       # 0-1
    expected_engagement: float      # 0-1
    expected_points: float          # durable, scaled SAT points
    value: float                    # expected_points / minutes

    value_low: float = 0.0          # 10th percentile of the posterior draws
    value_high: float = 0.0         # 90th
    p_best: float = 0.0             # among all candidate actions
    p_best_intervention: float = 0.0  # among approaches, marginalised over skills
    p_failure: float = 0.0
    confidence: float = 0.0
    info_value: float = 0.0         # posterior sd this episode would remove
    episodes: int = 0
    retention_checks: int = 0

    @property
    def key(self) -> tuple[str, str]:
        return (self.skill_id, self.intervention)

    @property
    def evidence(self) -> str:
        if self.episodes == 0:
            return "never tried with you"
        bits = [f"{self.episodes} session{'s' if self.episodes != 1 else ''}"]
        if self.retention_checks:
            bits.append(f"{self.retention_checks} delayed check"
                        f"{'s' if self.retention_checks != 1 else ''}, "
                        f"{self.expected_retention:.0%} kept")
        return ", ".join(bits)


def _skill_pool(phone: str, states: dict, subject_filter: str | None,
                max_skills: int, exclude_ids: set[str]) -> list:
    """Skills worth considering, by the existing score-leverage ranking.

    Reusing `marginal_gains` rather than re-deriving it keeps one answer to
    "which skills matter" in the codebase. This layer's job is to decide *how*
    to spend the minutes, not to relitigate where.

    Availability is checked before the top-N cut, not after. Taking the top
    five by score leverage and then discarding the exhausted ones meant a
    student who had worked through their five most valuable skills got an empty
    candidate list and was told there was nothing left to do -- while twenty
    other skills sat in the bank with questions on them.
    """
    import bank

    gains = simulator.marginal_gains(states, subject_filter=subject_filter)
    usable = [g for g in gains if bank.remaining_for(g.skill_id, exclude_ids) > 0]
    return usable[:max_skills]


def _misconception_slugs(phone: str, skill_id: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for m in mastery.recent_misconceptions(phone, skill_id, limit=30):
        counts[m["misconception"]] = counts.get(m["misconception"], 0) + 1
    return counts


def candidates(phone: str, states: dict, minutes_available: float,
               subject_filter: str | None = None,
               max_skills: int = 5,
               exclude_skills: set[str] | None = None,
               exclude_interventions: set[str] | None = None) -> list[Candidate]:
    """Every (skill, intervention, duration) Aria could actually run right now.

    Filtered by feasibility, not by preference: an option that will not fit in
    the minutes the student has, or whose material does not exist, never
    reaches the ranking. Preference is the ranking's job.
    """
    import bank

    exclude_skills = exclude_skills or set()
    exclude_interventions = exclude_interventions or set()
    out: list[Candidate] = []
    seen_ids = mastery.seen_question_ids(phone) | policy.shown_item_ids(phone)

    for gain in _skill_pool(phone, states, subject_filter, max_skills, seen_ids):
        skill_id = gain.skill_id
        if skill_id in exclude_skills:
            continue
        state = states[skill_id]
        skill = SKILL_BY_ID[skill_id]
        ppm = points_per_mastery(states, skill_id)
        if ppm <= 0:
            continue

        slugs = _misconception_slugs(phone, skill_id)
        has_misconception = any(n >= 2 for n in slugs.values())
        has_prior_success = state.correct > 0
        available_q = bank.remaining_for(skill_id, seen_ids)
        if available_q <= 0:
            continue

        for iv in iv_mod.available_for(has_misconception=has_misconception,
                                       has_prior_success=has_prior_success):
            if iv.id in exclude_interventions:
                continue
            n_q = min(iv.default_questions,
                      iv.questions_within(minutes_available),
                      available_q - iv.demo_items)
            if n_q <= 0:
                continue
            minutes = iv.minutes_for(n_q)

            est = policy.estimate(phone, iv.id, skill.domain)
            step = policy.baseline_step(state.p_mastery)
            headroom = max(0.0, MASTERY_CAP - state.p_mastery)
            m = max(est.multiplier.mean, policy.MIN_MULTIPLIER_FOR_VALUE)
            expected_gain = min(m * n_q * step, headroom)
            rho = est.retention.mean
            eta = est.engagement.mean
            points = expected_gain * rho * eta * ppm

            out.append(Candidate(
                skill_id=skill_id, skill_name=skill.name, domain=skill.domain,
                intervention=iv.id, intervention_name=iv.name,
                student_label=iv.student_label,
                questions=n_q, minutes=minutes,
                mastery_before=state.p_mastery, points_per_mastery=ppm,
                baseline_step=step, expected_multiplier=est.multiplier.mean,
                expected_gain=expected_gain, expected_retention=rho,
                expected_engagement=eta, expected_points=points,
                value=points / minutes if minutes > 0 else 0.0,
                confidence=est.confidence, episodes=est.episodes,
                retention_checks=est.retention_checks,
                info_value=_info_value(est.multiplier),
            ))
    return out


def expected_value(phone: str, states: dict, skill_id: str, intervention: str,
                   questions: int) -> tuple[float, float, float]:
    """Price one specific block: (points per minute, multiplier, points/mastery).

    The same arithmetic `candidates()` does, exposed for the tutor. A block
    drawn up several questions ago has to be re-priced against the mastery it
    is actually starting from, and the forecast has to be stored with the
    episode or `regret_log` has nothing to compare an outcome against -- which
    is exactly what happened: the whole regret feature was dark on the live
    path while passing its own unit tests.
    """
    from interventions import INTERVENTION_BY_ID

    state = states[skill_id]
    iv = INTERVENTION_BY_ID[intervention]
    ppm = points_per_mastery(states, skill_id)
    est = policy.estimate(phone, intervention, SKILL_BY_ID[skill_id].domain)

    step = policy.baseline_step(state.p_mastery)
    headroom = max(0.0, MASTERY_CAP - state.p_mastery)
    m = max(est.multiplier.mean, policy.MIN_MULTIPLIER_FOR_VALUE)
    gain = min(m * questions * step, headroom)
    minutes = max(iv.minutes_for(questions), 0.1)
    value = gain * est.retention.mean * est.engagement.mean * ppm / minutes
    return value, est.multiplier.mean, ppm


def _info_value(gain: policy.Gaussian) -> float:
    """Posterior standard deviation one more episode would remove.

    Exact for the conjugate update in policy.py: precisions add, so the
    post-observation sd is determined without needing to know the observation.
    This is what makes "which experiment is worth running" a computation rather
    than a preference.
    """
    tau_after = gain.precision + 1.0 / (policy.OBS_NOISE_SD ** 2)
    return max(0.0, gain.sd - math.sqrt(1.0 / tau_after))


# --- ranking under uncertainty -------------------------------------------

def rank(phone: str, cands: list[Candidate],
         draws: int = THOMPSON_DRAWS,
         rng: random.Random | None = None) -> list[Candidate]:
    """Fill in p_best, the value interval and the failure probability.

    One joint draw per (intervention, domain) posterior per iteration, shared by
    every candidate that reads from it. Drawing independently per candidate
    would let one intervention beat itself on sampling noise across two skills,
    which would then be reported as evidence that Aria knows something.

    Two probabilities come out, and they answer different questions:

      p_best ................. this exact action beats every other action
      p_best_intervention .... this *approach* is the one worth using, summed
                               over whichever skill it would be used on

    The second is the one the exploration policy runs on. With five skills on
    the table, no single action can reach a 65% probability of being best even
    when Aria is certain about the teaching approach -- the probability mass is
    split across skills that are near-substitutes. Thresholding on that would
    keep Aria permanently, and falsely, unsure.
    """
    rng = rng or random
    if not cands:
        return []

    posteriors = {}
    for c in cands:
        pk = (c.intervention, c.domain)
        if pk not in posteriors:
            posteriors[pk] = policy.estimate(phone, c.intervention, c.domain)

    samples: dict[tuple[str, str], list[float]] = {c.key: [] for c in cands}
    wins: dict[tuple[str, str], int] = {c.key: 0 for c in cands}
    iv_wins: dict[str, int] = {}

    for _ in range(draws):
        drawn: dict[tuple[str, str], tuple[float, float, float]] = {}
        best_c, best_val = None, -1e9
        for c in cands:
            pk = (c.intervention, c.domain)
            if pk not in drawn:
                est = posteriors[pk]
                m = max(est.multiplier.sample(rng), 0.0)
                rho = est.retention.sample(rng)
                eta = est.engagement.sample(rng)
                drawn[pk] = (m, rho, eta)
            m, rho, eta = drawn[pk]
            headroom = max(0.0, MASTERY_CAP - c.mastery_before)
            gain = min(m * c.questions * c.baseline_step, headroom)
            value = gain * rho * eta * c.points_per_mastery / c.minutes
            samples[c.key].append(value)
            if value > best_val:
                best_c, best_val = c, value
        if best_c is not None:
            wins[best_c.key] += 1
            iv_wins[best_c.intervention] = iv_wins.get(best_c.intervention, 0) + 1

    for c in cands:
        s = sorted(samples[c.key])
        n = len(s)
        c.value_low = s[int(n * 0.10)]
        c.value_high = s[int(n * 0.90)]
        c.p_best = wins[c.key] / draws
        c.p_best_intervention = iv_wins.get(c.intervention, 0) / draws
        c.p_failure = sum(1 for v in s if v < FAILURE_FLOOR_PPM) / n

    cands.sort(key=lambda c: -c.value)
    return cands


# --- the decision ---------------------------------------------------------

@dataclass
class InterventionDecision:
    chosen: Candidate
    mode: str                      # 'exploit' | 'explore'
    greedy: Candidate              # what pure exploitation would have picked
    alternatives: list[Candidate] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    experiment_note: str | None = None

    @property
    def is_experiment(self) -> bool:
        return self.mode == "explore"

    def student_line(self) -> str:
        """What Aria says out loud. No posteriors, no jargon."""
        if self.is_experiment:
            return (f"I'm still working out what helps you most, so I'm trying "
                    f"something: {self.chosen.student_label.lower()}.")
        return self.chosen.student_label


def decide(phone: str, states: dict, minutes_available: float,
           subject_filter: str | None = None,
           exclude_skills: set[str] | None = None,
           exclude_interventions: set[str] | None = None,
           rng: random.Random | None = None,
           draws: int = THOMPSON_DRAWS) -> InterventionDecision | None:
    """Pick one action, and be able to say why.

    Exploit when one option is clearly best. When nothing is clearly best,
    spend the episode on whichever plausible option would teach the model the
    most -- but only if it costs little enough to be worth the information.
    """
    rng = rng or random
    cands = candidates(phone, states, minutes_available,
                       subject_filter=subject_filter,
                       exclude_skills=exclude_skills,
                       exclude_interventions=exclude_interventions)
    if not cands:
        return None
    cands = rank(phone, cands, draws=draws, rng=rng)

    greedy = cands[0]
    top_p = greedy.p_best_intervention
    reasons: list[str] = []

    # Score leverage first: this is the part the old engine already got right,
    # and it is still the largest term in the product.
    reasons.append(
        f"{greedy.skill_name} is worth {greedy.points_per_mastery:.0f} scaled "
        f"points per unit of mastery on this test - the highest available.")

    if top_p >= EXPLOIT_THRESHOLD:
        chosen, mode, note = greedy, "exploit", None
        reasons.append(
            f"{greedy.intervention_name} is the approach your history supports: "
            f"{greedy.evidence}, and it comes out ahead in {top_p:.0%} of the "
            f"simulations.")
    else:
        # Genuinely unsure which approach is best. Spend the session on
        # whichever plausible one would sharpen the estimate most -- provided
        # it is not much worse than the front-runner, because an experiment
        # that costs the student real points is not low-risk.
        contenders = [
            c for c in cands
            if c.p_best_intervention >= EXPERIMENT_MIN_P_BEST
            and c.value_high >= EXPERIMENT_VALUE_FLOOR * greedy.value
        ]
        # One candidate per intervention -- the best-valued use of it -- so the
        # experiment is a choice between approaches, not between skills.
        by_iv: dict[str, Candidate] = {}
        for c in contenders:
            if c.intervention not in by_iv or c.value > by_iv[c.intervention].value:
                by_iv[c.intervention] = c
        # Ties on information are the norm, not the exception: two interventions
        # Aria has never run have *identical* posteriors, so one more episode of
        # either removes exactly the same amount of variance. Break that tie on
        # coverage -- fewest episodes first -- so successive uncertain sessions
        # sweep the catalogue instead of re-confirming the same arm.
        pick = (max(by_iv.values(),
                    key=lambda c: (round(c.info_value, 4), -c.episodes, c.value))
                if by_iv else greedy)

        cost = 1.0 - (pick.value / greedy.value if greedy.value > 0 else 1.0)
        learn = pick.info_value / max(policy.POP_MULTIPLIER_SD, 1e-9)

        if len(by_iv) <= 1 and greedy.episodes > 0:
            # Nothing else is within reach of the front-runner. Aria is not
            # confident in the abstract -- top_p is still low, because with
            # eight arms and wide retention priors it takes a lot of evidence
            # to get above two thirds -- but there is no experiment worth
            # running when every alternative would cost more than 30% of the
            # session's value. Calling this exploration would be flattering.
            #
            # The episode-count guard is the important half. Before any
            # evidence exists, this branch would fire on the cheapest-per-
            # question intervention every time -- and that lead comes entirely
            # from the time model, not from the student. A ranking Aria
            # inherited from her own priors is not something she knows.
            chosen, mode, note = greedy, "exploit", None
            reasons.append(
                f"{greedy.intervention_name} is far enough ahead that nothing "
                f"else is worth an experiment right now: no other approach is "
                f"within {EXPERIMENT_VALUE_FLOOR:.0%} of its expected value.")
            return InterventionDecision(
                chosen=chosen, mode=mode, greedy=greedy,
                alternatives=_alternatives(cands, chosen),
                reasons=reasons + [_value_line(chosen)], experiment_note=None)

        chosen, mode = pick, "explore"
        if pick.intervention == greedy.intervention:
            note = (
                f"No approach is clearly ahead yet - {pick.intervention_name} "
                f"leads at {top_p:.0%}. It is also the one this session would "
                f"teach me most about ({learn:.0%} of the remaining doubt), so "
                f"the best guess and the best experiment are the same move."
            )
        else:
            note = (
                f"Uncertain between {greedy.intervention_name} "
                f"({top_p:.0%} likely best for you) and {pick.intervention_name} "
                f"({pick.p_best_intervention:.0%}). Trying "
                f"{pick.intervention_name.lower()} this session gives up "
                f"{cost:.0%} of the expected gain and removes about "
                f"{learn:.0%} of what I still do not know about it."
            )
        reasons.append(note)

    reasons.append(_value_line(chosen))
    if chosen.retention_checks:
        reasons.append(
            f"Delayed checks after {chosen.intervention_name.lower()} have come "
            f"back right {chosen.expected_retention:.0%} of the time for you.")

    return InterventionDecision(chosen=chosen, mode=mode, greedy=greedy,
                                alternatives=_alternatives(cands, chosen),
                                reasons=reasons, experiment_note=note)


def _value_line(c: Candidate) -> str:
    return (f"Expected {c.expected_points:.1f} durable points from "
            f"{c.minutes:.0f} minutes ({c.value:.2f}/min, 80% range "
            f"{c.value_low:.2f}-{c.value_high:.2f}).")


def _alternatives(cands: list[Candidate], chosen: Candidate) -> list[Candidate]:
    """The comparison a reader actually wants to see.

    Other approaches to the *same* skill first, because that is the choice the
    policy engine just made and the one it can be argued with about. Then the
    best option on a different skill, which is the choice the old score-only
    engine would have been making. One row per intervention: five near-
    identical rows differing only in which algebra skill they target is a
    table nobody reads.
    """
    same_skill, other_skill = [], []
    seen_iv: set[str] = {chosen.intervention}
    seen_skill: set[str] = {chosen.skill_id}
    for c in cands:
        if c.key == chosen.key:
            continue
        if c.skill_id == chosen.skill_id:
            if c.intervention in seen_iv:
                continue
            seen_iv.add(c.intervention)
            same_skill.append(c)
        elif c.skill_id not in seen_skill:
            seen_skill.add(c.skill_id)
            other_skill.append(c)
    return same_skill[:3] + other_skill[:1]


# --- the time-constrained plan -------------------------------------------

@dataclass
class Block:
    candidate: Candidate
    decision: InterventionDecision


@dataclass
class PolicyPlan:
    minutes_available: float
    blocks: list[Block]

    @property
    def is_empty(self) -> bool:
        return not self.blocks

    @property
    def expected_points(self) -> float:
        return sum(b.candidate.expected_points for b in self.blocks)

    @property
    def total_minutes(self) -> float:
        return sum(b.candidate.minutes for b in self.blocks)

    @property
    def total_questions(self) -> int:
        return sum(b.candidate.questions for b in self.blocks)

    def alloc(self) -> list[list]:
        """Serialisable form for the conversation session."""
        return [[b.candidate.skill_id, b.candidate.intervention,
                 b.candidate.questions] for b in self.blocks]


def plan(phone: str, states: dict, minutes_available: float,
         subject_filter: str | None = None,
         rng: random.Random | None = None,
         max_blocks: int = MAX_BLOCKS_PER_SESSION,
         draws: int = THOMPSON_DRAWS) -> PolicyPlan:
    """Spend the minutes the student actually has.

    Greedy over blocks, re-deciding after each award with the remaining budget,
    and never twice on the same skill in one session. The re-decision matters:
    a candidate that needed four setup minutes may simply not fit into what is
    left, and the second block should be chosen knowing that rather than
    inheriting a ranking computed against a budget that no longer exists.
    """
    rng = rng or random
    remaining = float(minutes_available)
    used_skills: set[str] = set()
    tried: set[str] = set()
    blocks: list[Block] = []

    for _ in range(max_blocks):
        if remaining < 2.0:
            break
        decision = decide(phone, states, remaining, subject_filter=subject_filter,
                          exclude_skills=used_skills,
                          exclude_interventions=tried,
                          rng=rng, draws=draws)
        if decision is None:
            break
        blocks.append(Block(candidate=decision.chosen, decision=decision))
        used_skills.add(decision.chosen.skill_id)
        remaining -= decision.chosen.minutes
        # Running the same uncertain approach twice in one sitting buys much
        # less than running two: the two observations would share a day, a mood
        # and a mastery state, so they are correlated in exactly the way the
        # noise model assumes they are not. When Aria is confident, repetition
        # is fine and the block is allowed to repeat.
        if decision.is_experiment:
            tried.add(decision.chosen.intervention)

    return PolicyPlan(minutes_available=minutes_available, blocks=blocks)


# --- the shadow student ---------------------------------------------------

def shadow_table(decision: InterventionDecision, limit: int = 3) -> list[dict]:
    """The roads not taken, priced.

    Every row except the first describes an intervention that was not run, so
    every row except the first is a model estimate that nothing can confirm.
    Displayed anyway, and labelled as an estimate wherever it is displayed,
    because a decision engine that will not say what it gave up cannot be
    argued with.
    """
    rows = [{
        "label": "CHOSEN",
        "skill": decision.chosen.skill_name,
        "intervention": decision.chosen.intervention_name,
        "minutes": decision.chosen.minutes,
        "expected_points": decision.chosen.expected_points,
        "value": decision.chosen.value,
        "low": decision.chosen.value_low,
        "high": decision.chosen.value_high,
        "p_best": decision.chosen.p_best,
        "evidence": decision.chosen.evidence,
        "estimated": False,
    }]
    for alt in decision.alternatives[:limit]:
        rows.append({
            "label": "COUNTERFACTUAL ESTIMATE",
            "skill": alt.skill_name,
            "intervention": alt.intervention_name,
            "minutes": alt.minutes,
            "expected_points": alt.expected_points,
            "value": alt.value,
            "low": alt.value_low,
            "high": alt.value_high,
            "p_best": alt.p_best,
            "evidence": alt.evidence,
            "estimated": True,
        })
    return rows
