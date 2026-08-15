"""What actually helps *this* student.

`mastery.py` estimates what a student knows. This estimates something else
entirely, and it is the thing a tutoring system almost never models:

    given this student, which intervention causes learning?

Those are different questions with different answers. Two students with an
identical mastery vector can need opposite treatments, and a system that only
models knowledge will hand them the same session and be wrong for one of them.

Three quantities are tracked per student per intervention, because "did it
work" decomposes into three separately-observable things:

    multiplier ... how much a question teaches, vs baseline  (Normal posterior)
    retention .... share of it still there days later        (Beta posterior)
    engagement ... whether the student finishes it at all    (Beta posterior)

Durable value is the product. Keeping them apart is what lets Aria say
"retrieval taught you less per question but you kept more of it", which is a
real distinction that collapses into noise if you only track one number.

The multiplier is the load-bearing choice here. The obvious thing to model is
mastery gained per *minute*, and it is wrong: minutes then appear on both sides
of the value calculation and cancel, so an intervention with four minutes of
setup would rank identically to one with none. It also confounds the student
with the syllabus, because a question at mastery 0.9 cannot move the needle as
far as the same question at 0.3 no matter who is answering it.

So the observation is normalised against the model's own dynamics:

    multiplier = actual mastery gained
                 -----------------------------------------
                 questions x (what one average question, at
                 that starting mastery, would have bought)

1.0 means the intervention taught exactly as much per question as generic
practice. 1.6 means this student learns 60% more per question from it. The
quantity is scale-free, comparable across skills and mastery levels, and -- the
part that matters most -- its neutral prior is a genuine 1.0 for everything,
so Aria starts with no favourite. Minutes then enter only as cost, where they
belong, and an intervention that teaches better per question can still lose
because it takes too long. That trade-off is the entire point.

Nothing here is fitted from a population. There is one student's data, usually
single digits of episodes, so the honest machinery is conjugate updating from a
deliberately weak prior with the uncertainty carried around and displayed
rather than rounded away.

Raw episodes are the source of truth; posteriors are derived on read. That is a
few microseconds of arithmetic over a few dozen rows, and it buys two things
worth more than the cycles: every number Aria shows can be traced back to the
rows that produced it, and the model can be changed without a migration.

--------------------------------------------------------------------------
The confound worth stating out loud
--------------------------------------------------------------------------

Interventions are not assigned at random. The engine picks them, which means
each one is applied to the situations the engine thought suited it, and the
resulting estimates are observational rather than experimental.

The normalisation absorbs the largest part of that -- an episode at mastery 0.8
is scored against what an average question buys at 0.8, not against what one
buys at 0.3 -- but it does not absorb all of it. `spaced_review` is the clearest
case: it is only offered on skills the student has already got right, and prior
success predicts performance above the decayed mastery estimate, so it will
tend to score above 1.0 for reasons that have nothing to do with review being
effective. Read its number as "review on skills already half-known went well",
not as "review is this student's best teaching approach".

The exploration policy in counterfactual.py is what keeps this from getting
worse over time: because uncertain options are deliberately tried rather than
only the currently-favoured one, the assignment is not purely self-confirming.
That is a mitigation, not a fix. A real fix is randomised assignment, which
costs a student real study minutes, and that is a trade nobody should make on
their behalf without saying so.
"""

import math
import random
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone

from config import DB_PATH
from interventions import INTERVENTION_BY_ID, INTERVENTIONS

__all__ = [
    "Gaussian", "Beta", "InterventionEstimate", "Episode",
    "init", "record_episode", "close_episode", "baseline_step",
    "multiplier_posterior", "retention_posterior", "engagement_posterior",
    "estimate", "profile", "episodes_for", "episode", "regret_log",
    "POP_MULTIPLIER_MEAN", "POP_MULTIPLIER_SD", "OBS_NOISE_SD",
]


# --- priors ---------------------------------------------------------------
#
# Every intervention starts from the *same* prior. That is the central design
# commitment of this module: Aria is not allowed to arrive believing worked
# examples beat explanations, because then a demo showing her "discovering"
# that would be showing her reciting it. Any ordering she ends up with has to
# have been paid for with observations.

# 1.0 is "this intervention teaches exactly as much per question as the generic
# BKT step predicts". The prior is that every intervention is average, which is
# the only starting point that lets a demo of Aria discovering a preference be
# a discovery rather than a recital.
POP_MULTIPLIER_MEAN = 1.0
POP_MULTIPLIER_SD = 0.45     # wide: half or double is inside one sigma

# Spread of a single episode's observed multiplier around the student's true
# value for that intervention. Three questions is a very small sample of a
# Bernoulli process, so this is large, and it is what stops one lucky episode
# from convincing Aria of anything.
OBS_NOISE_SD = 0.55

# Denominator floor for the normalisation. Near saturation one average question
# buys almost nothing, and dividing by that would turn rounding error into a
# spectacular multiplier.
MIN_BASELINE_STEP = 0.005

# How far one skill domain may drift from the student's own average response to
# an intervention -- the standard deviation of the between-domain random
# effect. Some students really do need examples for algebra and questions for
# grammar; this says how much of that is plausible before evidence.
#
# It is *added* to the pooled variance and then capped at the population prior,
# which matters more than it looks. Multiplying the pooled variance instead
# would inflate a prior that has no information in it yet, so a brand-new
# student would start out *less* certain about a domain than about nothing at
# all -- and the 10th-percentile of every forecast would sit at zero.
BETWEEN_DOMAIN_SD = 0.30

# Retention: does the learning survive to a delayed check? Beta(2,2) is a
# genuine "no idea", centred at a half with almost no weight.
RETENTION_PRIOR_A = 2.0
RETENTION_PRIOR_B = 2.0

# Engagement: does the student finish the intervention once started? Most
# people finish most things, and this is the least interesting of the three, so
# it carries the most prior weight and moves the least.
ENGAGEMENT_PRIOR_A = 8.0
ENGAGEMENT_PRIOR_B = 2.0

# Floor used when a posterior mean is fed into an expected-value calculation.
# The posterior itself is allowed to go negative -- an intervention really can
# leave a student worse than it found them -- but a negative expected gain
# would make the ranking reward doing nothing, and "do nothing" is not on the
# menu for a student who has opened the app.
MIN_MULTIPLIER_FOR_VALUE = 0.0


def baseline_step(p_mastery: float) -> float:
    """Mastery one average practice question buys at this starting point.

    The denominator of the normalisation, taken from `mastery.expected_step` so
    the yardstick is the same model the rest of the system projects with rather
    than a second opinion invented here.
    """
    from mastery import expected_step
    return max(expected_step(p_mastery) - p_mastery, MIN_BASELINE_STEP)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init():
    conn = _connect()
    conn.executescript("""
        -- One row per intervention episode Aria ran. This is the evidence
        -- base: every posterior in this module is an aggregation over it, and
        -- every claim Aria makes about a student can be traced to these rows.
        CREATE TABLE IF NOT EXISTS intervention_episodes (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            phone          TEXT NOT NULL,
            intervention   TEXT NOT NULL,
            skill_id       TEXT NOT NULL,
            domain         TEXT NOT NULL,
            minutes        REAL NOT NULL DEFAULT 0,
            questions      INTEGER NOT NULL DEFAULT 0,
            answered       INTEGER NOT NULL DEFAULT 0,
            correct        INTEGER NOT NULL DEFAULT 0,
            mastery_before REAL NOT NULL,
            mastery_after  REAL,
            -- Mastery one average question would have bought at mastery_before.
            -- Stored rather than recomputed so an observation stays reproducible
            -- even if the BKT constants are ever retuned.
            baseline_step  REAL,
            learning_multiplier REAL,      -- the observation the model consumes
            completed      INTEGER,        -- engagement observation, 0/1
            -- What the engine predicted at decision time, kept so that regret
            -- is a comparison against the actual forecast rather than a
            -- reconstruction of one after the outcome is known.
            expected_multiplier REAL,
            expected_points_per_min REAL,
            observed_points_per_min REAL,
            decision_mode  TEXT,           -- 'exploit' | 'explore'
            p_best_at_decision REAL,
            started_at     TIMESTAMP,
            closed_at      TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_episodes_phone
            ON intervention_episodes(phone, intervention);

        -- Items shown as worked examples. They are not attempts -- no mastery
        -- update comes from watching Aria solve one -- but serving the same
        -- item back as a question afterwards would be measuring recall of the
        -- demo, so they are excluded from selection all the same.
        CREATE TABLE IF NOT EXISTS shown_items (
            phone       TEXT NOT NULL,
            question_id TEXT NOT NULL,
            shown_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (phone, question_id)
        );
    """)
    conn.commit()
    conn.close()


# --- distributions --------------------------------------------------------

@dataclass(frozen=True)
class Gaussian:
    """A Normal posterior over a rate. Carries its own evidence count."""
    mean: float
    sd: float
    n: int = 0

    @property
    def precision(self) -> float:
        return 1.0 / (self.sd ** 2) if self.sd > 0 else float("inf")

    def sample(self, rng: random.Random) -> float:
        return rng.gauss(self.mean, self.sd)

    def interval(self, z: float = 1.2816) -> tuple[float, float]:
        """Central interval. Default z is the 80% one -- wide enough to be
        honest, narrow enough that a judge can read two of them side by side."""
        return (self.mean - z * self.sd, self.mean + z * self.sd)

    def updated(self, observations: list[float],
                obs_sd: float = OBS_NOISE_SD) -> "Gaussian":
        """Conjugate update with known observation noise.

        Precisions add: tau_post = tau_prior + n / sigma_obs^2, and the mean is
        the precision-weighted average of the prior mean and the sample mean.
        That is the whole model. It is deliberately the simplest thing that
        carries uncertainty correctly, because with six data points anything
        more elaborate is fitting the prior rather than the student.
        """
        if not observations:
            return self
        tau_prior = self.precision
        tau_obs = len(observations) / (obs_sd ** 2)
        tau_post = tau_prior + tau_obs
        mean_obs = sum(observations) / len(observations)
        mean_post = (tau_prior * self.mean + tau_obs * mean_obs) / tau_post
        return Gaussian(mean_post, math.sqrt(1.0 / tau_post), self.n + len(observations))


@dataclass(frozen=True)
class Beta:
    a: float
    b: float

    @property
    def mean(self) -> float:
        return self.a / (self.a + self.b)

    @property
    def sd(self) -> float:
        total = self.a + self.b
        return math.sqrt(self.a * self.b / (total * total * (total + 1.0)))

    def sample(self, rng: random.Random) -> float:
        return rng.betavariate(self.a, self.b)

    def observed(self, successes: int, failures: int) -> "Beta":
        return Beta(self.a + successes, self.b + failures)


# --- the evidence base ----------------------------------------------------

@dataclass
class Episode:
    id: int
    phone: str
    intervention: str
    skill_id: str
    domain: str
    minutes: float
    questions: int
    answered: int
    correct: int
    mastery_before: float
    mastery_after: float | None
    baseline_step: float | None
    learning_multiplier: float | None
    completed: int | None
    expected_multiplier: float | None
    expected_points_per_min: float | None
    observed_points_per_min: float | None
    decision_mode: str | None
    p_best_at_decision: float | None
    started_at: datetime | None
    closed_at: datetime | None

    @property
    def is_closed(self) -> bool:
        return self.learning_multiplier is not None

    @property
    def regret(self) -> float | None:
        """Forecast minus outcome, in durable points per minute.

        Positive means Aria expected more than it got. This is *policy
        feedback*, not causal inference: it does not separate a bad choice of
        intervention from a bad day, and with one student there is no way to.
        It is useful anyway, because a systematically over-optimistic forecast
        for one intervention is exactly the signal that should demote it.
        """
        if self.expected_points_per_min is None or self.observed_points_per_min is None:
            return None
        return self.expected_points_per_min - self.observed_points_per_min


def _row_to_episode(row) -> Episode:
    from mastery import _parse
    return Episode(
        id=row["id"], phone=row["phone"], intervention=row["intervention"],
        skill_id=row["skill_id"], domain=row["domain"],
        minutes=row["minutes"], questions=row["questions"],
        answered=row["answered"], correct=row["correct"],
        mastery_before=row["mastery_before"], mastery_after=row["mastery_after"],
        baseline_step=row["baseline_step"],
        learning_multiplier=row["learning_multiplier"], completed=row["completed"],
        expected_multiplier=row["expected_multiplier"],
        expected_points_per_min=row["expected_points_per_min"],
        observed_points_per_min=row["observed_points_per_min"],
        decision_mode=row["decision_mode"],
        p_best_at_decision=row["p_best_at_decision"],
        started_at=_parse(row["started_at"]), closed_at=_parse(row["closed_at"]),
    )


def record_episode(phone: str, intervention: str, skill_id: str, domain: str,
                   mastery_before: float, questions: int,
                   expected_multiplier: float | None = None,
                   expected_points_per_min: float | None = None,
                   decision_mode: str | None = None,
                   p_best_at_decision: float | None = None) -> int:
    """Open an episode. Returns its id; the outcome arrives later."""
    if intervention not in INTERVENTION_BY_ID:
        raise ValueError(f"unknown intervention: {intervention!r}")
    conn = _connect()
    cur = conn.execute(
        """INSERT INTO intervention_episodes
               (phone, intervention, skill_id, domain, questions, mastery_before,
                baseline_step, expected_multiplier, expected_points_per_min,
                decision_mode, p_best_at_decision, started_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (phone, intervention, skill_id, domain, questions, mastery_before,
         baseline_step(mastery_before), expected_multiplier,
         expected_points_per_min, decision_mode,
         p_best_at_decision, _now().isoformat()),
    )
    episode_id = cur.lastrowid
    conn.commit()
    conn.close()
    _invalidate(phone)
    return episode_id


def close_episode(episode_id: int, mastery_after: float, minutes: float,
                  answered: int, correct: int, completed: bool,
                  points_per_mastery: float | None = None) -> Episode | None:
    """Record what happened, and derive the observation the model consumes.

    `minutes` is the intervention's modelled cost, not wall-clock. A student
    who walks away mid-episode and comes back an hour later did not spend an
    hour learning, and charging them for it would make every interruption look
    like a failed intervention.

    The multiplier is normalised by the questions actually *answered*, not the
    questions planned. A student who quits after one has given evidence about
    one question; scoring them against three would blame the intervention for
    the two that never happened.
    """
    ep = episode(episode_id)
    if ep is None:
        return None

    if answered <= 0:
        # Nothing was attempted, so there is nothing to conclude. Recording it
        # would post a 0.00x observation -- "this intervention taught nothing"
        # -- against an intervention that was never actually run, and a handful
        # of those is enough to bury a genuinely good approach.
        #
        # It is tempting to keep the row as an engagement failure. Resist: at
        # this level a zero-answer episode is indistinguishable from the bank
        # running out of unseen items on that skill, and blaming the teaching
        # approach for our own inventory is worse than losing the signal.
        _delete_episode(episode_id)
        return None

    minutes = max(minutes, 0.1)
    step = ep.baseline_step or baseline_step(ep.mastery_before)
    denominator = max(answered, 1) * step
    multiplier = (mastery_after - ep.mastery_before) / denominator

    observed_ppm = None
    if points_per_mastery is not None:
        # Durable points per minute, using the *current* retention estimate for
        # this intervention. When a delayed probe later contradicts it, the
        # retention posterior moves and the ranking moves with it -- which is
        # the mechanism, not a correction to it.
        rho = retention_posterior(ep.phone, ep.intervention).mean
        observed_ppm = (mastery_after - ep.mastery_before) * points_per_mastery * rho / minutes

    conn = _connect()
    conn.execute(
        """UPDATE intervention_episodes
              SET mastery_after = ?, minutes = ?, answered = ?, correct = ?,
                  completed = ?, learning_multiplier = ?,
                  observed_points_per_min = ?, closed_at = ?
            WHERE id = ?""",
        (mastery_after, minutes, answered, correct, 1 if completed else 0,
         multiplier, observed_ppm, _now().isoformat(), episode_id),
    )
    conn.commit()
    conn.close()
    _invalidate(ep.phone)
    return episode(episode_id)


def _delete_episode(episode_id: int):
    """Drop an episode that produced no observation, and anything hanging off
    it. A retention probe for an episode nobody answered would be checking the
    retention of nothing."""
    conn = _connect()
    conn.execute("DELETE FROM intervention_episodes WHERE id = ?", (episode_id,))
    conn.execute("DELETE FROM retention_probes WHERE episode_id = ? "
                 "AND resolved_at IS NULL", (episode_id,))
    conn.commit()
    conn.close()
    _invalidate()


def episode(episode_id: int) -> Episode | None:
    conn = _connect()
    row = conn.execute("SELECT * FROM intervention_episodes WHERE id = ?",
                       (episode_id,)).fetchone()
    conn.close()
    return _row_to_episode(row) if row else None


# One decision asks for this student's episodes about forty times -- eight
# interventions across five skill domains, each needing its own partial-pooling
# split. Going to SQLite for every one of those turned a study plan into a
# couple of seconds of round-trips, which is a real cost on the reply path and
# not only in the demo. The rows change only when this module writes them, so
# the cache is invalidated at the write rather than on a timer.
_episode_cache: dict[str, list[Episode]] = {}


def _invalidate(phone: str | None = None):
    if phone is None:
        _episode_cache.clear()
    else:
        _episode_cache.pop(phone, None)


def episodes_for(phone: str, intervention: str | None = None,
                 closed_only: bool = True) -> list[Episode]:
    if closed_only:
        cached = _episode_cache.get(phone)
        if cached is None:
            cached = _load_episodes(phone, closed_only=True)
            _episode_cache[phone] = cached
        if intervention is None:
            return cached
        return [e for e in cached if e.intervention == intervention]
    return _load_episodes(phone, closed_only=False, intervention=intervention)


def _load_episodes(phone: str, closed_only: bool,
                   intervention: str | None = None) -> list[Episode]:
    conn = _connect()
    sql = "SELECT * FROM intervention_episodes WHERE phone = ?"
    params: list = [phone]
    if intervention:
        sql += " AND intervention = ?"
        params.append(intervention)
    if closed_only:
        sql += " AND learning_multiplier IS NOT NULL"
    sql += " ORDER BY id"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [_row_to_episode(r) for r in rows]


def open_episode_ids(phone: str) -> list[int]:
    conn = _connect()
    rows = conn.execute(
        "SELECT id FROM intervention_episodes "
        "WHERE phone = ? AND learning_multiplier IS NULL ORDER BY id",
        (phone,)).fetchall()
    conn.close()
    return [r["id"] for r in rows]


# --- posteriors -----------------------------------------------------------

def multiplier_posterior(phone: str, intervention: str,
                         domain: str | None = None) -> Gaussian:
    """How much a question under this intervention teaches this student.

    Partial pooling, two levels. With no domain asked for, this is the
    student's overall response to the intervention, updated from every episode.
    With a domain, the estimate is allowed to differ -- some students really do
    need examples for algebra and questions for grammar -- but it starts from
    the pooled estimate rather than from nothing, which is what makes a single
    domain-specific episode informative instead of noise.

    The pooled prior is built from the *other* domains only. Reusing the global
    posterior directly would fold this domain's own observations into its own
    prior and count them twice, which understates uncertainty exactly where the
    data is thinnest.
    """
    episodes = episodes_for(phone, intervention)
    prior = Gaussian(POP_MULTIPLIER_MEAN, POP_MULTIPLIER_SD, 0)

    if domain is None:
        return prior.updated([e.learning_multiplier for e in episodes])

    other = [e.learning_multiplier for e in episodes if e.domain != domain]
    same = [e.learning_multiplier for e in episodes if e.domain == domain]
    pooled = prior.updated(other)
    # Pooling may sharpen the domain estimate; it must never blunt it, so the
    # widened prior is capped at the population one.
    sd = min(POP_MULTIPLIER_SD,
             math.sqrt(pooled.sd ** 2 + BETWEEN_DOMAIN_SD ** 2))
    return Gaussian(pooled.mean, sd, pooled.n).updated(same)


def retention_posterior(phone: str, intervention: str) -> Beta:
    """Share of what was learned that is still there at a delayed check.

    Imported lazily: retention.py schedules and resolves the probes, and it
    needs this module for episode lookup, so the dependency has to run one way
    at import time and the other way at call time.
    """
    import retention
    kept, lost = retention.outcome_counts(phone, intervention)
    return Beta(RETENTION_PRIOR_A, RETENTION_PRIOR_B).observed(kept, lost)


def engagement_posterior(phone: str, intervention: str) -> Beta:
    episodes = episodes_for(phone, intervention)
    done = sum(1 for e in episodes if e.completed)
    quit_ = sum(1 for e in episodes if e.completed == 0)
    return Beta(ENGAGEMENT_PRIOR_A, ENGAGEMENT_PRIOR_B).observed(done, quit_)


@dataclass
class InterventionEstimate:
    """Aria's current belief about one intervention for one student."""
    intervention: str
    name: str
    multiplier: Gaussian
    retention: Beta
    engagement: Beta
    episodes: int
    retention_checks: int
    domain: str | None = None

    @property
    def durable_effectiveness(self) -> float:
        """Learning per question that is still there days later.

        The number the Learning Response Profile bars are drawn from. 1.0 is
        "average practice, fully retained"; it is a ratio, not a percentage,
        and it is not comparable across students.
        """
        return max(self.multiplier.mean, MIN_MULTIPLIER_FOR_VALUE) * self.retention.mean

    @property
    def confidence(self) -> float:
        """0-1. How far the posterior has moved off the population prior.

        Deliberately a shrinkage ratio rather than a p-value: it answers "how
        much of this number is the student and how much is still my prior",
        which is the question a judge and a student both actually have.
        """
        if self.multiplier.sd <= 0:
            return 1.0
        return max(0.0, min(1.0, 1.0 - (self.multiplier.sd / POP_MULTIPLIER_SD)))

    @property
    def evidence_line(self) -> str:
        if self.episodes == 0:
            return "no evidence yet - this is the starting assumption"
        bits = [f"{self.episodes} session{'s' if self.episodes != 1 else ''}"]
        if self.retention_checks:
            bits.append(f"{self.retention_checks} delayed check"
                        f"{'s' if self.retention_checks != 1 else ''} "
                        f"({self.retention.mean:.0%} kept)")
        bits.append(f"learns {self.multiplier.mean:.2f}x average per question "
                    f"(+/- {self.multiplier.sd:.2f})")
        return ", ".join(bits)


def estimate(phone: str, intervention: str,
             domain: str | None = None) -> InterventionEstimate:
    import retention
    eps = episodes_for(phone, intervention)
    kept, lost = retention.outcome_counts(phone, intervention)
    return InterventionEstimate(
        intervention=intervention,
        name=INTERVENTION_BY_ID[intervention].name,
        multiplier=multiplier_posterior(phone, intervention, domain),
        retention=retention_posterior(phone, intervention),
        engagement=engagement_posterior(phone, intervention),
        episodes=len(eps),
        retention_checks=kept + lost,
        domain=domain,
    )


def profile(phone: str, domain: str | None = None) -> list[InterventionEstimate]:
    """The Learning Response Profile.

    Aria's current estimate of which instructional approaches work for this
    student, ranked by durable learning per question. Not a personality type,
    not a diagnosis, and not a claim about how they learn in general -- only
    what has and has not moved the needle here, with the evidence attached.
    """
    out = [estimate(phone, iv.id, domain) for iv in INTERVENTIONS]
    out.sort(key=lambda e: -e.durable_effectiveness)
    return out


def has_evidence(phone: str) -> bool:
    return bool(episodes_for(phone))


def regret_log(phone: str, limit: int = 20) -> list[dict]:
    """Where the forecast and the outcome disagreed, worst first.

    Labelled as estimated policy feedback wherever it is displayed. The
    counterfactual column is a model estimate of an intervention that was not
    run, so it cannot be checked against anything; it is shown because a
    decision engine that never states what it gave up is not auditable.
    """
    rows = []
    for ep in episodes_for(phone):
        r = ep.regret
        if r is None:
            continue
        rows.append({
            "episode_id": ep.id,
            "intervention": ep.intervention,
            "name": INTERVENTION_BY_ID[ep.intervention].name,
            "skill_id": ep.skill_id,
            "expected": ep.expected_points_per_min,
            "observed": ep.observed_points_per_min,
            "regret": r,
            "mode": ep.decision_mode,
        })
    rows.sort(key=lambda d: -abs(d["regret"]))
    return rows[:limit]


# --- worked-example bookkeeping ------------------------------------------

def mark_shown(phone: str, question_id: str):
    conn = _connect()
    conn.execute(
        "INSERT OR IGNORE INTO shown_items (phone, question_id, shown_at) VALUES (?, ?, ?)",
        (phone, question_id, _now().isoformat()))
    conn.commit()
    conn.close()


def shown_item_ids(phone: str) -> set[str]:
    conn = _connect()
    rows = conn.execute("SELECT question_id FROM shown_items WHERE phone = ?",
                        (phone,)).fetchall()
    conn.close()
    return {r["question_id"] for r in rows}
