"""Builds the coach-facing dashboard.

Two audiences, one engine:

  * a student's own picture -- what they know, what it projects to, and what
    tonight's minutes are worth
  * a counsellor's triage view -- with 400 students and no time, which ones
    does an hour of attention move the most?

The second is the reason this exists as a web page at all. Students get plain
text on WhatsApp because that works on a shared phone over 2G; the person with
a desktop and a caseload is the one who needs a screen.

    python dashboard.py --seed     create a demo cohort
    python dashboard.py            write dashboard.html from the live database
"""

import argparse
import json
import os
import random
import sqlite3
from datetime import datetime, timedelta, timezone

import mastery
import simulator
import student as student_mod
from config import DB_PATH
from skills import SKILL_BY_ID, SKILLS, question_weight

OUT_PATH = os.path.join(os.path.dirname(__file__), "dashboard.html")

# Demo cohort. Names are deliberately varied; the profiles encode different
# *shapes* of student so the triage view has something real to sort.
_COHORT = [
    ("Maya Okonkwo",      1400, 0.62, {"m_systems": 0.15, "m_equivalent_expr": 0.2}),
    ("Diego Ramirez",     1200, 0.45, {"rw_boundaries": 0.2, "rw_transitions": 0.25}),
    ("Aisha Rahman",      1500, 0.80, {"m_circles": 0.3}),
    ("Tyler Brooks",      1100, 0.30, {}),
    ("Priya Nair",        1350, 0.70, {"m_nonlinear_eq": 0.25}),
    ("Jordan Ellis",      1200, 0.38, {"rw_synthesis": 0.15, "rw_form_structure": 0.2}),
    ("Sofia Marino",      1300, 0.55, {"m_two_var_data": 0.2}),
    ("Kwame Mensah",      1450, 0.72, {"rw_cross_text": 0.3}),
    ("Lin Zhao",          1250, 0.50, {"m_percentages": 0.2, "m_ratios_rates": 0.25}),
    ("Hannah Weiss",      1150, 0.35, {"rw_words_in_context": 0.2}),
    ("Omar Haddad",       1400, 0.68, {"m_right_triangles_trig": 0.2}),
    ("Grace Adeyemi",     1050, 0.28, {"m_linear_one_var": 0.15}),
]


def seed_cohort():
    """Populate a demo cohort by running real BKT updates, not by writing
    mastery values directly -- so the numbers on the dashboard came out of the
    same code path a real student's would."""
    rng = random.Random(11)
    student_mod.init()
    mastery.init()

    import autonomy
    autonomy.init()

    now = datetime.now(timezone.utc)
    for i, (name, target, base, weak) in enumerate(_COHORT):
        phone = f"1555000{2000 + i}"
        _wipe(phone)
        student_mod.get_or_create(phone, name=name)
        student_mod.update(phone, target_score=target,
                           test_date=(now + timedelta(days=rng.randint(12, 70))).date())

        for skill in SKILLS:
            accuracy = weak.get(skill.id, base + rng.uniform(-0.12, 0.12))
            accuracy = min(max(accuracy, 0.05), 0.95)
            for _ in range(rng.randint(2, 6)):
                mastery.record_attempt(
                    phone, skill.id, rng.random() < accuracy,
                    question_id=f"seed_{skill.id}_{rng.randint(0, 10**9)}",
                    misconception="seed_pattern" if rng.random() < 0.3 else None,
                    source="seed",
                )

        _seed_policy(phone, rng, _TRAIT_SHAPES[i % len(_TRAIT_SHAPES)], now)

        # Backdate activity so the decay model and the idle triggers have
        # something to work with.
        days_idle = rng.choice([0, 0, 1, 2, 3, 5, 8])
        conn = sqlite3.connect(DB_PATH)
        stamp = (now - timedelta(days=days_idle)).isoformat()
        conn.execute("UPDATE students SET last_active = ? WHERE phone = ?", (stamp, phone))
        conn.execute("UPDATE skill_mastery SET last_seen = ? WHERE phone = ?", (stamp, phone))
        conn.commit()
        conn.close()
        print(f"  seeded {name} ({days_idle}d idle)")

    print(f"\n{len(_COHORT)} students seeded")


def _wipe(phone: str):
    conn = sqlite3.connect(DB_PATH)
    for table in ("sessions", "students", "skill_mastery", "attempts",
                  "agent_decisions", "intervention_episodes", "retention_probes",
                  "shown_items"):
        try:
            conn.execute(f"DELETE FROM {table} WHERE phone = ?", (phone,))
        except sqlite3.OperationalError:
            pass
    conn.commit()
    conn.close()


# Hidden per-student response traits for the demo cohort: (accuracy, retention)
# under each approach. Aria never reads these -- the seeder answers questions
# with them and she has to infer the pattern from the BKT updates that result,
# exactly as she would from a real student.
#
# They differ per student on purpose. Two students with the same weak skill
# getting different prescriptions is the entire argument, and it has to be
# visible in the cohort rather than asserted in a caption.
#
# Accuracies are calibrated to straddle what the BKT model already expects of a
# student at these mastery levels (around 0.6 correct). An approach at 0.5
# accuracy is *underperforming* the model and records below 1.0x however
# reasonable it sounds -- which is why an earlier, gentler set of numbers put
# almost every measured approach beneath the untouched prior.
_TRAIT_SHAPES = [
    {"worked_example": (0.90, 0.90), "retrieval_practice": (0.62, 0.70),
     "hint_first": (0.74, 0.22), "direct_explanation": (0.55, 0.32),
     "timed_drill": (0.45, 0.25)},
    {"retrieval_practice": (0.76, 0.92), "hint_first": (0.80, 0.20),
     "socratic": (0.64, 0.60), "worked_example": (0.66, 0.50),
     "timed_drill": (0.46, 0.28)},
    {"misconception_repair": (0.90, 0.86), "timed_drill": (0.76, 0.70),
     "retrieval_practice": (0.62, 0.62), "worked_example": (0.64, 0.48),
     "direct_explanation": (0.50, 0.28)},
    {"direct_explanation": (0.88, 0.82), "worked_example": (0.74, 0.62),
     "hint_first": (0.66, 0.28), "socratic": (0.54, 0.36),
     "timed_drill": (0.45, 0.24)},
]


def _seed_policy(phone: str, rng: random.Random, shape: dict, now):
    """Give a seeded student a real intervention history.

    Every episode runs the shipping code: mastery moves through
    `mastery.record_attempt`, the multiplier is derived by
    `policy.close_episode` from that movement, and the retention probes are
    genuine scheduled checks. Nothing is written into the model directly, so
    the profile the dashboard renders was inferred rather than authored.
    """
    import counterfactual
    import policy
    import retention

    approaches = list(shape)
    # Spread across a wide pool. Concentrating twenty-odd episodes on six
    # skills drives them to saturation, and near saturation the multiplier is
    # sharply asymmetric -- one wrong answer costs far more mastery than one
    # right answer gains, relative to what an average question buys there. The
    # result was four approaches pinned at zero, which says more about the
    # fixture than about the student. (The underlying asymmetry is real and is
    # noted in the README: observation noise is modelled as constant when it
    # actually grows with mastery.)
    weak = sorted(SKILLS, key=lambda s: mastery.get_state(phone, s.id).p_mastery)[:14]

    # Roughly three weeks of short sessions. Fewer than this and the tested
    # approaches sit *below* the untouched prior on the profile -- which is
    # arithmetically correct and reads as nonsense, because two episodes is
    # genuinely not enough to beat "no idea". The honest fix is more evidence,
    # not a friendlier sort order.
    total = rng.randint(18, 24)
    for n in range(total):
        intervention = approaches[n % len(approaches)]
        accuracy, retain = shape[intervention]
        skill = rng.choice(weak)
        questions = rng.randint(2, 4)

        states = mastery.get_all_states(phone)
        state = states[skill.id]
        expected, multiplier, ppm = counterfactual.expected_value(
            phone, states, skill.id, intervention, questions)

        episode_id = policy.record_episode(
            phone, intervention, skill.id, skill.domain, state.p_mastery,
            questions, expected_multiplier=multiplier,
            expected_points_per_min=expected, decision_mode="seed")

        correct = 0
        for _ in range(questions):
            right = rng.random() < accuracy
            correct += 1 if right else 0
            mastery.record_attempt(
                phone, skill.id, right,
                question_id=f"pol_{skill.id}_{rng.randint(0, 10**9)}",
                misconception=None if right else "seed_pattern", source="seed")

        after = mastery.get_state(phone, skill.id).p_mastery
        from interventions import INTERVENTION_BY_ID
        policy.close_episode(
            episode_id, mastery_after=after,
            minutes=INTERVENTION_BY_ID[intervention].minutes_for(questions),
            answered=questions, correct=correct, completed=True,
            points_per_mastery=ppm)

        # Older episodes have had their delayed check come back; the most
        # recent ones are still outstanding, which is the honest steady state.
        gain = after - state.p_mastery
        probe_id = retention.schedule(phone, episode_id, intervention, skill.id,
                                      after, gain=gain, delay_days=-2.0)
        if probe_id and n < total - 3:
            retention.resolve(probe_id, rng.random() < retain)


def student_payload(phone: str, minutes: int = 25) -> dict | None:
    profile = student_mod.get(phone)
    if not profile:
        return None

    states = mastery.get_all_states(phone)
    projection = simulator.project(states, n_sims=1500, seed=3)
    plan = simulator.plan_session(states, minutes)
    gains = {g.skill_id: g for g in simulator.marginal_gains(states)}
    planned = {item.skill_id for item in plan.skills}

    skills_payload = []
    for skill in SKILLS:
        state = states[skill.id]
        gain = gains.get(skill.id)
        skills_payload.append({
            "id": skill.id,
            "name": skill.name,
            "section": skill.section,
            "domain": skill.domain,
            "mastery": round(state.p_mastery, 4),
            "attempts": state.attempts,
            "questionsPerTest": round(question_weight(skill.id), 2),
            "pointsAvailable": round(gain.points_gained, 1) if gain else 0.0,
            "pointsPerMinute": round(gain.points_per_minute, 3) if gain else 0.0,
            "inPlan": skill.id in planned,
        })

    # Histogram of the simulated score distribution.
    samples = projection.samples
    lo, hi = min(samples), max(samples)
    buckets = 24
    width = max(10, (hi - lo) / buckets) if hi > lo else 10
    hist: dict[int, int] = {}
    for value in samples:
        index = int((value - lo) / width)
        hist[index] = hist.get(index, 0) + 1
    histogram = [{"score": round(lo + i * width), "count": hist.get(i, 0)}
                 for i in range(max(hist.keys()) + 1)] if hist else []

    import autonomy
    candidates = sorted(autonomy.evaluate(phone), key=lambda d: -d.score)
    allowed, gate = autonomy.can_message(phone)

    return {
        "phone": phone,
        "name": profile.name,
        "target": profile.target_score,
        "daysUntilTest": profile.days_until_test,
        "daysIdle": round(profile.days_since_active or 0, 1),
        "attempts": mastery.total_attempts(phone),
        "projection": {
            "total": projection.total,
            "low": projection.total_low,
            "high": projection.total_high,
            "rw": projection.rw,
            "math": projection.math,
            "chanceOfTarget": round(projection.probability_at_least(profile.target_score), 3),
        },
        "daysToTarget": simulator.days_to_target(states, profile.target_score, 20),
        "histogram": histogram,
        "skills": skills_payload,
        "plan": {
            "minutes": minutes,
            "expectedPoints": round(plan.expected_points, 1),
            "totalQuestions": plan.total_questions,
            "items": [{
                "skillId": item.skill_id,
                "name": item.name,
                "questions": item.questions,
                "minutes": item.minutes,
                "points": round(item.points_gained, 1),
                "masteryBefore": round(item.mastery_before, 3),
                "masteryAfter": round(item.mastery_after, 3),
            } for item in plan.skills],
        },
        "agent": {
            "canMessage": allowed,
            "gate": gate,
            "candidates": [{
                "trigger": d.trigger,
                "skill": SKILL_BY_ID[d.skill_id].name if d.skill_id else None,
                "score": round(d.score, 1),
                "reason": d.reason,
                "evidence": d.evidence,
            } for d in candidates],
        },
    }


def policy_payload(phone: str, minutes: int = 25) -> dict:
    """What Aria has learned about how this student learns, and what she'd do.

    Deterministic seed on the Thompson pass so the page is reproducible; the
    decision itself is the shipping one, drawn from the same posteriors a
    WhatsApp reply would use.
    """
    import counterfactual
    import policy
    import retention

    states = mastery.get_all_states(phone)
    decision = counterfactual.decide(phone, states, minutes,
                                     rng=random.Random(17))
    episodes = policy.episodes_for(phone)

    profile = [{
        "id": e.intervention,
        "name": e.name,
        "durable": round(e.durable_effectiveness, 3),
        "multiplier": round(e.multiplier.mean, 2),
        "multiplierSd": round(e.multiplier.sd, 2),
        "retention": round(e.retention.mean, 3),
        "episodes": e.episodes,
        "checks": e.retention_checks,
        "confidence": round(e.confidence, 3),
        "evidence": e.evidence_line,
        "maintenance": e.is_maintenance,
    } for e in policy.profile(phone)]

    best = policy.best_teaching_approach(phone)

    chosen = decision.chosen if decision else None
    table = counterfactual.shadow_table(decision) if decision else []
    alt_candidates = ([chosen] + decision.alternatives) if decision else []

    return {
        "hasEvidence": bool(episodes),
        "episodeCount": len(episodes),
        "bestTeaching": None if not best else {
            "name": best.name,
            "episodes": best.episodes,
            "checks": best.retention_checks,
            "retention": round(best.retention.mean, 3),
        },
        "profile": profile,
        "retention": retention.summary(phone),
        "decision": None if not decision else {
            "mode": decision.mode,
            "skill": chosen.skill_name,
            "intervention": chosen.intervention_name,
            "studentLabel": chosen.student_label,
            "questions": chosen.questions,
            "minutes": round(chosen.minutes, 1),
            "expectedPoints": round(chosen.expected_points, 1),
            "value": round(chosen.value, 2),
            "low": round(chosen.value_low, 2),
            "high": round(chosen.value_high, 2),
            "retentionExpected": round(chosen.expected_retention, 3),
            "risk": round(chosen.p_failure, 3),
            "pBest": round(chosen.p_best_intervention, 3),
            "reasons": decision.reasons,
            "note": decision.experiment_note,
        },
        "shadow": [{
            "label": row["label"],
            "estimated": row["estimated"],
            "skill": row["skill"],
            "intervention": row["intervention"],
            "minutes": round(row["minutes"], 1),
            "value": round(row["value"], 2),
            "low": round(row["low"], 2),
            "high": round(row["high"], 2),
            "pBest": round(cand.p_best_intervention, 3),
            "evidence": row["evidence"],
        } for row, cand in zip(table, alt_candidates)],
        "regret": [{
            "name": r["name"],
            "skill": SKILL_BY_ID[r["skill_id"]].name if r["skill_id"] in SKILL_BY_ID else r["skill_id"],
            "expected": round(r["expected"], 2),
            "observed": round(r["observed"], 2),
            "regret": round(r["regret"], 2),
        } for r in policy.regret_log(phone, limit=6)],
    }


def cohort_payload() -> list[dict]:
    """Rank every student by how much an hour of attention would move them."""
    rows = []
    for profile in student_mod.all_students():
        states = mastery.get_all_states(profile.phone)
        if mastery.total_attempts(profile.phone) == 0:
            continue
        projected = simulator.expected_total(states)
        plan = simulator.plan_session(states, 60)
        gains = simulator.marginal_gains(states)

        # What Aria has worked out about how this one learns. Shown per row
        # because the claim is that it *differs* between students -- two rows
        # with the same weak skill and different prescriptions make that
        # argument in a way no caption can.
        import policy
        best = policy.best_teaching_approach(profile.phone)

        rows.append({
            "phone": profile.phone,
            "name": profile.name,
            "target": profile.target_score,
            "projected": int(round(projected / 10.0) * 10),
            "gap": int(round((profile.target_score - projected) / 10.0) * 10),
            "daysUntilTest": profile.days_until_test,
            "daysIdle": round(profile.days_since_active or 0, 1),
            "pointsPerHour": round(plan.expected_points, 1),
            "topSkill": gains[0].name if gains else None,
            "bestApproach": best.name if best else None,
            "approachEpisodes": best.episodes if best else 0,
            "approachConfidence": round(best.confidence, 2) if best else 0.0,
        })
    rows.sort(key=lambda r: -r["pointsPerHour"])
    return rows


def build(focus_phone: str | None = None, minutes: int = 25):
    import bank

    students = student_mod.all_students()
    if not students:
        print("No students. Run: python dashboard.py --seed")
        return

    focus = focus_phone or students[0].phone
    payload = student_payload(focus, minutes)
    if payload is None:
        print(f"no student {focus}")
        return

    data = {
        "generatedAt": datetime.now(timezone.utc).strftime("%d %B %Y, %H:%M UTC"),
        "student": payload,
        "policy": policy_payload(focus, minutes),
        "cohort": cohort_payload(),
        "bankSize": len(bank.load()),
        "skillCount": len(SKILLS),
    }

    html = TEMPLATE.replace("__DATA__", json.dumps(data, separators=(",", ":")))
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"wrote {OUT_PATH}")
    print(f"  focus student : {payload['name']}")
    print(f"  projection    : {payload['projection']['total']} "
          f"({payload['projection']['low']}-{payload['projection']['high']})")
    print(f"  cohort        : {len(data['cohort'])} students")


TEMPLATE = r"""<title>Aria - study-time allocation console</title>
<style>
  :root {
    color-scheme: light;
    --plane:      #f4f5f7;
    --surface:    #fcfcfd;
    --raised:     #ffffff;
    --ink:        #10151c;
    --ink-2:      #4d5764;
    --muted:      #7c8797;
    --line:       #e2e6eb;
    --grid:       #eceff3;
    --accent:     #2a78d6;
    --accent-dim: #cde2fb;
    --good:       #0ca30c;
    --warning:    #fab219;
    --critical:   #d03b3b;
    --shadow:     0 1px 2px rgba(16,21,28,.06), 0 8px 24px rgba(16,21,28,.05);

    --serif: "Iowan Old Style", "Palatino Linotype", Palatino, "Book Antiqua", Georgia, serif;
    --sans: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
    --mono: ui-monospace, "SF Mono", "Cascadia Mono", Menlo, Consolas, monospace;
  }
  @media (prefers-color-scheme: dark) {
    :root:where(:not([data-theme="light"])) {
      color-scheme: dark;
      --plane:      #0b0e12;
      --surface:    #12171e;
      --raised:     #171d26;
      --ink:        #eef1f5;
      --ink-2:      #aab4c2;
      --muted:      #7b8695;
      --line:       #222b36;
      --grid:       #1b2029;
      --accent:     #3987e5;
      --accent-dim: #184f95;
      --good:       #0ca30c;
      --warning:    #fab219;
      --critical:   #e05b5b;
      --shadow:     0 1px 2px rgba(0,0,0,.4), 0 8px 24px rgba(0,0,0,.3);
    }
  }
  :root[data-theme="dark"] {
    color-scheme: dark;
    --plane:      #0b0e12;
    --surface:    #12171e;
    --raised:     #171d26;
    --ink:        #eef1f5;
    --ink-2:      #aab4c2;
    --muted:      #7b8695;
    --line:       #222b36;
    --grid:       #1b2029;
    --accent:     #3987e5;
    --accent-dim: #184f95;
    --good:       #0ca30c;
    --warning:    #fab219;
    --critical:   #e05b5b;
    --shadow:     0 1px 2px rgba(0,0,0,.4), 0 8px 24px rgba(0,0,0,.3);
  }

  * { box-sizing: border-box; }
  body {
    margin: 0; background: var(--plane); color: var(--ink);
    font-family: var(--sans); font-size: 15px; line-height: 1.55;
    -webkit-font-smoothing: antialiased;
  }
  .wrap { max-width: 1120px; margin: 0 auto; padding: 40px 24px 80px; }

  header { margin-bottom: 36px; }
  .eyebrow {
    font-family: var(--mono); font-size: 11px; letter-spacing: .14em;
    text-transform: uppercase; color: var(--muted); margin: 0 0 10px;
  }
  h1 {
    font-family: var(--serif); font-weight: 600; font-size: clamp(28px, 4vw, 40px);
    line-height: 1.15; margin: 0 0 10px; text-wrap: balance; letter-spacing: -.01em;
  }
  .sub { color: var(--ink-2); margin: 0; max-width: 62ch; }

  h2 {
    font-family: var(--serif); font-size: 21px; font-weight: 600;
    margin: 0 0 4px; letter-spacing: -.005em;
  }
  .note { color: var(--ink-2); font-size: 13.5px; margin: 0 0 18px; max-width: 68ch; }

  section { margin-top: 40px; }
  .card {
    background: var(--surface); border: 1px solid var(--line);
    border-radius: 10px; padding: 22px; box-shadow: var(--shadow);
  }

  .tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(158px, 1fr)); gap: 14px; }
  .tile { background: var(--surface); border: 1px solid var(--line); border-radius: 10px; padding: 16px 18px; box-shadow: var(--shadow); }
  .tile .label { font-family: var(--mono); font-size: 10.5px; letter-spacing: .1em; text-transform: uppercase; color: var(--muted); }
  .tile .value { font-size: 30px; font-weight: 600; line-height: 1.1; margin-top: 6px; letter-spacing: -.02em; }
  .tile .foot { font-size: 12.5px; color: var(--ink-2); margin-top: 4px; }
  .tile .value.sm { font-size: 22px; }

  .grid2 { display: grid; grid-template-columns: 1.35fr 1fr; gap: 20px; align-items: start; }
  @media (max-width: 860px) { .grid2 { grid-template-columns: 1fr; } }

  figure { margin: 0; }
  figcaption { font-size: 12.5px; color: var(--ink-2); margin-top: 12px; }

  .legend { display: flex; flex-wrap: wrap; gap: 16px; margin: 0 0 14px; font-size: 12.5px; color: var(--ink-2); }
  .key { display: inline-flex; align-items: center; gap: 7px; }
  .dot { width: 10px; height: 10px; border-radius: 50%; flex: none; }

  table { width: 100%; border-collapse: collapse; font-size: 13.5px; }
  th, td { text-align: left; padding: 9px 12px; border-bottom: 1px solid var(--line); }
  th { font-family: var(--mono); font-size: 10.5px; letter-spacing: .09em; text-transform: uppercase; color: var(--muted); font-weight: 500; }
  td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; font-family: var(--mono); font-size: 12.5px; }
  tbody tr:last-child td { border-bottom: none; }
  tbody tr:hover { background: var(--grid); }
  .scroll { overflow-x: auto; }

  .pill { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 11px; font-family: var(--mono); letter-spacing: .04em; border: 1px solid transparent; white-space: nowrap; }
  .pill.plan { background: var(--accent-dim); color: var(--accent); border-color: var(--accent); }
  .pill.risk { background: color-mix(in srgb, var(--critical) 14%, transparent); color: var(--critical); border-color: color-mix(in srgb, var(--critical) 45%, transparent); }
  .pill.idle { background: color-mix(in srgb, var(--warning) 18%, transparent); color: color-mix(in srgb, var(--warning) 70%, var(--ink)); border-color: color-mix(in srgb, var(--warning) 50%, transparent); }
  .pill.ok { background: color-mix(in srgb, var(--good) 12%, transparent); color: var(--good); border-color: color-mix(in srgb, var(--good) 40%, transparent); }

  .plan-row { display: grid; grid-template-columns: 1fr auto; gap: 4px 16px; padding: 13px 0; border-bottom: 1px solid var(--line); align-items: baseline; }
  .plan-row:last-of-type { border-bottom: none; }
  .plan-name { font-weight: 550; }
  .plan-meta { font-size: 12.5px; color: var(--ink-2); grid-column: 1; }
  .plan-pts { font-family: var(--mono); font-variant-numeric: tabular-nums; font-size: 15px; color: var(--accent); font-weight: 600; grid-row: 1 / span 2; align-self: center; }
  .bar { height: 5px; background: var(--grid); border-radius: 3px; overflow: hidden; grid-column: 1 / -1; margin-top: 7px; }
  .bar > i { display: block; height: 100%; background: var(--accent); border-radius: 3px; }

  .decision { border-left: 2px solid var(--line); padding: 2px 0 2px 16px; margin-bottom: 18px; }
  .decision.top { border-left-color: var(--accent); }
  .decision .dh { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; }
  .decision .trigger { font-family: var(--mono); font-size: 11.5px; letter-spacing: .05em; color: var(--accent); }
  .decision .score { font-family: var(--mono); font-size: 11.5px; color: var(--muted); font-variant-numeric: tabular-nums; }
  .decision .reason { margin: 4px 0 3px; }
  .decision .evidence { font-family: var(--mono); font-size: 11.5px; color: var(--ink-2); }

  .callout { background: var(--raised); border: 1px solid var(--line); border-left: 3px solid var(--accent); border-radius: 8px; padding: 16px 20px; margin-top: 18px; }
  .callout strong { font-weight: 600; }

  /* --- learning response profile --- */
  .lp { display: grid; grid-template-columns: 1fr; gap: 11px; }
  .lp-row { display: grid; grid-template-columns: 190px 1fr 128px; gap: 12px; align-items: center; }
  .lp-name { font-size: 13px; }
  .lp-track { position: relative; height: 15px; background: color-mix(in srgb, var(--ink) 7%, transparent); border-radius: 4px; overflow: hidden; }
  .lp-fill { position: absolute; inset: 0 auto 0 0; border-radius: 4px; background: var(--accent); }
  .lp-fill.untested { background: repeating-linear-gradient(135deg, color-mix(in srgb, var(--ink) 16%, transparent) 0 5px, transparent 5px 10px); }
  .lp-fill.weak { background: var(--warning); }
  .lp-meta { font-family: var(--mono); font-size: 11px; color: var(--muted); text-align: right; font-variant-numeric: tabular-nums; }
  .lp-legend { margin-top: 14px; font-size: 12.5px; color: var(--ink-2); }

  .mode { font-family: var(--mono); font-size: 11px; letter-spacing: .09em; padding: 3px 9px; border-radius: 5px; }
  .mode.exploit { background: color-mix(in srgb, var(--good) 13%, transparent); color: var(--good); border: 1px solid color-mix(in srgb, var(--good) 40%, transparent); }
  .mode.explore { background: color-mix(in srgb, var(--warning) 18%, transparent); color: color-mix(in srgb, var(--warning) 72%, var(--ink)); border: 1px solid color-mix(in srgb, var(--warning) 50%, transparent); }

  .dec-grid { display: grid; grid-template-columns: 128px 1fr; gap: 5px 16px; font-size: 13.5px; margin: 14px 0 4px; }
  .dec-grid dt { font-family: var(--mono); font-size: 11.5px; color: var(--muted); letter-spacing: .04em; text-transform: uppercase; padding-top: 2px; }
  .dec-grid dd { margin: 0; }

  tr.cf td { color: var(--ink-2); }
  tr.cf td:first-child { font-style: italic; }

  svg { display: block; width: 100%; height: auto; overflow: visible; }
  .tick { font-family: var(--mono); font-size: 10px; fill: var(--muted); }
  .axis-title { font-family: var(--mono); font-size: 10px; letter-spacing: .09em; text-transform: uppercase; fill: var(--muted); }
  .mark { cursor: pointer; }
  .mark:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }

  #tip {
    position: fixed; pointer-events: none; opacity: 0; transition: opacity .12s;
    background: var(--raised); border: 1px solid var(--line); border-radius: 7px;
    padding: 9px 12px; box-shadow: var(--shadow); font-size: 12.5px; max-width: 250px; z-index: 10;
  }
  #tip b { display: block; margin-bottom: 3px; font-size: 13px; }
  #tip .r { font-family: var(--mono); font-size: 11.5px; color: var(--ink-2); font-variant-numeric: tabular-nums; }

  footer { margin-top: 56px; padding-top: 20px; border-top: 1px solid var(--line); font-size: 12.5px; color: var(--muted); }
  @media (prefers-reduced-motion: reduce) { * { transition: none !important; } }
</style>

<div class="wrap">
<header>
  <p class="eyebrow">Operations research on study time</p>
  <h1>Every student gets the same 24 hours. Almost nobody is told which minutes matter.</h1>
  <p class="sub">Aria models what a student knows, simulates the exam they have not sat yet,
  and allocates the minutes they actually have to the skills that move the score most.
  This is the coach's view; the student sees plain text on WhatsApp.</p>
</header>

<section id="hero"></section>
<section id="thesis"></section>
<section id="policy"></section>
<section id="plan"></section>
<section id="agent"></section>
<section id="cohort"></section>

<footer id="foot"></footer>
</div>
<div id="tip" role="tooltip"></div>

<script>
const DATA = __DATA__;
const S = DATA.student;
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const pct = (v) => Math.round(v * 100) + '%';

/* ---------- tooltip ---------- */
const tip = $('tip');
function showTip(evt, html) {
  tip.innerHTML = html; tip.style.opacity = '1';
  const pad = 14, r = tip.getBoundingClientRect();
  let x = evt.clientX + pad, y = evt.clientY + pad;
  if (x + r.width > innerWidth - 8) x = evt.clientX - r.width - pad;
  if (y + r.height > innerHeight - 8) y = evt.clientY - r.height - pad;
  tip.style.left = x + 'px'; tip.style.top = y + 'px';
}
const hideTip = () => { tip.style.opacity = '0'; };
addEventListener('scroll', hideTip, { passive: true });

/* ---------- hero ---------- */
const p = S.projection;
const gap = S.target - p.total;
$('hero').innerHTML = `
  <div class="tiles">
    <div class="tile">
      <div class="label">Projected score</div>
      <div class="value">${p.total}</div>
      <div class="foot">80% range ${p.low}&ndash;${p.high}</div>
    </div>
    <div class="tile">
      <div class="label">Target</div>
      <div class="value">${S.target}</div>
      <div class="foot">${gap > 0 ? gap + ' points short' : 'on track'}</div>
    </div>
    <div class="tile">
      <div class="label">Chance today</div>
      <div class="value">${pct(p.chanceOfTarget)}</div>
      <div class="foot">of clearing ${S.target}</div>
    </div>
    <div class="tile">
      <div class="label">Time to target</div>
      <div class="value ${S.daysToTarget ? '' : 'sm'}">${S.daysToTarget ? S.daysToTarget + 'd' : 'out of reach'}</div>
      <div class="foot">${S.daysToTarget ? 'at 20 min/day' : 'at 20 min/day'}</div>
    </div>
    <div class="tile">
      <div class="label">Evidence</div>
      <div class="value">${S.attempts}</div>
      <div class="foot">answers observed</div>
    </div>
  </div>`;

/* ---------- thesis: mastery x value scatter ---------- */
(function () {
  const W = 680, H = 380, M = { t: 16, r: 20, b: 46, l: 56 };
  const iw = W - M.l - M.r, ih = H - M.t - M.b;
  const maxPts = Math.max(...S.skills.map(s => s.pointsAvailable), 1);
  const x = m => M.l + m * iw;
  const y = v => M.t + ih - (v / maxPts) * ih;

  let grid = '';
  for (let i = 0; i <= 4; i++) {
    const gx = M.l + (i / 4) * iw;
    grid += `<line x1="${gx}" y1="${M.t}" x2="${gx}" y2="${M.t + ih}" stroke="var(--grid)" stroke-width="1"/>
             <text class="tick" x="${gx}" y="${M.t + ih + 18}" text-anchor="middle">${i * 25}%</text>`;
  }
  for (let i = 0; i <= 4; i++) {
    const v = (maxPts / 4) * i, gy = y(v);
    grid += `<line x1="${M.l}" y1="${gy}" x2="${M.l + iw}" y2="${gy}" stroke="var(--grid)" stroke-width="1"/>
             <text class="tick" x="${M.l - 10}" y="${gy + 3.5}" text-anchor="end">${v.toFixed(0)}</text>`;
  }

  const sorted = [...S.skills].sort((a, b) => a.inPlan - b.inPlan);
  const marks = sorted.map(s => {
    const cx = x(s.mastery), cy = y(s.pointsAvailable);
    const r = 4 + Math.sqrt(s.questionsPerTest) * 2.2;
    const fill = s.inPlan ? 'var(--accent)' : 'var(--muted)';
    const op = s.inPlan ? 0.92 : 0.34;
    return `<circle class="mark" cx="${cx}" cy="${cy}" r="${r}" fill="${fill}" fill-opacity="${op}"
      stroke="var(--surface)" stroke-width="2" tabindex="0" role="img"
      aria-label="${esc(s.name)}, mastery ${pct(s.mastery)}, ${s.pointsAvailable} points available"
      data-t="<b>${esc(s.name)}</b><span class='r'>mastery ${pct(s.mastery)}<br>${s.pointsAvailable} pts available<br>${s.questionsPerTest} questions per test<br>${s.pointsPerMinute} pts/min</span>"/>`;
  }).join('');

  // Label the two skills that make the argument: highest value, and weakest.
  const best = S.skills.reduce((a, b) => b.pointsAvailable > a.pointsAvailable ? b : a);
  const weakest = S.skills.reduce((a, b) => b.mastery < a.mastery ? b : a);
  const labels = [[best, 'highest value'], [weakest, 'weakest skill']]
    .filter(([s], i, arr) => arr.findIndex(([o]) => o.id === s.id) === i)
    .map(([s, tag]) => {
      const cx = x(s.mastery), cy = y(s.pointsAvailable);
      const flip = cx > M.l + iw * 0.62;
      return `<text class="tick" x="${cx + (flip ? -12 : 12)}" y="${cy - 10}"
        text-anchor="${flip ? 'end' : 'start'}" fill="var(--ink)" style="font-size:11px">${esc(s.name)}</text>
        <text class="tick" x="${cx + (flip ? -12 : 12)}" y="${cy + 2}" text-anchor="${flip ? 'end' : 'start'}">${tag}</text>`;
    }).join('');

  $('thesis').innerHTML = `
    <h2>The counter-intuitive part</h2>
    <p class="note">Each circle is one of the ${DATA.skillCount} SAT skills. Left means the student is weaker at it;
    higher means closing it is worth more points. Circle size is how often that skill actually appears on the exam.
    The weakest skill is usually <em>not</em> the best use of the next twenty minutes &mdash; and that is the
    entire argument for planning study time instead of just drilling weaknesses.</p>
    <div class="card">
      <div class="legend">
        <span class="key"><span class="dot" style="background:var(--accent)"></span>Selected for tonight</span>
        <span class="key"><span class="dot" style="background:var(--muted);opacity:.45"></span>Not selected</span>
        <span class="key">Circle size = questions per exam</span>
      </div>
      <figure>
        <svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Scatter of skill mastery against points available">
          ${grid}
          <text class="axis-title" x="${M.l + iw / 2}" y="${H - 6}" text-anchor="middle">Current mastery</text>
          <text class="axis-title" transform="rotate(-90 14 ${M.t + ih / 2})" x="14" y="${M.t + ih / 2}" text-anchor="middle">Points available</text>
          ${marks}${labels}
        </svg>
        <figcaption>Top-left is the sweet spot: not yet learned, and worth a lot. Bottom-left skills are
        genuine weaknesses that simply do not pay &mdash; they are rare on the exam.</figcaption>
      </figure>
    </div>`;
})();

/* ---------- plan + distribution ---------- */
(function () {
  const plan = S.plan;
  const maxP = Math.max(...plan.items.map(i => i.points), 1);
  const rows = plan.items.map(i => `
    <div class="plan-row">
      <div class="plan-name">${esc(i.name)}</div>
      <div class="plan-pts">+${i.points}</div>
      <div class="plan-meta">${i.questions} questions &middot; ${i.minutes} min &middot;
        mastery ${pct(i.masteryBefore)} &rarr; ${pct(i.masteryAfter)}</div>
      <div class="bar"><i style="width:${(i.points / maxP) * 100}%"></i></div>
    </div>`).join('');

  // Distribution
  const W = 420, H = 240, M = { t: 14, r: 12, b: 40, l: 40 };
  const iw = W - M.l - M.r, ih = H - M.t - M.b;
  const h = S.histogram, maxC = Math.max(...h.map(d => d.count), 1);
  const bw = iw / Math.max(h.length, 1);
  const bars = h.map((d, i) => {
    const bh = (d.count / maxC) * ih;
    const inRange = d.score >= S.target;
    return `<rect class="mark" x="${M.l + i * bw}" y="${M.t + ih - bh}" width="${Math.max(bw - 2, 1)}" height="${bh}"
      rx="2" fill="${inRange ? 'var(--good)' : 'var(--accent)'}" fill-opacity="${inRange ? .85 : .75}"
      tabindex="0" role="img" aria-label="${d.count} of 1500 simulations scored near ${d.score}"
      data-t="<b>${d.score}</b><span class='r'>${d.count} of 1500 simulated exams</span>"/>`;
  }).join('');
  const tx = [0, Math.floor(h.length / 2), h.length - 1].filter((v, i, a) => a.indexOf(v) === i && h[v])
    .map(i => `<text class="tick" x="${M.l + i * bw + bw / 2}" y="${M.t + ih + 17}" text-anchor="middle">${h[i].score}</text>`).join('');
  const tgtI = h.findIndex(d => d.score >= S.target);
  const tgtLine = tgtI >= 0 ? `<line x1="${M.l + tgtI * bw}" y1="${M.t - 4}" x2="${M.l + tgtI * bw}" y2="${M.t + ih}"
      stroke="var(--ink)" stroke-width="1.5" stroke-dasharray="3 3"/>
      <text class="tick" x="${M.l + tgtI * bw + 5}" y="${M.t + 6}" fill="var(--ink)">target ${S.target}</text>` : '';

  $('plan').innerHTML = `
    <h2>What tonight is worth</h2>
    <p class="note">The student said they have ${plan.minutes} minutes. The allocator assigns each question to
    whichever skill gains the most projected points from that one question, capped at three skills so the
    session stays coherent.</p>
    <div class="grid2">
      <div class="card">
        ${rows}
        <div class="callout">
          <strong>${plan.totalQuestions} questions, ${plan.minutes} minutes, about +${plan.expectedPoints} points.</strong>
          That is the whole product: not more content, but knowing which content.
        </div>
      </div>
      <div class="card">
        <figure>
          <svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Distribution of 1500 simulated exam scores">
            ${bars}${tgtLine}${tx}
            <text class="axis-title" x="${M.l + iw / 2}" y="${H - 6}" text-anchor="middle">Simulated total score</text>
          </svg>
          <figcaption>1,500 simulated exams drawn from the current mastery estimate.
          Green bars clear the target; the model gives a ${pct(p.chanceOfTarget)} chance today.</figcaption>
        </figure>
      </div>
    </div>`;
})();

/* ---------- learning policy ---------- */
(function () {
  const P = DATA.policy;
  if (!P) return;

  /* Bars are scaled against the widest estimate rather than an absolute
     ceiling: the quantity is a ratio with no natural maximum, and a fixed
     axis would either clip a strong result or squash every weak one. */
  const peak = Math.max(0.6, ...P.profile.map(r => r.durable));
  const bars = P.profile.map(r => {
    const cls = r.episodes === 0 ? 'untested' : (r.durable < 0.45 ? 'weak' : '');
    const meta = r.episodes === 0 ? 'no data'
      : `${r.episodes} session${r.episodes === 1 ? '' : 's'}` +
        (r.checks ? ` / ${r.checks} check${r.checks === 1 ? '' : 's'}` : '');
    return `<div class="lp-row">
      <div class="lp-name">${esc(r.name)}${r.maintenance
        ? ' <span class="lp-meta" style="text-align:left">maintenance</span>' : ''}</div>
      <div class="lp-track mark" tabindex="0"
           onmousemove="showTip(event, '<strong>${esc(r.name)}</strong><br>${esc(r.evidence)}')"
           onmouseleave="hideTip()">
        <div class="lp-fill ${cls}" style="width:${Math.max(2, (r.durable / peak) * 100)}%"></div>
      </div>
      <div class="lp-meta">${r.durable.toFixed(2)} &middot; ${meta}</div>
    </div>`;
  }).join('');

  const d = P.decision;
  const decision = !d ? '<p class="note">No action available &mdash; nothing left in the bank for this student’s priority skills.</p>' : `
    <div class="dh" style="display:flex;align-items:baseline;gap:10px;flex-wrap:wrap">
      <span class="mode ${d.mode}">${d.mode.toUpperCase()}</span>
      <strong style="font-size:15px">${esc(d.skill)}</strong>
      <span class="pill plan">${esc(d.intervention)}</span>
    </div>
    <dl class="dec-grid">
      <dt>Duration</dt><dd>${d.minutes} minutes &mdash; ${d.questions} question${d.questions === 1 ? '' : 's'}</dd>
      <dt>Expected</dt><dd><strong>+${d.expectedPoints} durable points</strong>
        <span class="lp-meta" style="text-align:left">(${d.value}/min, 80% range ${d.low}&ndash;${d.high})</span></dd>
      <dt>Retention</dt><dd>${pct(d.retentionExpected)} expected to survive a delayed check</dd>
      <dt>Risk</dt><dd>${pct(d.risk)} chance this buys almost nothing</dd>
    </dl>
    <div class="evidence" style="font-family:var(--mono);font-size:11.5px;color:var(--ink-2)">
      ${d.reasons.map(r => '&bull; ' + esc(r)).join('<br>')}
    </div>`;

  const shadow = P.shadow.map(r => `
    <tr class="${r.estimated ? 'cf' : ''}">
      <td>${r.estimated ? 'counterfactual estimate' : '<strong>chosen</strong>'}</td>
      <td>${esc(r.skill)}</td>
      <td>${esc(r.intervention)}</td>
      <td class="num">${r.minutes}</td>
      <td class="num" style="${r.estimated ? '' : 'color:var(--accent);font-weight:600'}">${r.value}</td>
      <td class="num">${pct(r.pBest)}</td>
      <td>${esc(r.evidence)}</td>
    </tr>`).join('');

  const regret = P.regret.length ? `
    <div class="card" style="margin-top:18px">
      <h3 style="margin:0 0 4px;font-size:14px">What Aria got wrong</h3>
      <p class="note" style="margin-top:0">Each episode was chosen against a recorded forecast. This is
      <em>estimated policy feedback</em>, not causal inference &mdash; it cannot separate a bad choice from a bad day.</p>
      <table><thead><tr><th>Approach</th><th>Skill</th><th class="num">Expected</th><th class="num">Observed</th><th class="num">Gap</th></tr></thead>
      <tbody>${P.regret.map(r => `<tr>
        <td>${esc(r.name)}</td><td>${esc(r.skill)}</td>
        <td class="num">${r.expected}</td><td class="num">${r.observed}</td>
        <td class="num" style="color:${r.regret > 0 ? 'var(--critical)' : 'var(--good)'}">${r.regret > 0 ? '+' : ''}${r.regret}</td>
      </tr>`).join('')}</tbody></table>
    </div>` : '';

  const ret = P.retention;
  $('policy').innerHTML = `
    <h2>How this student learns</h2>
    <p class="note">Every tutoring system estimates <em>what does the student know</em>. This estimates something
    else: <em>which intervention actually makes them learn</em>. Aria runs small experiments, measures what
    survives a delayed check two days later, and re-plans around the answer. Every approach below started from an
    identical prior &mdash; any ordering here was paid for with observations.</p>

    <div class="grid2">
      <div class="card">
        <h3 style="margin:0 0 12px;font-size:14px">Learning Response Profile</h3>
        <div class="lp">${bars}</div>
        <p class="lp-legend">Durable learning per question, relative to average practice, multiplied by the share
        that survives a delayed check. <strong>1.00</strong> would be an average question fully retained. Hatched
        bars are the untouched prior. Based on ${P.episodeCount} recorded episode${P.episodeCount === 1 ? '' : 's'}
        and ${ret.resolved} resolved check${ret.resolved === 1 ? '' : 's'} (${ret.kept} kept, ${ret.lost} lost,
        ${ret.pending} outstanding).</p>
        ${P.bestTeaching ? `<div class="callout"><strong>Teaches ${esc(S.name.split(' ')[0])} best:
        ${esc(P.bestTeaching.name.toLowerCase())}.</strong> On ${P.bestTeaching.episodes} sessions and
        ${P.bestTeaching.checks} delayed check${P.bestTeaching.checks === 1 ? '' : 's'}${P.bestTeaching.checks
        ? `, ${pct(P.bestTeaching.retention)} of it still there days later` : ''}.</div>` : ''}
        <p class="lp-legend"><em>Spaced review</em> is marked maintenance: it is only ever offered on skills
        already got right, so it is not competing on the same terms and is excluded from &ldquo;what teaches this
        student best&rdquo;. It is shown rather than hidden, because a labelled confound beats a missing one.</p>
        <p class="lp-legend">This is a record of what has moved this student&rsquo;s scores &mdash; not a learning
        style, not a personality type, and not shown to them as one.</p>
      </div>
      <div class="card">
        <h3 style="margin:0 0 6px;font-size:14px">Aria&rsquo;s decision for the next ${S.plan ? DATA.student.plan.minutes : 25} minutes</h3>
        ${decision}
      </div>
    </div>

    <div class="card" style="margin-top:18px">
      <h3 style="margin:0 0 4px;font-size:14px">The roads not taken</h3>
      <p class="note" style="margin-top:0">Every row below the first describes an intervention that was
      <strong>not run</strong>, so nothing can confirm it. Shown anyway: a decision engine that will not say what it
      gave up cannot be argued with.</p>
      <table>
        <thead><tr><th></th><th>Skill</th><th>Approach</th><th class="num">Min</th><th class="num">Pts/min</th><th class="num">P(best)</th><th>Evidence</th></tr></thead>
        <tbody>${shadow}</tbody>
      </table>
    </div>
    ${regret}`;
})();

/* ---------- agent decisions ---------- */
(function () {
  const a = S.agent;
  const items = a.candidates.length ? a.candidates.map((c, i) => `
    <div class="decision ${i === 0 ? 'top' : ''}">
      <div class="dh">
        <span class="trigger">${esc(c.trigger)}</span>
        <span class="score">value ${c.score}</span>
        ${c.skill ? `<span class="pill plan">${esc(c.skill)}</span>` : ''}
        ${i === 0 ? '<span class="pill ok">chosen</span>' : ''}
      </div>
      <p class="reason">${esc(c.reason)}</p>
      <div class="evidence">${esc(c.evidence)}</div>
    </div>`).join('')
    : '<p class="note">Nothing worth saying right now. Staying quiet is a decision too.</p>';

  $('agent').innerHTML = `
    <h2>Why Aria would message, and why now</h2>
    <p class="note">Aria decides when to start a conversation. Every candidate reason is scored, the best one wins,
    and the reasoning is written down before the message goes out &mdash; so when a student replies
    <span style="font-family:var(--mono);font-size:12.5px">WHY</span>, they get the recorded basis for the
    decision rather than a plausible story invented afterwards.</p>
    <div class="card">
      ${items}
      <div class="callout" style="border-left-color:${a.canMessage ? 'var(--good)' : 'var(--warning)'}">
        <strong>Send gate:</strong> ${esc(a.gate)}.
        Rate limits, quiet hours and opt-out are checked before any reason is even considered.
      </div>
    </div>`;
})();

/* ---------- cohort triage ---------- */
(function () {
  const rows = DATA.cohort.map(r => {
    const idle = r.daysIdle >= 3 ? `<span class="pill idle">${r.daysIdle}d idle</span>`
      : `<span class="pill ok">active</span>`;
    const urgent = r.daysUntilTest !== null && r.daysUntilTest <= 21
      ? `<span class="pill risk">${r.daysUntilTest}d to test</span>` : `${r.daysUntilTest ?? '-'}d`;
    return `<tr>
      <td>${esc(r.name)}</td>
      <td class="num">${r.projected}</td>
      <td class="num">${r.target}</td>
      <td class="num" style="color:${r.gap > 100 ? 'var(--critical)' : 'var(--ink-2)'}">${r.gap > 0 ? '-' + r.gap : '+' + (-r.gap)}</td>
      <td>${urgent}</td>
      <td>${idle}</td>
      <td class="num" style="color:var(--accent);font-weight:600">+${r.pointsPerHour}</td>
      <td>${esc(r.topSkill ?? '-')}</td>
      <td>${r.bestApproach
        ? `${esc(r.bestApproach)} <span class="lp-meta" style="text-align:left">${r.approachEpisodes}ep</span>`
        : '<span class="lp-meta" style="text-align:left">learning</span>'}</td>
    </tr>`;
  }).join('');

  const best = DATA.cohort[0];
  $('cohort').innerHTML = `
    <h2>Where an hour of a counsellor's time is worth the most</h2>
    <p class="note">A counsellor in an under-resourced public school can carry four hundred students. The same
    engine that plans one student's evening ranks the whole caseload by how many points an hour of attention
    would actually buy &mdash; which is not the same as ranking by who is furthest behind.</p>
    <div class="card scroll">
      <table>
        <thead><tr>
          <th>Student</th><th class="num">Projected</th><th class="num">Target</th><th class="num">Gap</th>
          <th>Exam</th><th>Status</th><th class="num">Pts / hour</th><th>Top priority</th>
          <th>Works best for them</th>
        </tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
    ${best ? `<div class="callout"><strong>${esc(best.name)}</strong> tops the list at
      <strong>+${best.pointsPerHour} points per hour</strong> of focused work &mdash; not because they are the
      furthest from their target, but because their gap sits in skills the exam asks about most.</div>` : ''}`;
})();

/* ---------- tooltips + footer ---------- */
document.querySelectorAll('[data-t]').forEach(el => {
  const html = el.getAttribute('data-t');
  el.addEventListener('mousemove', e => showTip(e, html));
  el.addEventListener('mouseleave', hideTip);
  el.addEventListener('focus', e => {
    const r = el.getBoundingClientRect();
    showTip({ clientX: r.left + r.width / 2, clientY: r.top }, html);
  });
  el.addEventListener('blur', hideTip);
});

$('foot').innerHTML = `Generated ${esc(DATA.generatedAt)} from the live database &middot;
  ${DATA.skillCount} skills &middot; ${DATA.bankSize} vetted questions &middot;
  cohort figures are simulated students for demonstration &middot;
  score projection is a prep-grade model of a non-adaptive exam, not a College Board score report.`;
</script>
"""


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", action="store_true", help="create the demo cohort")
    ap.add_argument("--phone", help="focus student phone")
    ap.add_argument("--minutes", type=int, default=25)
    args = ap.parse_args()

    from database import init_db
    init_db()

    if args.seed:
        seed_cohort()
    build(args.phone, args.minutes)
