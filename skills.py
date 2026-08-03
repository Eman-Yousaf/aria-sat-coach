"""SAT skill taxonomy.

Mirrors the College Board Digital SAT domain structure. Everything downstream
(mastery tracking, score simulation, study planning) is indexed by the skill
ids defined here, so this module is the single source of truth for "what can a
student be good or bad at".

Domain weights are the published operational-question percentages for the
digital SAT. They matter because the simulator needs to know that being weak
at Algebra costs far more points than being weak at Circles.
"""

from dataclasses import dataclass

SECTION_RW = "Reading and Writing"
SECTION_MATH = "Math"

# Operational (scored) question counts per section on the digital SAT.
SECTION_QUESTION_COUNT = {
    SECTION_RW: 54,
    SECTION_MATH: 44,
}

# Each section is scaled 200-800.
SECTION_SCORE_MIN = 200
SECTION_SCORE_MAX = 800


@dataclass(frozen=True)
class Skill:
    id: str
    name: str
    section: str
    domain: str
    # Share of that domain's questions this skill accounts for.
    domain_share: float
    # Plain-language description used in student-facing messages.
    student_label: str


# Share of each section's questions belonging to a domain (College Board spec).
DOMAIN_WEIGHT = {
    "Information and Ideas": 0.26,
    "Craft and Structure": 0.28,
    "Expression of Ideas": 0.20,
    "Standard English Conventions": 0.26,
    "Algebra": 0.35,
    "Advanced Math": 0.35,
    "Problem-Solving and Data Analysis": 0.15,
    "Geometry and Trigonometry": 0.15,
}

DOMAIN_SECTION = {
    "Information and Ideas": SECTION_RW,
    "Craft and Structure": SECTION_RW,
    "Expression of Ideas": SECTION_RW,
    "Standard English Conventions": SECTION_RW,
    "Algebra": SECTION_MATH,
    "Advanced Math": SECTION_MATH,
    "Problem-Solving and Data Analysis": SECTION_MATH,
    "Geometry and Trigonometry": SECTION_MATH,
}


def _s(id_, name, domain, share, label):
    return Skill(id_, name, DOMAIN_SECTION[domain], domain, share, label)


SKILLS: list[Skill] = [
    # ---- Reading and Writing: Information and Ideas ----
    _s("rw_central_ideas", "Central Ideas and Details", "Information and Ideas", 0.30,
       "finding the main point of a passage"),
    _s("rw_evidence_text", "Command of Evidence (Textual)", "Information and Ideas", 0.30,
       "picking the quote that proves a claim"),
    _s("rw_evidence_quant", "Command of Evidence (Quantitative)", "Information and Ideas", 0.15,
       "reading graphs and tables inside a passage"),
    _s("rw_inferences", "Inferences", "Information and Ideas", 0.25,
       "concluding what the author implies but doesn't say"),

    # ---- Reading and Writing: Craft and Structure ----
    _s("rw_words_in_context", "Words in Context", "Craft and Structure", 0.45,
       "choosing the word that fits the sentence"),
    _s("rw_text_structure", "Text Structure and Purpose", "Craft and Structure", 0.35,
       "seeing why the author organized a text a certain way"),
    _s("rw_cross_text", "Cross-Text Connections", "Craft and Structure", 0.20,
       "comparing two passages that disagree"),

    # ---- Reading and Writing: Expression of Ideas ----
    _s("rw_synthesis", "Rhetorical Synthesis", "Expression of Ideas", 0.55,
       "combining notes to hit a writing goal"),
    _s("rw_transitions", "Transitions", "Expression of Ideas", 0.45,
       "choosing however vs. therefore vs. moreover"),

    # ---- Reading and Writing: Standard English Conventions ----
    _s("rw_boundaries", "Boundaries", "Standard English Conventions", 0.50,
       "commas, semicolons, and where sentences end"),
    _s("rw_form_structure", "Form, Structure, and Sense", "Standard English Conventions", 0.50,
       "subject-verb agreement, verb tense, and pronouns"),

    # ---- Math: Algebra ----
    _s("m_linear_one_var", "Linear Equations in One Variable", "Algebra", 0.20,
       "solving for x in a straight-line equation"),
    _s("m_linear_two_var", "Linear Equations in Two Variables", "Algebra", 0.20,
       "equations with both x and y"),
    _s("m_linear_functions", "Linear Functions", "Algebra", 0.20,
       "slope, intercepts, and reading a line"),
    _s("m_systems", "Systems of Two Linear Equations", "Algebra", 0.25,
       "solving two equations at the same time"),
    _s("m_linear_inequalities", "Linear Inequalities", "Algebra", 0.15,
       "greater-than and less-than problems"),

    # ---- Math: Advanced Math ----
    _s("m_equivalent_expr", "Equivalent Expressions", "Advanced Math", 0.35,
       "factoring and rewriting expressions"),
    _s("m_nonlinear_eq", "Nonlinear Equations in One Variable", "Advanced Math", 0.35,
       "quadratics and equations with squares"),
    _s("m_nonlinear_functions", "Nonlinear Functions", "Advanced Math", 0.30,
       "parabolas, exponentials, and their graphs"),

    # ---- Math: Problem-Solving and Data Analysis ----
    _s("m_ratios_rates", "Ratios, Rates, and Proportions", "Problem-Solving and Data Analysis", 0.25,
       "unit rates and scaling quantities"),
    _s("m_percentages", "Percentages", "Problem-Solving and Data Analysis", 0.20,
       "percent increase, decrease, and of-problems"),
    _s("m_one_var_data", "One-Variable Data", "Problem-Solving and Data Analysis", 0.20,
       "mean, median, spread, and outliers"),
    _s("m_two_var_data", "Two-Variable Data", "Problem-Solving and Data Analysis", 0.15,
       "scatterplots and lines of best fit"),
    _s("m_probability", "Probability", "Problem-Solving and Data Analysis", 0.10,
       "chance, including two-way tables"),
    _s("m_inference_stats", "Inference from Sample Statistics", "Problem-Solving and Data Analysis", 0.10,
       "margin of error and what a sample proves"),

    # ---- Math: Geometry and Trigonometry ----
    _s("m_area_volume", "Area and Volume", "Geometry and Trigonometry", 0.30,
       "area, surface area, and volume formulas"),
    _s("m_lines_angles_triangles", "Lines, Angles, and Triangles", "Geometry and Trigonometry", 0.30,
       "angle rules and similar triangles"),
    _s("m_right_triangles_trig", "Right Triangles and Trigonometry", "Geometry and Trigonometry", 0.25,
       "Pythagoras, sine, cosine, and tangent"),
    _s("m_circles", "Circles", "Geometry and Trigonometry", 0.15,
       "arcs, sectors, and circle equations"),
]

SKILL_BY_ID: dict[str, Skill] = {s.id: s for s in SKILLS}


def skills_in_section(section: str) -> list[Skill]:
    return [s for s in SKILLS if s.section == section]


def question_weight(skill_id: str) -> float:
    """Expected number of questions on a real SAT that test this skill."""
    skill = SKILL_BY_ID[skill_id]
    n = SECTION_QUESTION_COUNT[skill.section]
    return n * DOMAIN_WEIGHT[skill.domain] * skill.domain_share


# Legacy subject names ("Math" / "Reading" / "Writing") map onto sections so the
# existing WhatsApp flow keeps working while the model thinks in skills.
LEGACY_SUBJECT_SECTION = {
    "Math": SECTION_MATH,
    "Reading": SECTION_RW,
    "Writing": SECTION_RW,
}

# Reading and Writing share a section but students pick between them, so keep a
# narrower skill pool for each so "Writing" doesn't serve reading-comprehension.
LEGACY_SUBJECT_DOMAINS = {
    "Math": ["Algebra", "Advanced Math",
             "Problem-Solving and Data Analysis", "Geometry and Trigonometry"],
    "Reading": ["Information and Ideas", "Craft and Structure"],
    "Writing": ["Expression of Ideas", "Standard English Conventions"],
}


def skills_for_subject(subject: str) -> list[Skill]:
    domains = LEGACY_SUBJECT_DOMAINS.get(subject)
    if not domains:
        return list(SKILLS)
    return [s for s in SKILLS if s.domain in domains]


def validate() -> list[str]:
    """Sanity-check the taxonomy. Returns a list of problems (empty == healthy)."""
    problems = []
    by_domain: dict[str, float] = {}
    for s in SKILLS:
        by_domain[s.domain] = by_domain.get(s.domain, 0.0) + s.domain_share
    for domain, total in by_domain.items():
        if abs(total - 1.0) > 1e-6:
            problems.append(f"domain {domain!r} shares sum to {total:.3f}, expected 1.0")

    for section in (SECTION_RW, SECTION_MATH):
        total = sum(DOMAIN_WEIGHT[d] for d, sec in DOMAIN_SECTION.items() if sec == section)
        if abs(total - 1.0) > 1e-6:
            problems.append(f"section {section!r} domain weights sum to {total:.3f}, expected 1.0")

    for section in (SECTION_RW, SECTION_MATH):
        got = sum(question_weight(s.id) for s in skills_in_section(section))
        want = SECTION_QUESTION_COUNT[section]
        if abs(got - want) > 1e-6:
            problems.append(f"section {section!r} question weights sum to {got:.2f}, expected {want}")

    return problems


if __name__ == "__main__":
    issues = validate()
    if issues:
        for i in issues:
            print(f"FAIL: {i}")
        raise SystemExit(1)
    print(f"OK: {len(SKILLS)} skills across {len(DOMAIN_WEIGHT)} domains")
    for section in (SECTION_RW, SECTION_MATH):
        print(f"\n{section} ({SECTION_QUESTION_COUNT[section]} questions)")
        for s in sorted(skills_in_section(section), key=lambda x: -question_weight(x.id)):
            print(f"  {question_weight(s.id):5.1f}q  {s.name}")
