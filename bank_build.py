"""Offline question-bank generator.

Builds `question_bank.json`: SAT practice questions tagged by skill, with every
wrong option mapped to the specific misconception it encodes.

Run this once, ship the JSON. Nothing here executes at chat time -- the live
agent only reads the finished file. That keeps question delivery instant and
immune to API outages during a demo, and it makes the bank an inspectable
artifact rather than an opaque runtime behaviour.

Quality control is the whole point of this script. LLMs write plausible SAT
questions with wrong answer keys a meaningful fraction of the time, so every
generated item is re-solved from scratch by a *different* model that never sees
the proposed key. Disagreement means the item is discarded. Generating with one
model and checking with another matters: asking the same model to grade itself
mostly reproduces its original mistake.

Usage:
    python bank_build.py --pilot          # small run, prints a quality report
    python bank_build.py                  # full run (resumable)
    python bank_build.py --report         # summarise the existing bank
"""

import argparse
import json
import os
import random
import re
import sys
import time
from collections import Counter

import config
from config import GROQ_API_KEY
from skills import SKILLS, SKILL_BY_ID

# Maths needs a reasoning model: llama-3.3-70b writes fluent algebra questions
# with the wrong key roughly 8 times in 9, which burns quota on rejections.
# Reading and Writing is the opposite way round -- prose quality matters more
# than arithmetic, and the reasoning model is slower for no benefit.
GROQ_MODEL_MATH = "openai/gpt-oss-120b"
GROQ_MODEL_RW = "llama-3.3-70b-versatile"
GROQ_MODEL_VERIFIER = "openai/gpt-oss-120b"

# Azure meters by quota on the deployment rather than tokens-per-day per model,
# so a build can run to completion in one sitting. Deployment names come from
# config because they are chosen when the deployment is created.
if config.USE_AZURE:
    GENERATOR_MODEL_MATH = config.AZURE_DEPLOYMENT_MATH
    GENERATOR_MODEL_RW = config.AZURE_DEPLOYMENT_RW
    VERIFIER_MODEL = config.AZURE_DEPLOYMENT_VERIFIER
else:
    GENERATOR_MODEL_MATH = GROQ_MODEL_MATH
    GENERATOR_MODEL_RW = GROQ_MODEL_RW
    VERIFIER_MODEL = GROQ_MODEL_VERIFIER

DIFFICULTIES = ["easy", "medium", "hard"]

DIFFICULTY_NOTE = {
    "easy": "one step, no distractor requires real reasoning to eliminate. A "
            "student who knows the concept answers in under 30 seconds.",
    "medium": "two or three steps, and at least two distractors are traps a "
              "student who half-knows the material would fall for.",
    "hard": "multi-step, or requires combining two ideas. The wrong options "
            "must be the results of genuinely tempting reasoning errors, not "
            "obviously wrong values. Should defeat most students scoring below 650.",
}

# Passage registers, sampled per batch. Without this the generator writes the
# same municipal-notice prose every time.
TOPIC_POOL = [
    "an excerpt from 19th-century literary fiction",
    "a contemporary short story with an unresolved tension",
    "a popular-science account of a recent biology or astronomy finding",
    "a historical account of a social movement or reform",
    "a passage from a primary-source political speech or founding document",
    "an economics or sociology argument citing a study",
    "a naturalist's field observation of animal behaviour",
    "an art or music criticism piece making an evaluative claim",
    "an archaeology or anthropology account of a discovery",
    "a debate between two named researchers who disagree",
]
QUESTIONS_PER_CELL = 5          # per skill per difficulty -> 435 target, 15/skill
FLOOR = 2                       # every skill reaches this before any goes deeper
BATCH_SIZE = 3                  # questions requested per generation call
VERIFIER_VOTES = 2              # independent solves that must agree with the key

RAW_PATH = "bank_raw.jsonl"     # every candidate, resumable checkpoint
OUT_PATH = "question_bank.json"

_client = None


def client():
    """Groq and Azure OpenAI both expose the OpenAI chat-completions surface,
    so the rest of this file is written against that one interface."""
    global _client
    if _client is None:
        if config.USE_AZURE:
            from openai import AzureOpenAI
            missing = [n for n, v in (
                ("AZURE_DEPLOYMENT_MATH", config.AZURE_DEPLOYMENT_MATH),
                ("AZURE_DEPLOYMENT_RW", config.AZURE_DEPLOYMENT_RW),
                ("AZURE_DEPLOYMENT_VERIFIER", config.AZURE_DEPLOYMENT_VERIFIER),
            ) if not v]
            if missing:
                raise SystemExit(
                    "Azure endpoint is set but these deployment names are not: "
                    + ", ".join(missing))
            _client = AzureOpenAI(
                azure_endpoint=config.AZURE_OPENAI_ENDPOINT,
                api_key=config.AZURE_OPENAI_API_KEY,
                api_version=config.AZURE_OPENAI_API_VERSION,
            )
        else:
            from groq import Groq
            _client = Groq(api_key=GROQ_API_KEY)
    return _client


# The GPT-5 family rejects `max_tokens` (wants `max_completion_tokens`) and
# rejects any `temperature` other than the default. Rather than hardcode which
# deployment is which -- deployment names are ours to choose, so the name tells
# us nothing about the model behind it -- learn it from the first rejection and
# remember it per model.
_UNSUPPORTED: dict[str, set[str]] = {}


def _chat(model: str, messages: list, max_tokens: int = 2048,
          temperature: float = 0.8, retries: int = 4) -> str:
    """Chat call with backoff, against Groq or Azure OpenAI.

    Reasoning models put prose in `reasoning`, so fall back to that field when
    `content` comes back empty."""
    delay = 2.0
    last = None
    for _ in range(retries):
        quirks = _UNSUPPORTED.setdefault(model, set())
        kwargs = {"model": model, "messages": messages}
        if "max_tokens" in quirks:
            kwargs["max_completion_tokens"] = max_tokens
        else:
            kwargs["max_tokens"] = max_tokens
        if "temperature" not in quirks:
            kwargs["temperature"] = temperature
        try:
            resp = client().chat.completions.create(**kwargs)
            msg = resp.choices[0].message
            text = (msg.content or "").strip()
            if not text:
                text = (getattr(msg, "reasoning", None) or "").strip()
            if text:
                return text
            last = "empty response"
        except Exception as e:
            last = f"{type(e).__name__}: {e}"
            detail = str(e).lower()
            # Retry immediately on a parameter the model does not accept; this
            # costs one wasted call per model per parameter, not per request.
            learned = False
            for param in ("max_tokens", "temperature"):
                if param not in quirks and (
                        f"'{param}' is not supported" in detail
                        or f"unsupported parameter: '{param}'" in detail
                        or (f"'{param}'" in detail and "unsupported" in detail)):
                    quirks.add(param)
                    learned = True
            if learned:
                continue
            if "rate" in detail or "429" in detail:
                time.sleep(delay)
                delay = min(delay * 2, 60)
                continue
            time.sleep(delay)
            delay = min(delay * 2, 30)
    print(f"    ! call failed: {last}", flush=True)
    return ""


def _extract_json(text: str):
    """Pull the first JSON array or object out of a model response."""
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    for opener, closer in (("[", "]"), ("{", "}")):
        start = text.find(opener)
        if start == -1:
            continue
        depth = 0
        for i in range(start, len(text)):
            if text[i] == opener:
                depth += 1
            elif text[i] == closer:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except json.JSONDecodeError:
                        break
    return None


GEN_SYSTEM = """You write practice questions for the digital SAT.

THE CRITICAL RULE: every question must be answerable using only what you write.
A question that refers to "the passage" without including that passage, or asks
for "the main point" of nothing, is worthless. If answering requires a text,
you must write that text in the `passage` field.

Output rules, all mandatory:
- Plain text only. No LaTeX, no markdown, no unicode math symbols. Write powers
  as x^2, fractions as 3/4, and multiplication as *.
- Reading and Writing questions REQUIRE a `passage` of 50-90 words that you
  write yourself. It is read on a phone, so keep it tight. Math questions set
  `passage` to null unless the problem needs a scenario.
- The question stem stays under 200 characters. The passage carries the context.
- Exactly 4 options. Do NOT prefix them with A), B), C) or D) -- give the bare
  text of each option.
- Exactly one option is defensibly correct. For Reading and Writing, the three
  wrong options must be clearly wrong to a careful reader, not merely weaker.
  If two options could both be argued, rewrite the question.
- Every incorrect option must be a mistake a real student would plausibly make,
  not a random wrong number. For each one, name the specific misconception.
- The explanation stays under 200 characters and states the actual method.

Return a JSON array. Each element:
{
  "passage": "..." or null,
  "question": "...",
  "options": ["...", "...", "...", "..."],
  "correct_index": 0,
  "explanation": "...",
  "distractors": {
     "1": {"slug": "sign_error", "why": "flipped the sign when moving the term"},
     "2": {"slug": "...", "why": "..."},
     "3": {"slug": "...", "why": "..."}
  }
}
`distractors` has an entry for every index except correct_index. `slug` is
lower_snake_case and reused across questions for the same underlying error.
Return only the JSON array."""

GEN_EXAMPLE_MATH = """[{
 "passage": null,
 "question": "If 3(x - 4) = 2x + 5, what is the value of x?",
 "options": ["17", "7", "-7", "1"],
 "correct_index": 0,
 "explanation": "Distribute: 3x - 12 = 2x + 5. Subtract 2x, add 12: x = 17.",
 "distractors": {
   "1": {"slug": "dropped_distribution", "why": "only multiplied 3 by x, not by -4"},
   "2": {"slug": "sign_error", "why": "moved the constant across without flipping its sign"},
   "3": {"slug": "combined_unlike_terms", "why": "treated 3x and 2x as cancelling to x = 1"}
 }
}]"""

GEN_EXAMPLE_RW = """[{
 "passage": "Ravens are among the few animals that appear to plan. In one study, birds were shown a box that could be opened with a stone. When later offered a stone and several useless objects, the ravens chose the stone, even though the box was nowhere in sight and the reward would not arrive for a full day. The birds were not reacting to the box. They were preparing for it.",
 "question": "What does the passage mainly suggest about the ravens?",
 "options": [
   "They acted on a future goal rather than an immediate cue.",
   "They were trained to associate stones with food rewards.",
   "They preferred stones to other objects in all situations.",
   "They solved the box puzzle faster than other bird species."
 ],
 "correct_index": 0,
 "explanation": "The box was absent and the reward a day away, so the choice reflects planning, not reaction.",
 "distractors": {
   "1": {"slug": "confuses_planning_with_conditioning", "why": "reads deliberate planning as trained reflex"},
   "2": {"slug": "overgeneralizes_from_one_case", "why": "turns one experimental choice into an always-claim"},
   "3": {"slug": "invents_uncompared_detail", "why": "the passage never compares species"}
 }
}]"""


def generate_batch(skill, difficulty: str, n: int) -> list[dict]:
    from skills import SECTION_MATH
    is_math = skill.section == SECTION_MATH
    example = GEN_EXAMPLE_MATH if is_math else GEN_EXAMPLE_RW
    passage_note = (
        "Set passage to null unless the problem needs a short real-world scenario."
        if is_math else
        "You MUST write a 50-90 word passage for each question. The question is "
        "unusable without it."
    )
    topic_note = ""
    if not is_math:
        topic = random.choice(TOPIC_POOL)
        topic_note = (
            f"\nWrite the passage(s) in this register: {topic}. Real SAT passages "
            f"draw on literature, science, history and social science -- never "
            f"write bland civic announcements about community centres or recycling "
            f"programmes.\n"
        )

    user = (
        f"Write {n} {difficulty} digital-SAT questions.\n"
        f"Section: {skill.section}\n"
        f"Domain: {skill.domain}\n"
        f"Skill: {skill.name} -- {skill.student_label}\n\n"
        f"{passage_note}\n"
        f"{topic_note}\n"
        f"Difficulty calibration for '{difficulty}': {DIFFICULTY_NOTE[difficulty]}\n\n"
        f"Every question must test {skill.name} specifically. Vary the surface "
        f"context so the {n} questions do not look alike.\n\n"
        f"Here is one correctly formed example of the shape and quality expected:\n"
        f"{example}"
    )
    model = GENERATOR_MODEL_MATH if is_math else GENERATOR_MODEL_RW
    raw = _chat(model,
                [{"role": "system", "content": GEN_SYSTEM},
                 {"role": "user", "content": user}],
                max_tokens=6000 if is_math else 4000, temperature=0.9)
    data = _extract_json(raw)
    if not isinstance(data, list):
        return []
    out = []
    for item in data:
        if isinstance(item, dict):
            item["skill_id"] = skill.id
            item["difficulty"] = difficulty
            out.append(item)
    return out


_REFERS_TO_TEXT = re.compile(
    r"\b(passage|text|author|excerpt|paragraph|line|quote|underlined|sentence)\b", re.I)


def structurally_valid(q: dict) -> tuple[bool, str]:
    from skills import SECTION_MATH
    if not isinstance(q.get("question"), str) or not q["question"].strip():
        return False, "empty question"
    if len(q["question"]) > 320:
        return False, "question too long"
    if len(q["question"].strip()) < 25:
        return False, "stem too short to be a real question"

    # Self-containment. A question that points at a text without supplying it
    # cannot be answered, and the answer-verifier will happily "solve" it by
    # picking whichever option sounds nicest -- so this has to be caught here.
    skill = SKILL_BY_ID.get(q.get("skill_id", ""))
    passage = q.get("passage")
    has_passage = isinstance(passage, str) and len(passage.split()) >= 30
    if skill and skill.section != SECTION_MATH and not has_passage:
        return False, "reading/writing item with no usable passage"
    if _REFERS_TO_TEXT.search(q["question"]) and not has_passage:
        return False, "refers to a text that is not included"
    opts = q.get("options")
    if not isinstance(opts, list) or len(opts) != 4:
        return False, "not 4 options"
    if any(not isinstance(o, str) or not o.strip() for o in opts):
        return False, "blank option"
    if len({o.strip().lower() for o in opts}) != 4:
        return False, "duplicate options"
    ci = q.get("correct_index")
    if not isinstance(ci, int) or not 0 <= ci <= 3:
        return False, "bad correct_index"
    if any(re.match(r"^\s*[A-D]\s*[\)\.:]", o) for o in opts):
        return False, "options carry letter prefixes"
    d = q.get("distractors")
    if not isinstance(d, dict):
        return False, "no distractors map"
    wrong = {str(i) for i in range(4)} - {str(ci)}
    if set(d.keys()) != wrong:
        return False, "distractor keys do not match wrong options"
    for v in d.values():
        if not isinstance(v, dict) or not v.get("slug") or not v.get("why"):
            return False, "incomplete distractor tag"
    if not isinstance(q.get("explanation"), str) or not q["explanation"].strip():
        return False, "no explanation"
    return True, ""


SOLVE_SYSTEM = (
    "You are an expert SAT test-taker. Solve the question and reply with only "
    "the single digit index (0, 1, 2, or 3) of the correct option. Reply with "
    "the digit alone and nothing else."
)


def verify_answer(q: dict, votes: int = VERIFIER_VOTES) -> tuple[bool, list]:
    """Re-solve the question from scratch, without showing the proposed key."""
    listing = "\n".join(f"{i}: {o}" for i, o in enumerate(q["options"]))
    passage = q.get("passage")
    prefix = f"{passage.strip()}\n\n" if isinstance(passage, str) and passage.strip() else ""
    user = f"{prefix}{q['question']}\n\n{listing}"
    picks = []
    for _ in range(votes):
        raw = _chat(VERIFIER_MODEL,
                    [{"role": "system", "content": SOLVE_SYSTEM},
                     {"role": "user", "content": user}],
                    max_tokens=1200, temperature=0.0)
        m = re.findall(r"[0-3]", raw)
        picks.append(int(m[-1]) if m else None)
    agreed = all(p == q["correct_index"] for p in picks) and None not in picks
    return agreed, picks


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def _signature(q: dict) -> str | None:
    """A fingerprint that survives rewording, or None when one is not safe.

    Exact-text dedup misses the way generators actually repeat themselves. Two
    pilot items came back as "A jacket's price is increased by 20% and then
    discounted by 25%... final price 72" and "A store increased a jacket's
    price by 20%, then decreased the new price by 25%... final price $72" --
    the same problem twice, with no shared normalised text.

    What they do share is the maths: same skill, same numbers, same answer. So
    fingerprint on that instead. Only for items carrying at least two numbers,
    because a reading question's signature would be (skill, {}, answer), which
    would collide with every other reading item that happens to share an answer
    and throw away good questions.
    """
    numbers = re.findall(r"-?\d+(?:\.\d+)?", q.get("question", ""))
    if len(numbers) < 2:
        return None
    try:
        answer = q["options"][q["correct_index"]]
    except (KeyError, IndexError, TypeError):
        return None
    canonical = sorted(str(float(n)) for n in numbers)
    return f"{q.get('skill_id')}|{','.join(canonical)}|{_norm(str(answer))}"


def load_raw() -> list[dict]:
    if not os.path.exists(RAW_PATH):
        return []
    rows = []
    with open(RAW_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return rows


def append_raw(rows: list[dict]):
    with open(RAW_PATH, "a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def build(pilot: bool = False, section: str | None = None):
    existing = load_raw()
    have = Counter((r["skill_id"], r["difficulty"]) for r in existing if r.get("kept"))
    seen_text = {_norm(r["question"]) for r in existing if r.get("question")}
    # Seeded from kept items only: a rejected item never reached a student, so
    # its fingerprint should not block a good question from being written.
    seen_sig = {s for s in (_signature(r) for r in existing if r.get("kept")) if s}

    # Pilot deliberately mixes a reading skill with maths skills: they fail in
    # completely different ways, so sampling only one section hides problems.
    pilot_ids = ["rw_central_ideas", "m_systems", "m_percentages"]
    skills = [SKILL_BY_ID[i] for i in pilot_ids] if pilot else SKILLS
    difficulties = ["medium"] if pilot else DIFFICULTIES
    want = 3 if pilot else QUESTIONS_PER_CELL

    # The two sections are generated by different models, and Groq meters
    # tokens-per-day per model. When one model's daily bucket is dry the other
    # is usually still open, so allow a run to be confined to the section that
    # can actually make progress instead of burning wall-clock on retries.
    if section:
        skills = [s for s in skills if s.section == section]
        if not skills:
            print(f"no skills in section {section!r}")
            return

    stats = Counter()
    t_start = time.time()

    # Fill breadth-first: every skill reaches FLOOR before any skill goes
    # deeper. A depth-first pass spends the whole daily quota on the first
    # third of SKILLS and leaves the rest at zero, which is worse than thin
    # coverage everywhere -- the planner allocates minutes by skill, so a
    # skill with no questions is a hole the student walks into.
    targets = sorted({t for t in (FLOOR, want) if t <= want})

    for target in targets:
        print(f"\n{'#'*60}\n# pass: fill every skill to {target} per difficulty\n{'#'*60}",
              flush=True)
        for skill in skills:
            for difficulty in difficulties:
                need = target - have[(skill.id, difficulty)]
                if need <= 0:
                    continue
                print(f"\n[{skill.id}/{difficulty}] need {need}", flush=True)

                attempts = 0
                while need > 0 and attempts < 4:
                    attempts += 1
                    batch = generate_batch(skill, difficulty, min(BATCH_SIZE, need))
                    if not batch:
                        stats["gen_failed"] += 1
                        continue

                    results = []
                    for q in batch:
                        ok, why = structurally_valid(q)
                        if not ok:
                            stats[f"reject_{why}"] += 1
                            print(f"    x structural: {why}", flush=True)
                            results.append({**q, "kept": False, "reject": why})
                            continue

                        key = _norm(q["question"])
                        if key in seen_text:
                            stats["reject_duplicate"] += 1
                            print("    x duplicate", flush=True)
                            results.append({**q, "kept": False, "reject": "duplicate"})
                            continue

                        sig = _signature(q)
                        if sig and sig in seen_sig:
                            stats["reject_reworded_duplicate"] += 1
                            print("    x same problem, reworded", flush=True)
                            results.append({**q, "kept": False,
                                            "reject": "reworded_duplicate"})
                            continue

                        agreed, picks = verify_answer(q)
                        if not agreed:
                            stats["reject_answer_disputed"] += 1
                            print(f"    x key disputed: claimed {q['correct_index']}, "
                                  f"solver said {picks}", flush=True)
                            results.append({**q, "kept": False, "reject": "answer_disputed",
                                            "solver_picks": picks})
                            continue

                        seen_text.add(key)
                        if sig:
                            seen_sig.add(sig)
                        stats["kept"] += 1
                        have[(skill.id, difficulty)] += 1
                        need -= 1
                        print(f"    + kept ({need} to go)", flush=True)
                        results.append({**q, "kept": True, "solver_picks": picks})

                    append_raw(results)

    elapsed = time.time() - t_start
    print(f"\n{'='*60}\nGeneration finished in {elapsed/60:.1f} min", flush=True)
    total_seen = sum(v for k, v in stats.items() if k != "gen_failed")
    for k, v in sorted(stats.items(), key=lambda kv: -kv[1]):
        pct = f"{100*v/total_seen:5.1f}%" if total_seen and k != "gen_failed" else "     "
        print(f"  {k:32s} {v:5d}  {pct}")
    if stats["kept"] and total_seen:
        print(f"\n  acceptance rate: {100*stats['kept']/total_seen:.1f}%")
    compile_bank()


def compile_bank():
    """Write the vetted subset of the raw log out as the shipped bank."""
    rows = [r for r in load_raw() if r.get("kept")]

    # Drop reworded duplicates that predate the signature check. The raw log is
    # append-only history and stays as it is; the shipped bank is the artifact
    # that has to be clean, and a student meeting the same problem twice in
    # different words notices immediately.
    seen_sig, deduped = set(), []
    for row in rows:
        sig = _signature(row)
        if sig and sig in seen_sig:
            continue
        if sig:
            seen_sig.add(sig)
        deduped.append(row)
    if len(deduped) != len(rows):
        print(f"  dropped {len(rows) - len(deduped)} reworded duplicate(s)")
    rows = deduped
    bank = []
    # Deterministic shuffle so rebuilding the bank does not reshuffle answers
    # out from under students who have already been served these ids.
    rng = random.Random(20260815)

    for i, r in enumerate(rows):
        passage = r.get("passage")
        options = [o.strip() for o in r["options"]]
        distractors = r["distractors"]

        # Generators inherit the answer position of whatever example they were
        # shown -- the first pilot put the key at index 0 in 100% of accepted
        # items. Shuffling here makes position uniform by construction instead
        # of hoping the prompt behaves.
        order = list(range(4))
        rng.shuffle(order)
        new_options = [options[old] for old in order]
        new_correct = order.index(r["correct_index"])
        new_distractors = {}
        for new_idx, old_idx in enumerate(order):
            if new_idx == new_correct:
                continue
            tag = distractors.get(str(old_idx))
            if tag:
                new_distractors[str(new_idx)] = tag

        bank.append({
            "id": f"q_{r['skill_id']}_{r['difficulty']}_{i}",
            "skill_id": r["skill_id"],
            "difficulty": r["difficulty"],
            "passage": passage.strip() if isinstance(passage, str) and passage.strip() else None,
            "question": r["question"].strip(),
            "options": new_options,
            "correct_index": new_correct,
            "explanation": r["explanation"].strip(),
            "distractors": new_distractors,
        })
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(bank, f, indent=1, ensure_ascii=False)
    print(f"\nwrote {OUT_PATH}: {len(bank)} questions")
    report(bank)


def report(bank=None):
    if bank is None:
        if not os.path.exists(OUT_PATH):
            print(f"{OUT_PATH} does not exist yet")
            return
        with open(OUT_PATH, encoding="utf-8") as f:
            bank = json.load(f)

    by_skill = Counter(q["skill_id"] for q in bank)
    print(f"\n{len(bank)} questions across {len(by_skill)}/{len(SKILLS)} skills")

    missing = [s.id for s in SKILLS if by_skill[s.id] == 0]
    thin = [(s.id, by_skill[s.id]) for s in SKILLS if 0 < by_skill[s.id] < 6]
    if missing:
        print(f"  NO questions ({len(missing)}): {', '.join(missing)}")
    if thin:
        print(f"  thin (<6): {', '.join(f'{k}={v}' for k, v in thin)}")

    slugs = Counter()
    for q in bank:
        for d in q.get("distractors", {}).values():
            slugs[d["slug"]] += 1
    print(f"\n  {len(slugs)} distinct misconceptions, top 12:")
    for slug, n in slugs.most_common(12):
        print(f"    {n:4d}  {slug}")

    # Answer position should be roughly uniform; a heavy skew means the
    # generator has a positional bias students could exploit.
    pos = Counter(q["correct_index"] for q in bank)
    print(f"\n  correct-answer position: "
          + "  ".join(f"{i}={pos[i]}" for i in range(4)))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--pilot", action="store_true", help="small run with a quality report")
    ap.add_argument("--report", action="store_true", help="summarise the existing bank")
    ap.add_argument("--compile", action="store_true", help="rebuild JSON from the raw log")
    ap.add_argument("--section", choices=["math", "rw"],
                    help="build only one section (their models have separate daily quotas)")
    args = ap.parse_args()

    if args.report:
        report()
    elif args.compile:
        compile_bank()
    else:
        from skills import SECTION_MATH, SECTION_RW
        section = {"math": SECTION_MATH, "rw": SECTION_RW}.get(args.section)
        build(pilot=args.pilot, section=section)
