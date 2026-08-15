"""The catalogue of things Aria can actually *do* to a student.

`skills.py` says what a student can be good or bad at. This says what Aria can
try in order to move that, and it exists because "practise your weak skill" is
not one action -- it is a dozen different ones with different time costs and
wildly different effectiveness *per student*.

Every intervention here is a genuinely different sequence of messages, not a
label on the same quiz loop. That constraint is deliberate: an intervention the
tutor cannot execute differently is one whose effectiveness cannot be measured,
and an effectiveness estimate for something that never happened is decoration.

The time model is the part that makes the ranking honest. A worked example
spends three minutes before the student answers anything; a timed drill spends
none and moves faster per question. So an intervention that teaches slightly
better can still lose on points-per-minute, and one that teaches worse can win.
That trade-off is the whole reason the engine has to *measure* rather than
follow a pedagogy rule.
"""

from dataclasses import dataclass

__all__ = [
    "Intervention", "INTERVENTIONS", "INTERVENTION_BY_ID",
    "available_for", "skill_principle", "skill_hint", "socratic_lead_in",
]


@dataclass(frozen=True)
class Intervention:
    id: str
    name: str                    # judge-facing
    student_label: str           # how Aria describes it to a student
    setup_minutes: float         # spent before the student answers anything
    minutes_per_question: float
    default_questions: int
    # What actually changes in the conversation. Kept here so the dashboard and
    # the docs cannot drift from tutor.py's behaviour without someone noticing.
    mechanics: str
    requires_misconception: bool = False
    requires_prior_success: bool = False
    # Bank items the preamble burns before the student answers anything. A
    # worked example is a real question solved in full, and it can never be
    # served back as a question afterwards, so it has to be budgeted. Left
    # unbudgeted it silently produced episodes with a demonstration and no
    # practice on any skill the bank was thin on -- which then recorded as
    # "this intervention taught nothing".
    demo_items: int = 0

    def minutes_for(self, questions: int) -> float:
        return self.setup_minutes + questions * self.minutes_per_question

    def questions_within(self, minutes: float) -> int:
        """Most questions that fit in a budget, 0 if even one does not."""
        usable = minutes - self.setup_minutes
        if usable < self.minutes_per_question:
            return 0
        return int(usable / self.minutes_per_question)


INTERVENTIONS: list[Intervention] = [
    Intervention(
        id="worked_example",
        name="Worked example then practice",
        student_label="I'll walk one through first, then you try",
        setup_minutes=3.0,
        minutes_per_question=2.0,
        default_questions=3,
        mechanics="Aria solves a real item from the bank in full, step by step, "
                  "then serves fresh unseen items on the same skill.",
        demo_items=1,
    ),
    Intervention(
        id="retrieval_practice",
        name="Cold retrieval",
        student_label="Straight into questions, no warm-up",
        setup_minutes=0.0,
        minutes_per_question=2.0,
        default_questions=4,
        mechanics="No scaffolding at all. The student attempts from memory and "
                  "gets feedback only afterwards. Cheap per minute, and the "
                  "effortful-recall literature says it retains well -- but "
                  "whether it does so for *this* student is what gets measured.",
    ),
    Intervention(
        id="direct_explanation",
        name="Explanation then practice",
        student_label="Let me explain the rule first",
        setup_minutes=4.0,
        minutes_per_question=2.0,
        default_questions=3,
        mechanics="Aria states the governing principle for the skill, then "
                  "serves practice. The most expensive setup in the catalogue, "
                  "so it has to teach materially better to be worth choosing.",
    ),
    Intervention(
        id="socratic",
        name="Socratic questioning",
        student_label="I'll ask you a question before the question",
        setup_minutes=1.0,
        minutes_per_question=3.0,
        default_questions=3,
        mechanics="Before each item Aria asks the student to say what the "
                  "question is actually asking for, and only then shows the "
                  "options. Costs an extra turn per question.",
    ),
    Intervention(
        id="misconception_repair",
        name="Misconception correction",
        student_label="There's one specific trap you keep falling into",
        setup_minutes=2.5,
        minutes_per_question=2.2,
        default_questions=3,
        mechanics="Aria names the exact error this student has repeated, quotes "
                  "why it is wrong, then preferentially serves items that "
                  "encode that same misconception as a distractor -- so the "
                  "trap is walked into deliberately rather than by accident.",
        requires_misconception=True,
    ),
    Intervention(
        id="hint_first",
        name="Hint-first practice",
        student_label="I'll give you the nudge up front",
        setup_minutes=0.0,
        minutes_per_question=2.5,
        default_questions=3,
        mechanics="Each item ships with a one-line hint attached before the "
                  "student attempts it. Lower effort than cold retrieval, which "
                  "is exactly why it may retain worse.",
    ),
    Intervention(
        id="timed_drill",
        name="Timed drill",
        student_label="Clock on - speed is the skill here",
        setup_minutes=0.5,
        minutes_per_question=1.3,
        default_questions=5,
        mechanics="A stated per-question time budget and more items in the same "
                  "minutes. Fastest per question in the catalogue; whether the "
                  "learning survives the pace is student-specific.",
    ),
    Intervention(
        id="spaced_review",
        name="Spaced review",
        student_label="Quick check on something you already learned",
        setup_minutes=0.0,
        minutes_per_question=2.0,
        default_questions=2,
        mechanics="Retrieval on a skill the student has already got right "
                  "before, timed to land as the retention curve dips. Only "
                  "available once there is something to review.",
        requires_prior_success=True,
    ),
]

INTERVENTION_BY_ID: dict[str, Intervention] = {i.id: i for i in INTERVENTIONS}


def available_for(*, has_misconception: bool, has_prior_success: bool) -> list[Intervention]:
    """Interventions Aria is actually able to run right now.

    Two of them need material that may not exist: you cannot correct a
    misconception the student has never made, and you cannot review a skill
    they have never got right. Filtering here rather than at ranking time means
    the estimator never has to score an option that could not be executed.
    """
    out = []
    for iv in INTERVENTIONS:
        if iv.requires_misconception and not has_misconception:
            continue
        if iv.requires_prior_success and not has_prior_success:
            continue
        out.append(iv)
    return out


# --- content the interventions need --------------------------------------
#
# `direct_explanation` and `hint_first` have to say something true about the
# skill before the student answers. Generating that with a language model would
# make the two most content-dependent interventions the two that break when the
# key is missing -- and would make them non-deterministic, which would poison
# the very measurements this module exists to take. So the principles are
# written down, one per skill, and the LLM is never in this path.

SKILL_PRINCIPLE: dict[str, str] = {
    "rw_central_ideas":
        "The main idea is the claim every other sentence is serving. Find the "
        "sentence the rest of the passage would collapse without.",
    "rw_evidence_text":
        "The right quote has to prove the specific claim, not merely mention "
        "the same topic. Test each option against the claim word by word.",
    "rw_evidence_quant":
        "Read the axis labels and units before the answer choices. Most wrong "
        "options are true about the graph but do not support the claim made.",
    "rw_inferences":
        "An inference must follow necessarily from the text. If you need one "
        "extra outside fact to make it work, it is not the answer.",
    "rw_words_in_context":
        "Ignore the word's most common meaning. Substitute each option back "
        "into the sentence and keep the one that preserves the logic.",
    "rw_text_structure":
        "Ask what the sentence or paragraph *does* for the passage, not what "
        "it says. Options describing content rather than function are traps.",
    "rw_cross_text":
        "Pin down each author's claim separately first, then ask how the "
        "second author would respond to the first. Do not blend them.",
    "rw_synthesis":
        "The goal stated in the prompt is the whole task. Only notes that "
        "serve that exact goal belong in the answer, however true the rest are.",
    "rw_transitions":
        "Decide the relationship between the two sentences before you look: "
        "same direction, opposite, cause, or example. Then match it.",
    "rw_boundaries":
        "Two independent clauses need a period, a semicolon, or a comma plus a "
        "conjunction. A comma alone between them is always wrong.",
    "rw_form_structure":
        "Find the true subject, ignore everything between it and the verb, and "
        "then check agreement and tense against it.",
    "m_linear_one_var":
        "Clear fractions and parentheses first, gather the variable on one "
        "side, then divide once. Do the same thing to both sides every time.",
    "m_linear_two_var":
        "Two unknowns need two facts. Write both as equations before "
        "substituting, and keep track of which variable you solved for.",
    "m_linear_functions":
        "Slope is change in y over change in x, and the intercept is the value "
        "at x = 0. Read those two numbers off before touching the options.",
    "m_systems":
        "Eliminate a variable by adding or subtracting the equations when the "
        "coefficients line up; substitute when one variable is already alone.",
    "m_linear_inequalities":
        "Solve exactly as you would an equation, with one exception: "
        "multiplying or dividing by a negative flips the inequality sign.",
    "m_equivalent_expr":
        "Factoring and expanding are the same move in two directions. Look for "
        "a common factor first, then a difference of squares, then a trinomial.",
    "m_nonlinear_eq":
        "Get one side to zero, then factor. If it will not factor, the "
        "quadratic formula always works. Check for extraneous roots.",
    "m_nonlinear_functions":
        "Vertex form gives you the turning point, factored form gives you the "
        "zeros. Convert to whichever form the question is asking about.",
    "m_ratios_rates":
        "Set up the proportion with the same units in the same position on "
        "both sides, then cross-multiply. Units are the error-check.",
    "m_percentages":
        "Percent change is (new - old) / old, always over the *original*. "
        "A 20% rise then a 20% fall does not return you to where you started.",
    "m_one_var_data":
        "The mean moves with outliers and the median does not. Which one the "
        "question asks about usually decides the answer on its own.",
    "m_two_var_data":
        "The line of best fit predicts, it does not prove. Read values off the "
        "line rather than off individual points.",
    "m_probability":
        "Probability is the favourable count over the total count. In a "
        "two-way table, the phrase after 'given that' sets the denominator.",
    "m_inference_stats":
        "A margin of error describes the interval, not certainty. Conclusions "
        "only extend to the population the sample was actually drawn from.",
    "m_area_volume":
        "Write the formula before substituting numbers, and check the units: "
        "area is squared, volume is cubed.",
    "m_lines_angles_triangles":
        "Angles on a line sum to 180 and angles in a triangle sum to 180. "
        "Similar triangles give you proportional sides, not equal ones.",
    "m_right_triangles_trig":
        "SOH-CAH-TOA picks the ratio; the side you are solving for decides "
        "whether it goes on the top or the bottom of the fraction.",
    "m_circles":
        "Arc length and sector area are both just the fraction of the full "
        "circle that the central angle covers.",
}

# One-liners for `hint_first`: shorter than the principle, and deliberately not
# a restatement of the answer. A hint that gives the answer measures nothing.
SKILL_HINT: dict[str, str] = {
    "rw_words_in_context": "Substitute each option back into the sentence.",
    "rw_boundaries": "Check whether both halves could stand alone as sentences.",
    "rw_form_structure": "Find the real subject before you check the verb.",
    "rw_transitions": "Decide the relationship first, then match it.",
    "rw_evidence_text": "Match the quote to the exact claim, not to the topic.",
    "rw_synthesis": "Reread the stated goal before comparing options.",
    "m_linear_one_var": "Clear the parentheses and fractions first.",
    "m_systems": "Line up the coefficients and see what cancels.",
    "m_equivalent_expr": "Look for the common factor before anything else.",
    "m_nonlinear_eq": "Move everything to one side so it equals zero.",
    "m_percentages": "The denominator is the original amount.",
    "m_probability": "The words after 'given that' set the denominator.",
    "m_right_triangles_trig": "Label the sides relative to the angle you were given.",
    "m_area_volume": "Write the formula down before substituting.",
}

_DEFAULT_HINT = "Work out what the question is asking for before you compare options."


def skill_principle(skill_id: str) -> str:
    """The rule `direct_explanation` teaches. Always returns something usable."""
    return SKILL_PRINCIPLE.get(
        skill_id,
        "Work out exactly what is being asked, then test each option against it.",
    )


def skill_hint(skill_id: str) -> str:
    return SKILL_HINT.get(skill_id, _DEFAULT_HINT)


def socratic_lead_in(skill_id: str) -> str:
    """The question Aria asks before the question.

    Generic on purpose. A Socratic prompt works by making the student
    articulate the goal in their own words; the prompt itself carries almost no
    information, which is what distinguishes it from an explanation.
    """
    return ("Before you answer - in one line, what is this question actually "
            "asking you to find? Say it however you like, then I'll show you "
            "the options.")
