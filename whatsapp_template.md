# WhatsApp message templates

## Why this file exists

Aria's whole claim is that she reaches out first — `autonomy.py` decides that a
skill is decaying, or that a student has been idle while points sit on the
table, and sends a message nobody asked it to send. On WhatsApp Cloud API that
is exactly the thing you are not allowed to do freely.

Meta permits free-form text only inside a **24-hour customer service window**
that opens when the student messages you. Outside it, every outbound message
must be a **template approved in advance**. So the autonomy story does not work
on a hosted number until these are submitted and approved, and approval takes
hours to a day.

Submit at: **WhatsApp Manager → Message templates → Create template**.

## Design rules Meta enforces

Templates get rejected for reasons that are not obvious:

- A variable may not open or close the body, and two variables may not touch.
- A body that is mostly variables reads as a blank cheque and is refused. Keep
  parameters to short values — a name, a count, a skill — never a whole
  sentence.
- `UTILITY` is for messages about something the user is already doing;
  `MARKETING` is for anything promotional and is rate-limited per user. These
  are follow-ups to practice the student opted into, so `UTILITY` is the honest
  category. Expect Meta to reclassify at least one.
- Every variable needs a realistic sample or review fails immediately.

`outreach.compose()` builds free-form text from `decision.reason`, which is
written per-decision and therefore cannot be a template. The templates below
are the constrained version: same trigger, same numbers, fixed skeleton.

## The templates

All five carry the same two quick-reply buttons. Buttons matter more than they
look — a student on a cheap handset over slow data taps once instead of typing,
which is the accessibility argument the rest of the product makes.

Buttons: **`Send me one`** and **`Not now`**

The label is what the student sees; the **payload** is what Aria receives. Send
`GO` as the payload behind *Send me one* — `tutor.GO_WORDS` already accepts it,
so a tap and a typed "go" become the same input and there is no second code
path to keep in sync. *Not now* can carry anything; it lands on the off-script
reply, which is the right behaviour.

---

### 1. `aria_first_nudge` — UTILITY

Trigger: `first_nudge` — signed up, zero attempts.

```
Hi {{1}}, it's Aria. You signed up for SAT practice but haven't tried a
question yet. The first one takes about two minutes and tells me where to
start.
```

| Var | Meaning | Sample |
|---|---|---|
| `{{1}}` | student name | `Zayraa` |

---

### 2. `aria_decay_review` — UTILITY

Trigger: `decay_risk` — mastery is falling toward the threshold.

```
Hi {{1}}, it's Aria. You had {{2}} solid about {{3}} days ago, and it fades
without a refresher. A couple of questions now keeps what you already earned.
```

| Var | Meaning | Sample |
|---|---|---|
| `{{1}}` | student name | `Zayraa` |
| `{{2}}` | skill name | `Linear equations in one variable` |
| `{{3}}` | days since practised | `9` |

---

### 3. `aria_misconception` — UTILITY

Trigger: `misconception_pattern` — same slip repeated.

```
Hi {{1}}, it's Aria. You've made the same slip in {{2}} {{3}} times now. It's
one habit rather than a gap, and it's usually fixable in a single session.
```

Two variables touch in `{{2}} {{3}}`, which Meta may reject. Fallback wording
if it does:

```
Hi {{1}}, it's Aria. The same slip has come up {{2}} times in your recent work
on {{3}}. It's one habit rather than a gap, and it's usually fixable in a
single session.
```

| Var | Meaning | Sample |
|---|---|---|
| `{{1}}` | student name | `Zayraa` |
| `{{2}}` | repeat count | `4` |
| `{{3}}` | skill name | `Percentages` |

---

### 4. `aria_high_value_idle` — UTILITY

Trigger: `high_value_idle` — idle 2+ days with a high-value skill available.
This is the one to demo: it carries the shadow price in plain language.

```
Hi {{1}}, it's Aria. About {{2}} questions on {{3}} is worth roughly {{4}}
points to your score - the biggest single win available to you right now.
```

| Var | Meaning | Sample |
|---|---|---|
| `{{1}}` | student name | `Zayraa` |
| `{{2}}` | questions needed | `6` |
| `{{3}}` | skill name | `Systems of two linear equations` |
| `{{4}}` | projected point gain | `30` |

---

### 5. `aria_test_urgency` — UTILITY

Trigger: `test_urgency` — test within 30 days and below target.

```
Hi {{1}}, it's Aria. Your test is in {{2}} days and you're about {{3}} points
below {{4}}. That is still reachable, but it needs to start this week.
```

| Var | Meaning | Sample |
|---|---|---|
| `{{1}}` | student name | `Zayraa` |
| `{{2}}` | days until test | `18` |
| `{{3}}` | shortfall | `70` |
| `{{4}}` | target score | `1300` |

`autonomy.py` picks between "Still reachable if you start today" and "Let's
lock in the points that are still winnable" depending on feasibility. A
template cannot branch, so this text takes the reachable phrasing and the
infeasible case needs its own template — or, more honestly, needs a human.

## What is still missing in the code

Quick-reply buttons come back through the webhook as `type: "button"`, not
`type: "text"`. `whatsapp_cloud.extract_messages()` handles that now, mapping
the button payload to its text so a tap and the typed word take the same path.
Without it, tapping **Send me one** would get "I can only read text messages
right now" — the accessibility feature failing at the accessibility step.

Sending a template is also not the same call as sending text: it needs
`type: "template"` with a components array, which `send_message()` does not
build. That is unwritten until there is an approved template to send.
