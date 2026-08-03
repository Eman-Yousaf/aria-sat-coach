# Aria — a resource-allocation engine for SAT prep

Free SAT content is already solved. Khan Academy gives away every lesson, and
College Board gives away real practice tests. What a $200/hour tutor actually
sells is not content — it is **knowing which forty minutes to spend**. That
judgement is the part still behind a paywall, and it is the part a student
whose parents cannot buy it never gets.

Aria gives that part away. She runs on WhatsApp, on a shared phone, over 2G.

She is not a quiz bot. She maintains a probabilistic estimate of what a student
knows, simulates the exam they have not yet sat, prices every skill in
*points per minute of study*, and spends the minutes they actually have tonight
on the skills with the highest shadow price.

That is an operations research problem wearing a tutor's clothes:
**state estimation → stochastic simulation → constrained allocation.**

---

## The core claim, in one table

Here is the engine's actual output for one student — reproduce it verbatim with
`python demo.py --gains`. Skills ranked by what one minute spent on them is
worth:

| # | Skill | Mastery | Points | Min | **Points/min** |
|---|-------|--------:|-------:|----:|---------------:|
| 1 | Form, Structure, and Sense | 0.29 | 32.5 | 26 | **1.25** |
| 2 | Words in Context | 0.29 | 31.6 | 26 | **1.22** |
| 3 | Rhetorical Synthesis | 0.33 | 25.6 | 24 | **1.07** |
| 4 | Boundaries | 0.63 | 14.1 | 16 | **0.88** |
| 5 | Equivalent Expressions | **0.70** | 11.4 | 14 | **0.82** |
| 6 | Linear Functions | 0.36 | 18.1 | 24 | 0.75 |
| 7 | Command of Evidence (Textual) | **0.29** | 19.3 | 26 | **0.74** |

Look at rows 5 and 7. The student is **far weaker** at Command of Evidence
(0.29) than at Equivalent Expressions (0.70), and Command of Evidence is worth
**more raw points** (19.3 vs 11.4). Every "practice your weakest subject" app
sends them to Command of Evidence.

Aria sends them to Equivalent Expressions, because it is **cheaper to move** —
7 questions instead of 13. Fewer questions to mastery means more points per
minute. Over a fixed study budget, that ordering is worth real score.

You cannot get that ranking from a leaderboard of weaknesses. You get it from a
counterfactual: *simulate the exam with this skill raised, hold everything else
fixed, and price the difference.*

---

## How it works

```
                 answers
  student  ──────────────►  mastery.py        Bayesian Knowledge Tracing
                            (state estimate)  + forgetting curve
                                   │
                                   │  P(knows skill k) for 29 skills
                                   ▼
                            simulator.py      Monte Carlo over the exam
                                   │          ├─ project()        score distribution
                                   │          ├─ marginal_gains() shadow price / skill
                                   │          └─ plan_session()   allocation over minutes
                                   ▼
             ┌─────────────────────┴─────────────────────┐
             ▼                                           ▼
        tutor.py                                   autonomy.py
        the session, opened with a plan            should Aria speak first?
             │                                           │
             ▼                                           ▼
        bank.py  vetted questions                  outreach.py  message text
             │                                           │
             └──────────────► WhatsApp ◄─────────────────┘
```

### 1. State estimation — `mastery.py`

Bayesian Knowledge Tracing tracks `P(student has learned skill k)`, updated
after every answer through slip and guess probabilities.

Textbook BKT **never forgets**, which is wrong for test prep and — more
usefully — leaves the agent with no principled reason to message a student on
any particular day. So mastery here decays back toward the prior between
sessions, with a half-life that lengthens with each success. That buys two
things at once: the spacing effect, and `days_until_decay_below()`, which is
what lets Aria decide *when* to reach out **on her own**.

State is SQLite, local. Choosing a question is a dictionary lookup, not a
network round-trip — the student waits on their own connection and nothing else.

### 2. Simulation — `simulator.py`

The mastery model is generative, so a mastery vector can be used to *sit the
exam*. 2,000 Monte Carlo runs give a **distribution**, not a point estimate:

```
Projection(total=1130, total_low=1050, total_high=1210, rw=510, math=620)
```

Students are always shown the range. A single number would be a lie about
precision we do not have.

### 3. Allocation — `plan_session()`

Given the minutes a student actually has, allocate them across skills to
maximise expected points. Gains are **concave** — the fifth question on a skill
is worth less than the first — so this is solved by marginal allocation:
repeatedly give the next block of minutes to whichever skill currently has the
highest derivative, re-pricing after each award.

```
StudyPlan(minutes_available=40, expected_points=69.4, total_questions=20)
  Form, Structure, and Sense   7 questions   14 min   +25.7 pts   0.29 → 0.77
  Words in Context             7 questions   14 min   +25.0 pts   0.29 → 0.77
  Rhetorical Synthesis         6 questions   12 min   +18.7 pts   0.33 → 0.75
```

> This replaced a 0/1 knapsack, which was wrong twice over: it modelled each
> skill as an all-or-nothing item, and it returned an **empty plan** for any
> session under 26 minutes — exactly the short sessions a busy student has.

### 4. Autonomy — `autonomy.py`

> The difference between a chatbot and an agent is who starts the conversation,
> and whether the decision to start it was reasoned.

The old build sent every student "time to practice!" on a fixed timer — a cron
job in a tutor's costume. This version asks, per student: *is there something
worth saying right now, and what is it worth?* Every candidate is scored, the
best one wins, and **the reasoning is written to a table**.

| Trigger | Evidence |
|---|---|
| `decay_risk` | a skill they had earned is slipping below usefulness |
| `misconception_pattern` | the same specific error three times or more |
| `high_value_idle` | they have been away and points are sitting on the table |
| `test_urgency` | the exam is close and the plan is behind |
| `first_nudge` | signed up, never practised |

A student — or a judge — can ask Aria **why she messaged**, and get the actual
recorded basis, not a plausible story generated after the fact.

`python demo.py --autonomy` ages a real session by four days and prints the
entire deliberation:

```
  Central Ideas and Details    0.62 -> 0.40   <- below the 0.60 threshold
  Words in Context             0.77 -> 0.63

  WINS   17.2  decay_risk
         You learned Words in Context and it's fading. A few questions now keeps it.
         evidence: mastery 0.63, crosses 0.6 in 1.0d, worth 14 pts

          6.0  high_value_idle
         13 questions on Boundaries is worth about 34 points.
         evidence: idle 4.0d, top skill rw_boundaries at 1.33 pts/min

  Send gate: PASS - ok
```

Two candidates, scored against each other, one wins — and it is the *cheaper*
intervention that wins, not the one worth more raw points. Nothing here mocks
the clock: the demo moves the student's rows back in time, so the real decay
curve and real thresholds run unmodified.

Message text is deterministic. The LLM only warms the phrasing, and if it is
unavailable the deterministic version ships unchanged. An outage should cost
tone, never correctness.

### 5. The question bank — `bank_build.py` (offline) / `bank.py` (runtime)

Generated questions are worthless if the answer key is wrong, and a model
grading its own work is not a check — it agrees with itself.

So every item is **re-solved from scratch by a different model that never sees
the proposed key**. Two independent solves must agree with it. Any disagreement
discards the item. Rejected items stay in `bank_raw.jsonl` with the solver's
picks, so the failure rate is auditable rather than invisible.

Each distractor is tagged with the **misconception it encodes** (`sign_error`,
`combined_unlike_terms`, `contradicts_the_passage`, …), which is what lets Aria
name a mistake instead of marking it wrong, and recognise it when it recurs.

---

## Bugs found and fixed — the ones worth admitting

Written down because they are the difference between a demo and a system.

| Bug | Why it mattered |
|---|---|
| Planner returned an **empty plan** under 26 minutes | Killed exactly the short sessions the target student has |
| **Every** generated question had its answer at option A | 100% of accepted items. Fixed with a deterministic shuffle at compile time, so position is uniform by construction rather than by hoping the prompt behaves |
| Reading questions generated with **no passage** | Unanswerable, and it read fine until you tried to answer one |
| Forward projections assumed students never answer wrong and never forget | Turned a projection into a fantasy |
| RAG path returned the **same nearest neighbour forever** | Instantly visible in a demo. `bank.py` now tracks served ids per student |

---

## Honest limitations

Stated here rather than buried, because a judge who finds them unaided should
find them already acknowledged.

- **The score model approximates a non-adaptive exam.** The real digital SAT is
  module-adaptive: Module 2 difficulty depends on Module 1 performance. Aria
  models the non-adaptive approximation and converts raw→scaled through a
  published-style anchor table. It is **prep-grade, and caps around 1500** — not
  a College Board score report.
- **Projections are withheld until 8+ answers of evidence.** Below that the
  posterior is dominated by the prior and any number shown would be noise
  wearing a decimal point.
- **Student-facing copy always shows a range**, never a single number.
- **The question bank is model-generated**, not College Board licensed. The
  two-model disagreement gate raises the floor; it does not make it official
  material.

---

## Run it

No API key and no network needed for the demo — the mastery model, simulator
and planner are pure computation over a local SQLite file.

```bash
git clone https://github.com/Eman-Yousaf/aria-sat-coach
cd aria-sat-coach
python -m venv .venv && .venv\Scripts\activate      # Windows
# source .venv/bin/activate                          # macOS / Linux
pip install -r requirements.txt

python demo.py                # full agent loop, scripted student
python demo.py --gains        # the table above: every skill priced per minute
python demo.py --autonomy     # skip 4 days, watch Aria decide to speak first
python demo.py --chat         # talk to Aria yourself in the terminal
python dashboard.py --seed    # build a demo cohort
python dashboard.py           # write dashboard.html from the live database
python web.py                 # browser chat + coach view at :8000
```

### On WhatsApp, for real

```bash
npm install                   # whatsapp-web.js, puppeteer
python main.py                # spawns the bridge; scan the QR once
```

`main.py` is the same engine: inbound messages go to `tutor.handle()`, and a
scheduler asks `autonomy.decide()` whether any student is worth messaging. The
session caches in `.wwebjs_auth/` (gitignored), so the QR is a one-time step.

This needs a real browser and a linked phone, so it runs on a machine you
control rather than in the deployed container.

### Generating questions

An API key is needed only to **generate** questions or to warm outreach
phrasing:

```bash
copy .env.example .env        # then add a key
python bank_build.py          # rebuild the bank (resumable)
python bank_build.py --report # coverage, misconceptions, answer-position balance
```

---

## Two audiences, one engine

Students get **plain text on WhatsApp** — no markdown, no emoji carrying
meaning, short messages. That is not a limitation to apologise for; it is what
survives a shared phone on a slow connection, which is the device the students
who need this most actually have.

The **web dashboard is for the counsellor**, and it exists because of a
different scarcity: one counsellor, four hundred students, one hour. Which
students does that hour move the most? That is the same allocation problem as
`plan_session()`, one level up — and the person with a desktop and a caseload
is the one who needs a screen.

---

## Files

| File | Purpose |
|---|---|
| `skills.py` | 29 College Board skills (11 R&W, 18 Math) with real digital-SAT question weights |
| `mastery.py` | BKT + forgetting curve over SQLite |
| `simulator.py` | Monte Carlo projection, `marginal_gains()`, `plan_session()` |
| `bank.py` | Runtime question serving; never repeats an item per student |
| `bank_build.py` | Offline generator with the two-model verification gate |
| `student.py` | Durable profiles: target score, test date, opt-out |
| `autonomy.py` | Scored outreach decisions with written-down rationale |
| `outreach.py` | Decision → message text |
| `tutor.py` | The session, opened with a plan rather than a subject menu |
| `dashboard.py` | Counsellor triage view → `dashboard.html` |
| `demo.py` | Full offline run, shadow-price table, autonomy time-skip |
| `web.py` | FastAPI front door: browser chat + `/dashboard`, same tutor |
| `main.py` / `whatsapp.py` | WhatsApp transport |

## Deploying

The `Dockerfile` builds the hosted app and runs on Railway, Render, Fly or any
container host — it reads `$PORT`, runs as a non-root user, and keeps its
SQLite file on a writable volume at `/data`.

`requirements.txt` is the runtime set. `chromadb` and `sentence-transformers`
are deliberately absent — they serve only `sat_rag.py`, the RAG path `bank.py`
replaced, which nothing that runs imports; carrying them drags `torch` in for
roughly 2 GB that never executes. The bank generator's SDKs are separate, in
`requirements-build.txt`.

`question_bank.json` and `dashboard.html` are baked in as build artifacts.
Regenerating the bank at boot would need an LLM key, take hours, and serve
different questions on every deploy.

See [DEPLOY.md](DEPLOY.md) for the full walkthrough, including the
`DASHBOARD_TOKEN` the counsellor view requires when reached over a network.
</content>
