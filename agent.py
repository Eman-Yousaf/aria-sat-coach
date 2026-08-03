import json

import config
from config import GROQ_API_KEY

_llm = None
_chat_client = None
_chat_quirks: set = set()


def chat_text(system: str, user: str, max_tokens: int = 400) -> str | None:
    """One plain chat completion, against whichever provider is configured.

    Returns None when no provider is available or the call fails, so every
    caller can fall back to deterministic text. This exists separately from the
    LangChain path below because the deployed app has Azure credentials and no
    Groq key -- without it, every conversational reply in production silently
    degrades to canned copy, which is exactly what makes a bot feel scripted.
    """
    global _chat_client
    try:
        if _chat_client is None:
            if config.USE_AZURE:
                from openai import AzureOpenAI
                _chat_client = ("azure", AzureOpenAI(
                    azure_endpoint=config.AZURE_OPENAI_ENDPOINT,
                    api_key=config.AZURE_OPENAI_API_KEY,
                    api_version=config.AZURE_OPENAI_API_VERSION,
                ))
            elif GROQ_API_KEY:
                from groq import Groq
                _chat_client = ("groq", Groq(api_key=GROQ_API_KEY))
            else:
                return None

        kind, client = _chat_client
        model = (config.AZURE_DEPLOYMENT_CHAT if kind == "azure"
                 else "llama-3.3-70b-versatile")
        if not model:
            return None

        messages = [{"role": "system", "content": system},
                    {"role": "user", "content": user}]
        for _ in range(2):
            kwargs = {"model": model, "messages": messages}
            # The GPT-5 family wants max_completion_tokens and rejects a custom
            # temperature; learn that from the error rather than hardcoding it.
            kwargs["max_completion_tokens" if "max_tokens" in _chat_quirks
                   else "max_tokens"] = max_tokens
            if "temperature" not in _chat_quirks:
                kwargs["temperature"] = 0.7
            try:
                resp = client.chat.completions.create(**kwargs)
            except Exception as exc:
                detail = str(exc).lower()
                learned = False
                for param in ("max_tokens", "temperature"):
                    if param not in _chat_quirks and f"'{param}'" in detail \
                            and "unsupported" in detail:
                        _chat_quirks.add(param)
                        learned = True
                if learned:
                    continue
                raise
            msg = resp.choices[0].message
            out = (msg.content or "").strip()
            return out or None
    except Exception:
        return None
    return None


def _get_llm():
    global _llm
    if _llm is None:
        from langchain_core.prompts import ChatPromptTemplate
        from langchain_core.output_parsers import JsonOutputParser
        from langchain_groq import ChatGroq
        _llm = ChatGroq(
            model="llama-3.3-70b-versatile",
            temperature=0.7,
            api_key=GROQ_API_KEY,
        )
    return _llm


def _diag_prompt():
    from langchain_core.prompts import ChatPromptTemplate
    return ChatPromptTemplate.from_messages([
        ("system", "You are Aria, an adaptive SAT tutor. Analyze the student's message and extract:\n"
         "- subject: one of 'Math', 'Reading', or 'Writing'\n"
         "- weakness: the specific area (e.g. 'Algebra', 'Linear Equations', "
         "'Inference', 'Main Idea', 'Vocabulary in Context', 'Grammar', 'Punctuation', "
         "'Sentence Structure', 'Problem Solving', 'Data Analysis', 'Advanced Math', "
         "'Command of Evidence', 'Style and Tone')\n"
         "- time_available: extracted time like '30 minutes' or '1 hour' or null\n\n"
         "Respond in JSON only with keys: subject, weakness, time_available."),
        ("human", "Student message: {message}"),
    ])


def _q_prompt():
    from langchain_core.prompts import ChatPromptTemplate
    return ChatPromptTemplate.from_messages([
        ("system", "You are Aria, an expert SAT tutor. Generate a single SAT multiple-choice question.\n"
         "Subject: {subject}\nWeakness: {weakness}\n"
         "Difficulty: {difficulty} (easy/medium/hard)\nStudent name: {name}\n\n"
         "Rules:\n- Create a realistic SAT-style question\n"
         "- Provide exactly 4 options labeled A, B, C, D\n"
         "- Mark the correct answer as A/B/C/D\n"
         "- Include a concise explanation (<150 chars)\n"
         "- Keep the question text under 200 characters\n"
         "- Output JSON with keys: question, options (list of 4 strings), correct_answer, explanation"),
        ("human", "Generate a {difficulty} {subject} question focusing on {weakness}."),
    ])


def _feedback_prompt():
    from langchain_core.prompts import ChatPromptTemplate
    return ChatPromptTemplate.from_messages([
        ("system", "You are Aria, an encouraging SAT tutor. Give brief feedback.\n"
         "Keep it under 250 characters.\nIf correct: praise and briefly explain why.\n"
         "If wrong: encourage and give a short hint without revealing the answer.\n"
         "Use a warm, supportive tone."),
        ("human", "Question: {question}\n"
         "Correct answer: {correct_answer}\nStudent's answer: {student_answer}"),
    ])


def _reminder_prompt():
    from langchain_core.prompts import ChatPromptTemplate
    return ChatPromptTemplate.from_messages([
        ("system", "You are Aria, an adaptive SAT tutor. Send a short, motivating WhatsApp reminder.\n"
         "Keep it under 200 characters.\nInclude the student's name and subject.\n"
         "Encourage daily practice.\nEnd with: Reply HELP for commands or just send your answer."),
        ("human", "Remind {name} to practice {subject} for their SAT prep today."),
    ])


def diagnose_student(message: str) -> dict:
    from langchain_core.output_parsers import JsonOutputParser
    chain = _diag_prompt() | _get_llm() | JsonOutputParser()
    try:
        result = chain.invoke({"message": message})
        return {
            "subject": result.get("subject", "Math"),
            "weakness": result.get("weakness", ""),
            "time_available": result.get("time_available"),
        }
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return {"subject": "Math", "weakness": "", "time_available": None}


def generate_question(
    subject: str,
    weakness: str | None = None,
    difficulty: str = "medium",
    name: str | None = None,
) -> dict:
    from langchain_core.output_parsers import JsonOutputParser
    chain = _q_prompt() | _get_llm() | JsonOutputParser()
    try:
        result = chain.invoke({
            "subject": subject,
            "weakness": weakness or "General",
            "difficulty": difficulty,
            "name": name or "Student",
        })
        return {
            "question": result.get("question", ""),
            "options": result.get("options", ["A", "B", "C", "D"]),
            "correct_answer": result.get("correct_answer", "A"),
            "explanation": result.get("explanation", ""),
        }
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return {
            "question": "",
            "options": ["A", "B", "C", "D"],
            "correct_answer": "A",
            "explanation": "",
        }


def generate_feedback(
    question: str,
    student_answer: str,
    correct_answer: str,
) -> str:
    chain = _feedback_prompt() | _get_llm()
    try:
        response = chain.invoke({
            "question": question,
            "student_answer": student_answer,
            "correct_answer": correct_answer,
        })
        return response.content.strip()
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return "Great effort! Keep practicing to improve."


def answer_follow_up(current_question: dict, student_message: str) -> str:
    from langchain_core.prompts import ChatPromptTemplate
    prompt = ChatPromptTemplate.from_messages([
        ("system", "You are Aria, an SAT tutor. The student has a question about the current SAT question.\n\n"
         "Current question: {question}\n"
         "Options:\nA) {option_a}\nB) {option_b}\nC) {option_c}\nD) {option_d}\n"
         "Correct answer: {correct_answer}\n\n"
         'The student asks: "{student_message}"\n\n'
         "Give a clear, concise, and encouraging answer (under 250 characters).\n"
         "If they ask for an explanation of why the correct answer is correct, explain briefly.\n"
         "If they ask for a hint, give a subtle hint.\n"
         "If they ask something else, answer directly.\n"
         "Do NOT ask a new question."),
        ("human", "{student_message}"),
    ])
    options = current_question.get("options", ["", "", "", ""])
    chain = prompt | _get_llm()
    response = chain.invoke({
        "question": current_question.get("question", ""),
        "option_a": options[0] if len(options) > 0 else "",
        "option_b": options[1] if len(options) > 1 else "",
        "option_c": options[2] if len(options) > 2 else "",
        "option_d": options[3] if len(options) > 3 else "",
        "correct_answer": current_question.get("correct_answer", ""),
        "student_message": student_message,
    })
    return response.content.strip()


def generate_reminder(name: str, subject: str) -> str:
    chain = _reminder_prompt() | _get_llm()
    try:
        response = chain.invoke({"name": name, "subject": subject})
        return response.content.strip()
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return f"Hi {name}! Time to practice {subject}. Reply HELP for commands."
