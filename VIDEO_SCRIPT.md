# 3-minute video

One idea, said three times: **Aria does not just model what you know — she
works out what makes *you* learn, and changes her own teaching because of it.**

Everything below is real output. Nothing is mocked.

---

## Before you hit record

Run these once. They take a couple of minutes and you do **not** want them
running live.

```bash
cd aria-sat-coach

# 1. The discovery run -> a text file you scroll through on camera.
python discover.py --short > run.txt

# 2. The dashboard, with the seeded cohort.
python dashboard.py --seed
python dashboard.py
```

Then open, ready to switch between:

- **Window A** — `run.txt` in an editor, scrolled to the top. Dark theme, font
  bumped to ~16pt so section headers are readable at 1080p.
- **Window B** — `dashboard.html` in a browser, scrolled to the top.

Record at 1920×1080. Do not show your file tree or terminal prompt path.

---

## 0:00 – 0:20 · The claim

**On screen:** `run.txt`, top of file. Section 1.

> "Every AI tutor estimates the same thing: what does the student know. Aria
> estimates something else as well — which kind of teaching actually makes
> *this* student learn. Here she is meeting a student called Priya for the
> first time."

**Point at:** the Learning Response Profile where every approach reads `0.50`
and `no data`.

> "Eight teaching approaches. All at exactly the same value, because she has
> no evidence yet. That's deliberate — if she started with a favourite, the
> next two minutes would be her reciting it, not discovering it."

---

## 0:20 – 0:55 · She admits she doesn't know

**Scroll to:** Section 2, `ARIA'S DECISION [EXPLORE]`.

> "First decision. Note the mode: EXPLORE. She's not pretending. She picks the
> approach she'd learn most from, and she prices it honestly —"

**Point at, in order:**
- `Expected +11.9 durable points (1.70/min, 80% range …)` — "a range, not a
  number."
- `Risk 3% chance this buys almost nothing`
- the counterfactual table — "and here's what she gave up, labelled as
  estimates, because nobody ran them."

---

## 0:55 – 1:35 · A week of study, and the delayed checks

**Scroll to:** Section 3, then 4.

> "Now a week of short sessions, driven through the real tutor — she sees only
> the letters the student types."

**Point at:** the episode list, specifically a row where the multiplier is
negative.

> "She's measuring how much each question taught, relative to average practice.
> Some of these went backwards."

**Scroll to Section 4** and land hard here:

> "This is the part I care about. Getting five right straight after an
> explanation proves the explanation was still on screen. So two days later she
> comes back and asks one question."

**Point at:** the per-approach kept/checked counts.

> "Hint-first practice looked *excellent* on the day. Two days later it was
> gone. Aria caught that, and demoted it."

---

## 1:35 – 2:10 · The profile, paid for

**Scroll to:** Section 5.

> "Same eight bars as thirty seconds ago."

Pause. Let them read it.

> "Worked examples on top. Timed drills at the bottom. She was never told any
> of this — she inferred it from answers, and she can show you the sessions
> behind every bar."

---

## 2:10 – 2:35 · She got it right — and you can check

**Scroll to:** Section 6, then jump to the very bottom of the file.

> "Same question as the very start: twenty minutes, what do we do. Different
> answer — and nothing in the code changed between them. The evidence did."

**Then the last two lines.** This is the strongest single frame in the video:

```
  For Priya: worked example then practice.
  On the evidence of 3 sessions and 3 delayed checks.
  The hidden truth at the top of this file says worked_example.
```

> "And here's the check. The simulated student has hidden traits written at the
> top of the file — Aria never reads them. She recovered the right answer from
> nothing but the letters this student typed."

**Optional, if you have room:** scroll up to Section 8, *What Aria got wrong* —
the forecast-versus-outcome table. "She also keeps score of her own
predictions."

---

## 2:35 – 2:55 · The screen a counsellor gets

**Switch to Window B.** Scroll to *How this student learns*.

> "Same engine, counsellor's view."

**Point at, fast:**
- the profile bars, hatched = never tried
- the decision card with its `EXPLORE` badge
- **the cohort table's last column** — *this is the money shot*

> "Twelve students. Look at the last column. Worked examples for one,
> misconception correction for another, explanation-first for a third. Same
> weak skills, different prescriptions — because they're different people."

---

## 2:55 – 3:00 · Close

> "Aria doesn't just work out what a student doesn't know. She runs experiments
> to find out how they learn, checks two days later whether it stuck, and
> spends their limited time on what actually works for them."

---

## What to cut if you run long

In order: Section 7 (the time table), the episode list at 0:55, then the risk
line. Never cut Section 4 (retention), the hidden-truth reveal, or the cohort
column — those three are what nobody else will have.

**On Section 7.** For this seed the budget does not change the answer, so do
not promise on camera that it will. If you want the point made, say it over the
profile instead: *"a worked example spends three minutes before the first
question, so it has to earn them back — which is why the ranking is per minute,
not per session."* The mechanism is real (`tests/test_counterfactual.py::
test_the_budget_changes_the_chosen_approach` asserts it); this particular
student just is not the case that shows it off.

## Things not to say

- Don't say "Bayesian", "Thompson sampling" or "posterior" on camera. Say
  "she's not sure yet", "she runs a small experiment", "a range not a number".
- Don't claim the delayed check is proof. It's one question. Say "evidence".
- Don't call it a learning style. It's a record of what has moved this
  student's scores.

## If a judge asks "is this real or a script?"

```bash
python -m pytest tests/ -q      # 86 tests
```

Then open `discover.py` and show `TRUE_ACCURACY` / `TRUE_RETENTION` at the top:
the simulated student's hidden traits. Aria never reads them. Change a number,
rerun, and she reaches a different conclusion by the same route.
