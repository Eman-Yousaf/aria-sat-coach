import os
import shutil
import time

from config import CHROMA_DB_PATH

_SENTENCE_TRANSFORMER_MODEL = "all-MiniLM-L6-v2"

_embedding_func = None
_client = None


def _get_embedding_func():
    global _embedding_func
    if _embedding_func is None:
        from chromadb.utils import embedding_functions
        t = time.time()
        print("[init] loading embedding model (takes ~40s)...", flush=True)
        _embedding_func = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name=_SENTENCE_TRANSFORMER_MODEL
        )
        print(f"[init] embedding model ready ({time.time() - t:.1f}s)", flush=True)
    return _embedding_func


def _get_client():
    global _client
    if _client is None:
        import chromadb
        print("[init] connecting to ChromaDB...", flush=True)
        _client = chromadb.PersistentClient(path=CHROMA_DB_PATH)
        print("[init] ChromaDB connected", flush=True)
    return _client

_QUESTIONS_COLLECTION_NAME = "sat_questions"
_PASSAGES_COLLECTION_NAME = "sat_passages"

_SEED_QUESTIONS = [
    # ===== MATH: Algebra (Easy) =====
    {
        "subject": "Math",
        "weakness": "Algebra",
        "difficulty": "easy",
        "question": "If 3x + 7 = 22, what is the value of x?",
        "options": ["3", "5", "7", "15"],
        "correct_answer": "B",
    },
    {
        "subject": "Math",
        "weakness": "Algebra",
        "difficulty": "easy",
        "question": "Solve for y: 2y - 5 = 3y + 2",
        "options": ["-7", "7", "3", "-3"],
        "correct_answer": "A",
    },
    # ===== MATH: Algebra (Medium) =====
    {
        "subject": "Math",
        "weakness": "Algebra",
        "difficulty": "medium",
        "question": "If x^2 - 5x + 6 = 0, what are the values of x?",
        "options": ["2 and 3", "-2 and -3", "1 and 6", "-1 and -6"],
        "correct_answer": "A",
    },
    {
        "subject": "Math",
        "weakness": "Algebra",
        "difficulty": "medium",
        "question": "If f(x) = 2x^2 - 3x + 1, what is f(-1)?",
        "options": ["0", "6", "-4", "2"],
        "correct_answer": "B",
    },
    # ===== MATH: Algebra (Hard) =====
    {
        "subject": "Math",
        "weakness": "Algebra",
        "difficulty": "hard",
        "question": "If (x + y)^2 = 100 and xy = 21, what is x^2 + y^2?",
        "options": ["58", "79", "100", "121"],
        "correct_answer": "A",
    },
    # ===== MATH: Problem Solving (Easy) =====
    {
        "subject": "Math",
        "weakness": "Problem Solving",
        "difficulty": "easy",
        "question": "A store sells apples at $0.50 each. How many can you buy with $6?",
        "options": ["10", "12", "15", "3"],
        "correct_answer": "B",
    },
    # ===== MATH: Problem Solving (Medium) =====
    {
        "subject": "Math",
        "weakness": "Problem Solving",
        "difficulty": "medium",
        "question": "A car travels 240 miles at 60 mph. How many minutes does the trip take?",
        "options": ["180", "240", "120", "360"],
        "correct_answer": "B",
    },
    # ===== MATH: Problem Solving (Hard) =====
    {
        "subject": "Math",
        "weakness": "Problem Solving",
        "difficulty": "hard",
        "question": "If the probability of rain on any given day is 0.3, what is the probability it rains on exactly 2 of the next 3 days?",
        "options": ["0.189", "0.441", "0.027", "0.063"],
        "correct_answer": "A",
    },
    # ===== MATH: Data Analysis (Easy) =====
    {
        "subject": "Math",
        "weakness": "Data Analysis",
        "difficulty": "easy",
        "question": "What is the median of 4, 8, 15, 16, 23?",
        "options": ["8", "15", "16", "13"],
        "correct_answer": "B",
    },
    # ===== MATH: Data Analysis (Medium) =====
    {
        "subject": "Math",
        "weakness": "Data Analysis",
        "difficulty": "medium",
        "question": "A data set has mean 50 and standard deviation 5. What percent falls within 1 SD of the mean?",
        "options": ["~68%", "~95%", "~50%", "~99.7%"],
        "correct_answer": "A",
    },
    # ===== MATH: Data Analysis (Hard) =====
    {
        "subject": "Math",
        "weakness": "Data Analysis",
        "difficulty": "hard",
        "question": "In a survey, 60% prefer X and 40% prefer Y. Margin of error is 4%. What is the confidence interval for X?",
        "options": ["56%-64%", "60%-64%", "56%-60%", "58%-62%"],
        "correct_answer": "A",
    },
    # ===== MATH: Advanced Math (Easy) =====
    {
        "subject": "Math",
        "weakness": "Advanced Math",
        "difficulty": "easy",
        "question": "What is the slope of the line y = 3x - 7?",
        "options": ["3", "-7", "7", "-3"],
        "correct_answer": "A",
    },
    # ===== MATH: Advanced Math (Medium) =====
    {
        "subject": "Math",
        "weakness": "Advanced Math",
        "difficulty": "medium",
        "question": "For the quadratic y = x^2 - 4x + 3, what is the x-coordinate of the vertex?",
        "options": ["2", "-2", "1", "3"],
        "correct_answer": "A",
    },
    # ===== MATH: Advanced Math (Hard) =====
    {
        "subject": "Math",
        "weakness": "Advanced Math",
        "difficulty": "hard",
        "question": "If sin(theta) = 0.6 and theta is acute, what is cos(theta)?",
        "options": ["0.8", "0.4", "0.75", "0.64"],
        "correct_answer": "A",
    },
    # ===== MATH: Linear Equations (Easy) =====
    {
        "subject": "Math",
        "weakness": "Linear Equations",
        "difficulty": "easy",
        "question": "Which point lies on the line y = 2x + 1?",
        "options": ["(0,1)", "(1,0)", "(2,3)", "(3,5)"],
        "correct_answer": "A",
    },
    # ===== MATH: Linear Equations (Medium) =====
    {
        "subject": "Math",
        "weakness": "Linear Equations",
        "difficulty": "medium",
        "question": "Two lines: y = 2x + 3 and y = 2x - 1. How do they relate?",
        "options": ["Parallel", "Perpendicular", "Intersect at (0,3)", "Same line"],
        "correct_answer": "A",
    },
    # ===== MATH: Linear Equations (Hard) =====
    {
        "subject": "Math",
        "weakness": "Linear Equations",
        "difficulty": "hard",
        "question": "Solve the system: 3x + 2y = 16, 2x - y = 6. What is x + y?",
        "options": ["5", "6", "7", "8"],
        "correct_answer": "C",
    },
    # ===== MATH: Additional =====
    {
        "subject": "Math",
        "weakness": "Algebra",
        "difficulty": "hard",
        "question": "If 2^(x+1) = 32, what is the value of x?",
        "options": ["4", "5", "15", "16"],
        "correct_answer": "A",
    },
    {
        "subject": "Math",
        "weakness": "Problem Solving",
        "difficulty": "medium",
        "question": "A recipe calls for 3 eggs for 12 cookies. How many eggs for 30 cookies?",
        "options": ["6", "7.5", "8", "9"],
        "correct_answer": "B",
    },
    {
        "subject": "Math",
        "weakness": "Data Analysis",
        "difficulty": "medium",
        "question": "A scatter plot shows r = -0.8. What does this indicate?",
        "options": ["Strong negative correlation", "Weak negative correlation", "Strong positive correlation", "No correlation"],
        "correct_answer": "A",
    },
    # ===== READING: Inference (Easy) =====
    {
        "subject": "Reading",
        "weakness": "Inference",
        "difficulty": "easy",
        "question": "The author says she 'felt a chill run down her spine.' What does this imply?",
        "options": ["She was scared", "She was cold", "She was excited", "She was tired"],
        "correct_answer": "A",
    },
    # ===== READING: Inference (Medium) =====
    {
        "subject": "Reading",
        "weakness": "Inference",
        "difficulty": "medium",
        "question": "The passage says the character 'glanced at the clock for the fifth time in ten minutes.' What does this suggest?",
        "options": ["She is impatient", "She is sleepy", "She is bored", "She is early"],
        "correct_answer": "A",
    },
    # ===== READING: Inference (Hard) =====
    {
        "subject": "Reading",
        "weakness": "Inference",
        "difficulty": "hard",
        "question": "The author describes a 'polite smile that did not reach her eyes.' What is inferred?",
        "options": ["She is being insincere", "She is happy", "She is angry", "She is confused"],
        "correct_answer": "A",
    },
    # ===== READING: Main Idea (Easy) =====
    {
        "subject": "Reading",
        "weakness": "Main Idea",
        "difficulty": "easy",
        "question": "A passage discusses the life cycle of butterflies. What is the main idea?",
        "options": ["How butterflies develop", "Where butterflies live", "What butterflies eat", "Butterfly predators"],
        "correct_answer": "A",
    },
    # ===== READING: Main Idea (Medium) =====
    {
        "subject": "Reading",
        "weakness": "Main Idea",
        "difficulty": "medium",
        "question": "The passage describes both benefits and drawbacks of social media. The main purpose is to:",
        "options": ["Present a balanced view", "Criticize social media", "Promote social media", "Compare platforms"],
        "correct_answer": "A",
    },
    # ===== READING: Main Idea (Hard) =====
    {
        "subject": "Reading",
        "weakness": "Main Idea",
        "difficulty": "hard",
        "question": "The passage traces the evolution of democracy from ancient Greece to modern times. The central claim is that:",
        "options": ["Democracy adapts to cultural contexts", "Greece had the best democracy", "Modern democracy is flawed", "Ancient systems were simpler"],
        "correct_answer": "A",
    },
    # ===== READING: Vocabulary in Context (Easy) =====
    {
        "subject": "Reading",
        "weakness": "Vocabulary in Context",
        "difficulty": "easy",
        "question": "In the sentence 'The room was illuminated by candles,' 'illuminated' most nearly means:",
        "options": ["Lit up", "Decorated", "Filled", "Warmed"],
        "correct_answer": "A",
    },
    # ===== READING: Vocabulary in Context (Medium) =====
    {
        "subject": "Reading",
        "weakness": "Vocabulary in Context",
        "difficulty": "medium",
        "question": "The word 'ephemeral' in 'the ephemeral beauty of cherry blossoms' most nearly means:",
        "options": ["Short-lived", "Seasonal", "Delicate", "Colorful"],
        "correct_answer": "A",
    },
    # ===== READING: Vocabulary in Context (Hard) =====
    {
        "subject": "Reading",
        "weakness": "Vocabulary in Context",
        "difficulty": "hard",
        "question": "In a scientific context, 'the results were equivocal' means the results were:",
        "options": ["Ambiguous", "Positive", "Negative", "Surprising"],
        "correct_answer": "A",
    },
    # ===== READING: Command of Evidence (Medium) =====
    {
        "subject": "Reading",
        "weakness": "Command of Evidence",
        "difficulty": "medium",
        "question": "Which statement from the passage best supports the claim that the author values education?",
        "options": ["She spent years in the library", "She disliked school", "She quit at age 16", "She taught herself"],
        "correct_answer": "A",
    },
    # ===== READING: Additional =====
    {
        "subject": "Reading",
        "weakness": "Inference",
        "difficulty": "medium",
        "question": "The passage states 'he wore a uniform every day for 30 years.' What career is implied?",
        "options": ["Military or police", "Chef", "Doctor", "Lawyer"],
        "correct_answer": "A",
    },
    {
        "subject": "Reading",
        "weakness": "Main Idea",
        "difficulty": "easy",
        "question": "A text explains three ways to reduce plastic waste. The main idea is:",
        "options": ["Strategies to reduce plastic", "Plastic is harmful", "Recycling is best", "Plastic bans work"],
        "correct_answer": "A",
    },
    {
        "subject": "Reading",
        "weakness": "Vocabulary in Context",
        "difficulty": "medium",
        "question": "In 'her argument was cogent and well-supported,' 'cogent' most nearly means:",
        "options": ["Convincing", "Confusing", "Creative", "Controversial"],
        "correct_answer": "A",
    },
    # ===== WRITING: Grammar (Easy) =====
    {
        "subject": "Writing",
        "weakness": "Grammar",
        "difficulty": "easy",
        "question": "Choose the correct form: 'Neither the teacher nor the students ___ ready.'",
        "options": ["is", "are", "was", "has been"],
        "correct_answer": "B",
    },
    # ===== WRITING: Grammar (Medium) =====
    {
        "subject": "Writing",
        "weakness": "Grammar",
        "difficulty": "medium",
        "question": "Which is correct? 'Each of the players ___ a uniform.'",
        "options": ["has", "have", "are having", "were having"],
        "correct_answer": "A",
    },
    # ===== WRITING: Grammar (Hard) =====
    {
        "subject": "Writing",
        "weakness": "Grammar",
        "difficulty": "hard",
        "question": "Identify the error: 'The committee have reached their decision.'",
        "options": ["Subject-verb agreement", "Pronoun agreement", "Verb tense", "No error"],
        "correct_answer": "A",
    },
    # ===== WRITING: Punctuation (Easy) =====
    {
        "subject": "Writing",
        "weakness": "Punctuation",
        "difficulty": "easy",
        "question": "Which is correct? 'Its ___ your turn.'",
        "options": ["its", "it's", "its'", "its's"],
        "correct_answer": "B",
    },
    # ===== WRITING: Punctuation (Medium) =====
    {
        "subject": "Writing",
        "weakness": "Punctuation",
        "difficulty": "medium",
        "question": "Choose the correct sentence:",
        "options": [
            "I like reading, writing, and arithmetic.",
            "I like reading writing, and arithmetic.",
            "I like reading, writing and, arithmetic.",
            "I like reading writing and arithmetic.",
        ],
        "correct_answer": "A",
    },
    # ===== WRITING: Punctuation (Hard) =====
    {
        "subject": "Writing",
        "weakness": "Punctuation",
        "difficulty": "hard",
        "question": "Which uses the semicolon correctly?",
        "options": [
            "I went home; it was late.",
            "I went home; and it was late.",
            "I went home; because it was late.",
            "I went home; however late.",
        ],
        "correct_answer": "A",
    },
    # ===== WRITING: Sentence Structure (Easy) =====
    {
        "subject": "Writing",
        "weakness": "Sentence Structure",
        "difficulty": "easy",
        "question": "Which is a complete sentence?",
        "options": [
            "She ran home.",
            "Running home.",
            "Because she ran.",
            "When she ran home.",
        ],
        "correct_answer": "A",
    },
    # ===== WRITING: Sentence Structure (Medium) =====
    {
        "subject": "Writing",
        "weakness": "Sentence Structure",
        "difficulty": "medium",
        "question": "Fix the run-on: 'I love coding it is fun.'",
        "options": [
            "I love coding. It is fun.",
            "I love coding it is fun.",
            "I love coding, it is fun.",
            "I love coding: it is fun.",
        ],
        "correct_answer": "A",
    },
    # ===== WRITING: Sentence Structure (Hard) =====
    {
        "subject": "Writing",
        "weakness": "Sentence Structure",
        "difficulty": "hard",
        "question": "Which revision fixes the misplaced modifier? 'Walking to school, the bus passed me.'",
        "options": [
            "While I was walking to school, the bus passed me.",
            "Walking to school, the bus passed me.",
            "The bus passed me walking to school.",
            "Walking to school, passing me was the bus.",
        ],
        "correct_answer": "A",
    },
    # ===== WRITING: Style and Tone (Easy) =====
    {
        "subject": "Writing",
        "weakness": "Style and Tone",
        "difficulty": "easy",
        "question": "Which is more formal?",
        "options": [
            "The company regrets the delay.",
            "Sorry for the delay!",
            "Our bad about the delay.",
            "Delay happened, sorry.",
        ],
        "correct_answer": "A",
    },
    # ===== WRITING: Style and Tone (Medium) =====
    {
        "subject": "Writing",
        "weakness": "Style and Tone",
        "difficulty": "medium",
        "question": "In academic writing, which is preferred?",
        "options": [
            "Many researchers believe...",
            "A lot of scientists think...",
            "Tons of experts say...",
            "Lots of folks argue...",
        ],
        "correct_answer": "A",
    },
    # ===== WRITING: Style and Tone (Hard) =====
    {
        "subject": "Writing",
        "weakness": "Style and Tone",
        "difficulty": "hard",
        "question": "Which revision eliminates wordiness? 'Due to the fact that the weather was bad, the event was cancelled.'",
        "options": [
            "Because of bad weather, the event was cancelled.",
            "Due to the fact that the weather was bad, the event was cancelled.",
            "The weather being bad was why the event was cancelled.",
            "Bad weather caused the cancellation of the event.",
        ],
        "correct_answer": "A",
    },
    # ===== WRITING: Additional =====
    {
        "subject": "Writing",
        "weakness": "Grammar",
        "difficulty": "medium",
        "question": "Which is correct? 'If I ___ you, I would study more.'",
        "options": ["were", "was", "am", "would be"],
        "correct_answer": "A",
    },
    {
        "subject": "Writing",
        "weakness": "Punctuation",
        "difficulty": "medium",
        "question": "Choose the correct sentence:",
        "options": [
            "She said, 'Hello, world.'",
            "She said 'Hello world.'",
            "She said, 'Hello world'",
            "She said 'Hello, world'",
        ],
        "correct_answer": "A",
    },
    {
        "subject": "Writing",
        "weakness": "Punctuation",
        "difficulty": "hard",
        "question": "Which uses dashes correctly?",
        "options": [
            "He gave three options—none were good.",
            "He gave three options - none were good.",
            "He gave three options: none were good.",
            "He gave three options; none were good.",
        ],
        "correct_answer": "A",
    },
]

_SEED_PASSAGES = [
    {
        "id": "passage_1",
        "subject": "Reading",
        "text": "Marie Curie was a pioneering physicist and chemist who conducted groundbreaking research on radioactivity. She was the first woman to win a Nobel Prize and remains the only person to win Nobel Prizes in two different scientific fields. Her work laid the foundation for modern nuclear physics and cancer treatment.",
    },
    {
        "id": "passage_2",
        "subject": "Reading",
        "text": "Climate change poses significant challenges to global food security. Rising temperatures and changing precipitation patterns affect crop yields worldwide. Scientists are developing drought-resistant crops and sustainable farming practices to adapt to these changes, emphasizing the need for international cooperation.",
    },
    {
        "id": "passage_3",
        "subject": "Reading",
        "text": "Social media has transformed how people communicate and share information. While it enables instant global connection, concerns about privacy and misinformation have grown. Studies suggest that excessive social media use may impact mental health, particularly among younger users.",
    },
    {
        "id": "passage_4",
        "subject": "Reading",
        "text": "The Renaissance was a period of cultural and intellectual rebirth in Europe. It marked a shift from medieval scholasticism to humanism, emphasizing individual potential and classical learning. Artists like Leonardo da Vinci and Michelangelo created works that continue to influence art today.",
    },
    {
        "id": "passage_5",
        "subject": "Reading",
        "text": "Space exploration has yielded numerous technological advances that benefit everyday life. Satellite technology enables GPS navigation, weather forecasting, and global communications. Despite high costs, space agencies continue to push boundaries, with missions to Mars and beyond.",
    },
]


def _init_collections():
    ef = _get_embedding_func()
    client = _get_client()
    try:
        client.get_collection(_QUESTIONS_COLLECTION_NAME)
    except Exception:
        questions_collection = client.create_collection(
            name=_QUESTIONS_COLLECTION_NAME,
            embedding_function=ef,
        )
        for i, q in enumerate(_SEED_QUESTIONS):
            metadata = {
                "subject": q["subject"],
                "weakness": q["weakness"],
                "difficulty": q["difficulty"],
                "options": ",".join(q["options"]),
                "correct_answer": q["correct_answer"],
            }
            questions_collection.add(
                documents=[q["question"]],
                metadatas=[metadata],
                ids=[f"q_{i}"],
            )

    try:
        client.get_collection(_PASSAGES_COLLECTION_NAME)
    except Exception:
        passages_collection = client.create_collection(
            name=_PASSAGES_COLLECTION_NAME,
            embedding_function=ef,
        )
        for p in _SEED_PASSAGES:
            passages_collection.add(
                documents=[p["text"]],
                metadatas=[{"subject": p["subject"], "passage_id": p["id"]}],
                ids=[p["id"]],
            )


_collections_initialized = False


def _ensure_collections():
    global _collections_initialized
    if not _collections_initialized:
        _init_collections()
        _collections_initialized = True


def get_question_by_subject(
    subject: str,
    difficulty: str = "medium",
    weakness: str | None = None,
) -> dict | None:
    _ensure_collections()
    ef = _get_embedding_func()
    collection = _get_client().get_collection(
        name=_QUESTIONS_COLLECTION_NAME,
        embedding_function=ef,
    )

    if weakness:
        results = collection.query(
            query_texts=[f"{subject} {weakness} {difficulty}"],
            n_results=3,
            where={
                "$and": [
                    {"subject": {"$eq": subject}},
                    {"weakness": {"$eq": weakness}},
                ]
            },
        )
        if results and results["ids"] and results["ids"][0]:
            idx = 0
            if len(results["ids"][0]) > 1:
                diff_order = {"easy": 0, "medium": 1, "hard": 2}
                scored = sorted(
                    range(len(results["ids"][0])),
                    key=lambda i: abs(
                        diff_order.get(
                            results["metadatas"][0][i].get("difficulty", "medium"), 1
                        )
                        - diff_order.get(difficulty, 1)
                    ),
                )
                idx = scored[0]
            return _format_question_result(results, idx)

    results = collection.query(
        query_texts=[f"{subject} {difficulty}"],
        n_results=1,
        where={
            "$and": [
                {"subject": {"$eq": subject}},
                {"difficulty": {"$eq": difficulty}},
            ]
        },
    )
    if results and results["ids"] and results["ids"][0]:
        return _format_question_result(results, 0)

    results = collection.query(
        query_texts=[subject],
        n_results=1,
        where={"subject": {"$eq": subject}},
    )
    if results and results["ids"] and results["ids"][0]:
        return _format_question_result(results, 0)

    return None


def _format_question_result(results, idx: int) -> dict:
    meta = results["metadatas"][0][idx]
    options = meta.get("options", "").split(",")
    return {
        "question": results["documents"][0][idx],
        "options": options if len(options) == 4 else ["A", "B", "C", "D"],
        "correct_answer": meta.get("correct_answer", "A"),
        "subject": meta.get("subject", ""),
        "weakness": meta.get("weakness", ""),
        "difficulty": meta.get("difficulty", "medium"),
    }


def get_reading_passage() -> dict | None:
    _ensure_collections()
    ef = _get_embedding_func()
    collection = _get_client().get_collection(
        name=_PASSAGES_COLLECTION_NAME,
        embedding_function=ef,
    )
    import random
    all_passages = collection.get()
    if all_passages and all_passages["ids"]:
        idx = random.randint(0, len(all_passages["ids"]) - 1)
        return {
            "id": all_passages["ids"][idx],
            "text": all_passages["documents"][idx],
        }
    return None
