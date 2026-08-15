# Demo video script

**Target: 3:00.** Sections are marked with times; if the limit is shorter, cut
§5 first, then §4. Never cut §2 — it is the whole argument.

Everything quoted below is real output, captured from the shipped build. Do not
retype it from memory; run the commands and film the screen.

## Before you record

```bash
python dashboard.py --seed         # a cohort for the coach view
python demo.py --gains             # check it prints; you will film this
```

Open two things: the live chat at
https://aria-sat-coach.azurewebsites.net and a terminal in the repo. Set the
terminal font large enough to read on a phone — judges watch on laptops, but
assume worse.

---

## §1 — The problem (0:00–0:25)

**On screen:** the live chat page, empty.

> Free SAT content is already solved. Khan Academy gives away every lesson.
> College Board gives away real practice tests.
>
> What a two-hundred-dollar-an-hour tutor actually sells isn't content. It's
> knowing which forty minutes to spend. That's the part still behind a paywall,
> and it's the part a student whose parents can't buy it never gets.
>
> This is Aria. She gives that part away, on WhatsApp.

---

## §2 — The core claim (0:25–1:15)

**On screen:** type into the live chat, slowly enough to read.

Type: **`i have no time`**

Aria replies:

```
Hi! I'm Aria, your SAT coach. 5 minutes is enough to be worth spending well.

What should I call you?
```

> "No time" is the most common answer a busy student gives, and most apps treat
> it as an error and ask again. Aria treats it as an answer. Five minutes is
> real, and it's the whole argument.

Type: **`Zayraa`** → then **`1300`**

```
5 minutes. Here's the best use of them:

1. Boundaries - 1 question, worth about 6 points
2. Form, Structure, and Sense - 1 question, worth about 6 points

Total: about +12 points.

Ready? Reply GO.
```

> Not a subject menu. A plan, priced. She's claiming those five minutes are
> worth about twelve points — and she can show her working.

**Cut to terminal:** `python demo.py --gains`

Hold on rows 5 and 7 of the table. Point at them.

> Every skill, priced in points per minute of study.
>
> Look at these two rows. This student is far weaker at Command of Evidence —
> mastery 0.29 — than at Equivalent Expressions at 0.70. And Command of
> Evidence is worth more raw points: nineteen against eleven.
>
> Every "practise your weakest subject" app sends them to Command of Evidence.
>
> Aria sends them to Equivalent Expressions, because it's *cheaper to move* —
> seven questions instead of thirteen. Over a fixed budget, that ordering is
> worth real score.
>
> You can't get that from a list of weaknesses. You get it from a
> counterfactual: simulate the exam with one skill raised, hold everything else
> fixed, and price the difference.

---

## §3 — Agent, not chatbot (1:15–2:00)

**On screen:** terminal. `python demo.py --autonomy`

> The difference between a chatbot and an agent is who starts the conversation
> — and whether the decision to start it was reasoned.
>
> Aria's model of what a student knows *decays* between sessions. That's what
> gives her a principled reason to speak on a particular day, rather than on a
> timer.

Let the deliberation print. Hold here:

```
  WINS   17.2  decay_risk
         You learned Words in Context and it's fading.
         evidence: mastery 0.63, crosses 0.6 in 1.0d, worth 14 pts

          6.0  high_value_idle
         evidence: idle 4.0d, top skill rw_boundaries at 1.33 pts/min
```

> Two reasons to reach out, scored against each other. The cheaper intervention
> wins — not the one worth more raw points.
>
> And the reasoning is written to a table *before* the message goes out. A
> student can ask why she messaged and get the recorded basis, not a plausible
> story generated afterwards.
>
> Nothing here mocks the clock. The demo moves the student's rows back four
> days, so the real decay curve runs unmodified.

---

## §4 — Accessibility (2:00–2:35)

**On screen:** back to the live chat, on a phone if you can film one.

Tap the mic button. Say out loud: **"I have twenty minutes today."**

Aria echoes what she heard, then answers.

> Every choice here assumes one thing: the student is on a borrowed Android
> phone with a cracked screen and four hundred megabytes of data left.
>
> So: WhatsApp, not an app — nothing to install, nothing to sign up for.
>
> They can talk instead of typing. Typing is a tax, and it falls hardest on a
> shared handset with the keyboard in the wrong script.
>
> She repeats back what she heard, because transcription is confidently wrong
> sometimes — and a student who sees "sex" for "six" needs to know why their
> answer was marked wrong, instead of concluding that they were.
>
> This page is ten kilobytes. One file, no CDN, no web fonts, no framework. And
> choosing a question is a dictionary lookup on a local database, not a network
> round trip — so the student waits on their own connection and nothing else.

---

## §5 — Trust (2:35–3:00)

**On screen:** `bank_raw.jsonl` scrolling, or the `--report` output.

> Generated questions are worthless if the answer key is wrong, and a model
> grading its own work isn't a check — it agrees with itself.
>
> So every question is re-solved from scratch by a *different* model that never
> sees the proposed answer. Two independent solves have to agree with it.
>
> That gate caught thirty-two wrong answer keys in the build that shipped.
> Three hundred and ninety-six questions survived it, across all twenty-nine
> skills.
>
> And the score model caps around 1500, projections are withheld until there
> are eight real answers of evidence, and students are always shown a range —
> never a single number. Because a single number would be a lie about precision
> we don't have.

**Final card:** the URL, and the repo link.

---

## Notes for the edit

- **Do not speed up the typing.** The pauses where Aria answers are the product.
- Film §2's table at full resolution and zoom in in post; it is the one thing a
  judge may pause on.
- If a take produces a question you have already seen, say so on camera rather
  than re-recording — Aria never repeats an item per student, and that is worth
  more said out loud than hidden.
- The 24-hour-window limitation on hosted WhatsApp outreach belongs in the
  written submission, not the video. Do not claim live proactive nudges on the
  hosted number until the templates are approved.
