# Aria — AceSAT submission

**A WhatsApp SAT coach that decides which forty minutes are worth spending.**

Live: https://aria-sat-coach.azurewebsites.net
Code: https://github.com/Eman-Yousaf/aria-sat-coach

---

## The one-paragraph version

Free SAT content is already solved — Khan Academy gives away every lesson and
College Board gives away real practice tests. What a $200/hour tutor actually
sells is not content. It is **knowing which forty minutes to spend**, and that
judgement is the part still behind a paywall. Aria gives that part away. She
keeps a probabilistic estimate of what a student knows, simulates the exam they
have not yet sat, prices every skill in *points per minute of study*, and
spends the minutes a student actually has tonight on the skills with the
highest return. She lives on WhatsApp, so reaching her costs no app install, no
signup, and almost no data.

---

## Impact

The students who most need a tutor are the ones who can least afford one. The
gap is not access to material — it is access to **prioritisation**. A student
with twenty minutes and eleven weak skills does not need another lesson list;
they need to know which of the eleven to open. Getting that wrong is how a
motivated student studies for three months and moves forty points.

Aria's answer is a number, not a vibe. For one real student:

| # | Skill | Mastery | Points available | Min to earn | **Points/min** |
|---|-------|--------:|-----------------:|------------:|---------------:|
| 5 | Equivalent Expressions | **0.70** | 11.4 | 14 | **0.82** |
| 7 | Command of Evidence (Textual) | **0.29** | 19.3 | 26 | **0.74** |

The student is far weaker at Command of Evidence and it is worth more raw
points. **Every "practise your weakest subject" app sends them there.** Aria
sends them to Equivalent Expressions, because it is cheaper to move — 7
questions instead of 13 — and over a fixed budget that ordering is worth real
score. You cannot get that ranking from a list of weaknesses. You get it from a
counterfactual: simulate the exam with this one skill raised, hold everything
else fixed, and price the difference.

Reproduce it with `python demo.py --gains`.

---

## Innovation — an agent, not a chatbot

> The difference between a chatbot and an agent is who starts the conversation,
> and whether the decision to start it was reasoned.

A chatbot waits. Aria decides. The mastery model decays between sessions, which
is what gives her a principled reason to speak on a *particular* day rather than
on a timer. Every candidate reason is scored against every other, the best one
wins, and **the reasoning is written to a table before the message goes out**.

| Trigger | Evidence |
|---|---|
| `decay_risk` | a skill they had earned is slipping below usefulness |
| `misconception_pattern` | the same specific error three times or more |
| `high_value_idle` | they have been away and points are sitting on the table |
| `test_urgency` | the exam is close and the plan is behind |
| `first_nudge` | signed up, never practised |

`python demo.py --autonomy` ages a real session four days and prints the whole
deliberation:

```
  Words in Context             0.77 -> 0.63

  WINS   17.2  decay_risk
         You learned Words in Context and it's fading.
         evidence: mastery 0.63, crosses 0.6 in 1.0d, worth 14 pts

          6.0  high_value_idle
         evidence: idle 4.0d, top skill rw_boundaries at 1.33 pts/min

  Send gate: PASS - ok
```

Two candidates, scored, and the **cheaper** intervention wins rather than the
one worth more raw points. Nothing mocks the clock — the demo moves the
student's rows back in time so the real decay curve runs unmodified.

A student can ask Aria *why she messaged* and get the recorded basis, not a
plausible story generated afterwards.

---

## Accessibility

Every choice here is downstream of one assumption: **the student is on a
borrowed Android phone with a cracked screen and 400 MB of data left.**

- **WhatsApp, not an app.** No install, no account, no app-store friction. It
  is already on the phone and already whitelisted on cheap data plans.
- **They can talk instead of typing.** Voice notes go through `voice.py` to a
  Whisper deployment and reach the tutor as ordinary text. Typing is a tax that
  falls hardest on a shared handset with the keyboard in the wrong script.
- **They can tap instead of either.** Quick-reply buttons carry payloads the
  tutor already understands, so a tap and a typed "go" are one input.
- **Aria repeats back what she heard.** Transcription is confidently wrong
  sometimes, and a student who sees "sex" for "six" needs to know why their
  answer was marked wrong rather than concluding they were.
- **The web page is ~10 KB, single file.** No CDN, no web fonts, no framework,
  light and dark, 16px inputs so iOS does not zoom. It renders on 2G.
- **Nothing needed for a question is on the network.** The mastery model,
  simulator, planner and question bank are local computation over SQLite.
  Choosing a question is a dictionary lookup. The student waits on their own
  connection and nothing else.
- **No dead ends.** Aria never asks the same question the same way twice; the
  student who cannot phrase an answer the way a parser wants is exactly the
  student least likely to try a third time.

---

## Technical execution

**State estimation → stochastic simulation → constrained allocation.**

1. **`mastery.py`** — Bayesian Knowledge Tracing over 29 College Board skills,
   updated after every answer through slip and guess probabilities. Textbook BKT
   never forgets, which is wrong for test prep, so mastery decays toward the
   prior between sessions with a half-life that lengthens with each success.
   That buys the spacing effect and `days_until_decay_below()` in one move.
2. **`simulator.py`** — the mastery model is generative, so a mastery vector can
   sit the exam. 2,000 Monte Carlo runs give a distribution, not a point
   estimate. Students always see a range; a single number would be a lie about
   precision we do not have.
3. **`plan_session()`** — gains are concave (the fifth question on a skill is
   worth less than the first), so minutes are allocated marginally: repeatedly
   award the next block to whichever skill has the highest derivative, re-pricing
   after each award. This replaced a 0/1 knapsack that returned an **empty plan**
   for any session under 26 minutes — exactly the short sessions a busy student
   has.
4. **`bank_build.py`** — every question is re-solved from scratch by a
   *different model that never sees the proposed key*, and two independent
   solves must agree with it. The build that produced the shipped bank caught
   **32 wrong answer keys**. Rejects stay on disk with the solver's picks, so
   the failure rate is auditable rather than invisible. Each distractor is
   tagged with the misconception it encodes, which is what lets Aria name a
   mistake instead of just marking it wrong.

**396 questions across all 29 skills.** 60.3% acceptance rate through the
verification gate.

### Things that were wrong and are now right

Listed because they are the difference between a demo and a system.

| Bug | Why it mattered |
|---|---|
| Planner returned an empty plan under 26 minutes | Killed exactly the short sessions the target student has |
| **Every** generated question had its answer at option A | 100% of accepted items. Fixed with a deterministic shuffle at compile time, so position is uniform by construction rather than by hoping the prompt behaves |
| 23 reading questions were unanswerable | "Which transition best completes the text?" over a passage with no gap. The verifier could not catch it — asked to choose, a model chooses, and two runs agree |
| The webhook accepted unsigned requests when no app secret was set | An open write endpoint: anyone with the URL could post as any phone number, corrupt that student's record, and read the replies |
| Session ids were normalised by stripping non-digits | Fine for phone numbers, which is what it was for. Applied to a web session id it discarded most of the entropy, and an id with no digits collapsed to the empty string — where students share a session and read each other's questions |
| "I have 20 minutes" as a first message was ignored | The minutes were dropped and asked for again three turns later — Aria visibly not listening |

---

## Honest limitations

Stated here rather than buried, because a judge who finds them unaided should
find them already acknowledged.

- **The score model approximates a non-adaptive exam.** The real digital SAT is
  module-adaptive. Aria models the non-adaptive approximation and converts
  raw→scaled through a published-style anchor table. It is prep-grade and caps
  around 1500 — not a College Board score report.
- **Projections are withheld until 8+ answers.** Below that the posterior is
  dominated by the prior, and any number shown would be noise wearing a decimal
  point.
- **The question bank is model-generated**, not College Board licensed. The
  two-model disagreement gate raises the floor; it does not make the items
  official material.
- **Proactive outreach on a hosted WhatsApp number is capped by Meta's 24-hour
  window.** Outside it, nudges require pre-approved templates. The five matching
  Aria's triggers are drafted and awaiting review; until they clear, autonomy is
  demonstrable on the local transport and in `demo.py --autonomy` but not on the
  hosted number.

---

## Try it

```bash
git clone https://github.com/Eman-Yousaf/aria-sat-coach
cd aria-sat-coach
pip install -r requirements.txt

python demo.py --gains        # every skill priced per minute of study
python demo.py --autonomy     # skip 4 days, watch Aria decide to speak first
python demo.py --chat         # talk to her in the terminal
```

No API key and no network needed for any of those — the engine is pure
computation over a local SQLite file.

Or open **https://aria-sat-coach.azurewebsites.net** and say *"I have 20
minutes"*.
