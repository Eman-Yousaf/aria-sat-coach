import agent
import sat_rag


def run_diagnosis(message: str) -> dict:
    return agent.diagnose_student(message)


def run_question(subject: str, weakness: str | None = None, difficulty: str = "medium") -> dict:
    rag_result = sat_rag.get_question_by_subject(
        subject=subject,
        difficulty=difficulty,
        weakness=weakness,
    )
    if rag_result and rag_result.get("question"):
        return rag_result
    return agent.generate_question(
        subject=subject,
        weakness=weakness,
        difficulty=difficulty,
    )


def run_feedback(question: str, student_answer: str, correct_answer: str) -> str:
    return agent.generate_feedback(question, student_answer, correct_answer)
