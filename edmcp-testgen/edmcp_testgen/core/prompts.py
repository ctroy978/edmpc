"""
Prompt templates for AI-powered question generation.
"""

from typing import Dict, List, Optional

# System prompt for question generation
SYSTEM_PROMPT = """You are an expert educational assessment designer. You create high-quality, pedagogically sound test questions based on provided learning materials. Your questions are:

1. Directly grounded in the source material - never invent facts
2. Clear and unambiguous in wording
3. Appropriately challenging for the specified difficulty level
4. Diverse in the concepts they assess
5. Free from cultural bias or assumptions

You always return valid JSON as specified in the output format."""


def get_material_analysis_prompt(material_text: str, focus_topics: Optional[List[str]] = None) -> str:
    """
    Generate a prompt to analyze learning materials and extract key concepts.
    """
    focus_instruction = ""
    if focus_topics:
        focus_instruction = f"\n\nPay special attention to these focus topics: {', '.join(focus_topics)}"

    return f"""Analyze the following learning material and extract the key concepts that could be tested.

MATERIAL:
{material_text}
{focus_instruction}

Identify and categorize:
1. Key vocabulary/definitions
2. Important facts and dates
3. Cause-and-effect relationships
4. Main ideas and themes
5. Procedures or processes
6. Comparisons and contrasts

Return your analysis as JSON:
{{
    "vocabulary": [
        {{"term": "...", "definition": "...", "importance": "high|medium|low"}}
    ],
    "facts": [
        {{"fact": "...", "context": "...", "importance": "high|medium|low"}}
    ],
    "relationships": [
        {{"type": "cause-effect|comparison|sequence", "description": "...", "elements": ["...", "..."]}}
    ],
    "main_ideas": [
        {{"idea": "...", "supporting_details": ["...", "..."]}}
    ],
    "processes": [
        {{"name": "...", "steps": ["...", "..."]}}
    ],
    "overall_themes": ["...", "..."],
    "suggested_question_count": {{
        "easy": ...,
        "medium": ...,
        "hard": ...
    }}
}}"""


def get_mcq_generation_prompt(
    context: str,
    count: int,
    difficulty: str,
    grade_level: Optional[str] = None,
    existing_questions: Optional[List[str]] = None,
) -> str:
    """
    Generate a prompt for multiple choice question generation.
    """
    difficulty_guidance = {
        "easy": "Focus on direct recall, definitions, and facts stated explicitly in the material.",
        "medium": "Focus on application, comprehension, and rephrasing of concepts. Students should understand, not just memorize.",
        "hard": "Focus on analysis, inference, and synthesis. Require connecting multiple concepts or applying knowledge to new situations.",
    }

    grade_instruction = ""
    if grade_level:
        grade_instruction = f"\nTarget grade level: {grade_level}. Use age-appropriate vocabulary and complexity."

    existing_instruction = ""
    if existing_questions:
        existing_instruction = f"\n\nAVOID creating questions similar to these already-generated questions:\n" + "\n".join(f"- {q}" for q in existing_questions[:10])

    return f"""Generate {count} multiple choice questions based on the following material.

DIFFICULTY LEVEL: {difficulty}
{difficulty_guidance.get(difficulty, difficulty_guidance["medium"])}
{grade_instruction}
{existing_instruction}

SOURCE MATERIAL:
{context}

REQUIREMENTS:
- Each question must have exactly 4 options (A, B, C, D)
- Only ONE option should be correct
- The 3 incorrect options (distractors) must be plausible but clearly wrong
- Questions must be directly answerable from the material
- Avoid "all of the above" or "none of the above" options
- Vary the position of the correct answer across questions

Return as JSON array:
[
    {{
        "question_text": "...",
        "options": [
            {{"letter": "A", "text": "..."}},
            {{"letter": "B", "text": "..."}},
            {{"letter": "C", "text": "..."}},
            {{"letter": "D", "text": "..."}}
        ],
        "correct_answer": "B",
        "difficulty": "{difficulty}",
        "source_reference": "Brief quote or reference to the source material",
        "distractors_rationale": "Why each incorrect option is plausible but wrong"
    }}
]"""


def get_fib_generation_prompt(
    context: str,
    count: int,
    difficulty: str,
    grade_level: Optional[str] = None,
    existing_questions: Optional[List[str]] = None,
) -> str:
    """
    Generate a prompt for fill-in-the-blank question generation.
    """
    difficulty_guidance = {
        "easy": "Use blanks for simple vocabulary terms and key definitions that appear explicitly in the text.",
        "medium": "Use blanks for concepts that require understanding context, or for completing cause-effect statements.",
        "hard": "Use blanks that require synthesis or inference, such as completing comparisons or applying concepts.",
    }

    grade_instruction = ""
    if grade_level:
        grade_instruction = f"\nTarget grade level: {grade_level}. Use age-appropriate vocabulary."

    existing_instruction = ""
    if existing_questions:
        existing_instruction = f"\n\nAVOID blanks for terms already tested:\n" + "\n".join(f"- {q}" for q in existing_questions[:10])

    return f"""Generate {count} fill-in-the-blank questions based on the following material.

DIFFICULTY LEVEL: {difficulty}
{difficulty_guidance.get(difficulty, difficulty_guidance["medium"])}
{grade_instruction}
{existing_instruction}

SOURCE MATERIAL:
{context}

REQUIREMENTS:
- Each sentence should have 1-2 blanks maximum
- The blank should test an important concept, not trivial words
- The sentence should provide enough context to determine the answer
- Use underscores to indicate the blank: __________
- Include acceptable alternative answers if applicable

Return as JSON array:
[
    {{
        "question_text": "The process by which plants convert sunlight into energy is called __________.",
        "correct_answer": "photosynthesis",
        "acceptable_answers": ["photosynthesis"],
        "difficulty": "{difficulty}",
        "source_reference": "Brief quote or reference to the source material"
    }}
]"""


def get_sa_generation_prompt(
    context: str,
    count: int,
    difficulty: str,
    grade_level: Optional[str] = None,
    include_rubrics: bool = True,
    existing_questions: Optional[List[str]] = None,
) -> str:
    """
    Generate a prompt for short answer question generation.
    """
    difficulty_guidance = {
        "easy": "Ask for simple explanations or descriptions of concepts directly from the material.",
        "medium": "Ask for explanations with examples, comparisons, or application to scenarios.",
        "hard": "Ask for analysis, evaluation, or synthesis of multiple concepts. Include 'what if' scenarios.",
    }

    grade_instruction = ""
    if grade_level:
        grade_instruction = f"\nTarget grade level: {grade_level}. Adjust expected response length and complexity."

    rubric_instruction = ""
    if include_rubrics:
        rubric_instruction = """
Include a detailed rubric for each question with:
- Total points
- Specific criteria with point values
- Description of what earns full/partial/no credit for each criterion"""

    existing_instruction = ""
    if existing_questions:
        existing_instruction = f"\n\nAVOID topics already covered:\n" + "\n".join(f"- {q}" for q in existing_questions[:10])

    return f"""Generate {count} short answer questions based on the following material.

DIFFICULTY LEVEL: {difficulty}
{difficulty_guidance.get(difficulty, difficulty_guidance["medium"])}
{grade_instruction}
{existing_instruction}

SOURCE MATERIAL:
{context}

REQUIREMENTS:
- Questions should require 2-5 sentence responses
- Include a complete model answer
- Questions should assess understanding, not just recall
{rubric_instruction}

Return as JSON array:
[
    {{
        "question_text": "Explain how... Provide at least two examples.",
        "model_answer": "A complete model response that would earn full credit...",
        "points": 6,
        "difficulty": "{difficulty}",
        "source_reference": "Brief reference to relevant source material",
        "rubric": {{
            "total_points": 6,
            "criteria": [
                {{
                    "name": "Understanding of concept",
                    "points": 2,
                    "full_credit": "Clearly explains the main concept",
                    "partial_credit": "Shows some understanding but incomplete",
                    "no_credit": "Incorrect or missing explanation"
                }},
                {{
                    "name": "Examples provided",
                    "points": 2,
                    "full_credit": "Provides two relevant examples",
                    "partial_credit": "Provides one example or weak examples",
                    "no_credit": "No examples or incorrect examples"
                }},
                {{
                    "name": "Clarity and organization",
                    "points": 2,
                    "full_credit": "Response is clear and well-organized",
                    "partial_credit": "Response is understandable but disorganized",
                    "no_credit": "Response is unclear or incoherent"
                }}
            ]
        }}
    }}
]"""


def get_question_regeneration_prompt(
    original_question: Dict,
    context: str,
    reason: Optional[str] = None,
    difficulty: Optional[str] = None,
) -> str:
    """
    Generate a prompt to regenerate a specific question.
    """
    question_type = original_question.get("question_type", "mcq")
    current_difficulty = original_question.get("difficulty", "medium")
    target_difficulty = difficulty or current_difficulty

    reason_instruction = ""
    if reason:
        reason_instruction = f"\nReason for regeneration: {reason}"

    type_specific = ""
    if question_type == "mcq":
        type_specific = """
Generate a new MCQ with:
- 4 options (A, B, C, D)
- Only 1 correct answer
- 3 plausible distractors
- Different from the original question"""
    elif question_type == "fib":
        type_specific = """
Generate a new fill-in-the-blank with:
- 1-2 blanks for key terms
- Clear context to determine the answer
- Different from the original question"""
    elif question_type == "sa":
        type_specific = """
Generate a new short answer with:
- Model answer
- Detailed rubric
- Different from the original question"""

    return f"""Regenerate this question to create a new, improved version.

ORIGINAL QUESTION:
{original_question.get('question_text', '')}
{reason_instruction}

TARGET DIFFICULTY: {target_difficulty}
QUESTION TYPE: {question_type}
{type_specific}

SOURCE MATERIAL:
{context}

Return the new question in the same JSON format as before, but with different content that tests a related concept."""


def get_difficulty_adjustment_prompt(
    question: Dict,
    target_difficulty: str,
    context: str,
) -> str:
    """
    Generate a prompt to adjust a question's difficulty.
    """
    question_type = question.get("question_type", "mcq")
    current_difficulty = question.get("difficulty", "medium")

    if target_difficulty == "easy":
        adjustment = "Make the question more straightforward by focusing on direct recall and explicit facts."
    elif target_difficulty == "hard":
        adjustment = "Make the question more challenging by requiring analysis, inference, or synthesis."
    else:
        adjustment = "Adjust to medium difficulty, requiring understanding but not complex analysis."

    return f"""Adjust this question's difficulty from {current_difficulty} to {target_difficulty}.

CURRENT QUESTION:
{question.get('question_text', '')}

ADJUSTMENT NEEDED:
{adjustment}

SOURCE MATERIAL:
{context}

Return the adjusted question in the same JSON format, preserving the question type ({question_type}) but modifying the content to match the target difficulty."""


def _round_to_half(value: float) -> float:
    """Round a value to the nearest 0.5 (e.g., 2.3 -> 2.5, 2.6 -> 2.5, 2.8 -> 3.0)."""
    return round(value * 2) / 2


def get_point_distribution_prompt(
    question_distribution: Dict[str, int],
    total_points: float,
) -> Dict[str, float]:
    """
    Calculate point distribution based on question types.
    This is deterministic, not AI-based.
    Points are rounded to nearest 0.5 for teacher-friendly values.
    """
    mcq_count = question_distribution.get("mcq", 0)
    fib_count = question_distribution.get("fib", 0)
    sa_count = question_distribution.get("sa", 0)

    total_questions = mcq_count + fib_count + sa_count
    if total_questions == 0:
        return {"mcq": 0, "fib": 0, "sa": 0}

    # Typical weighting: MCQ/FIB are 1-2 points, SA are 4-10 points
    # Assign weights: MCQ=1, FIB=1.5, SA=5
    mcq_weight = mcq_count * 1.0
    fib_weight = fib_count * 1.5
    sa_weight = sa_count * 5.0
    total_weight = mcq_weight + fib_weight + sa_weight

    if total_weight == 0:
        return {"mcq": 0, "fib": 0, "sa": 0}

    # Calculate points per question type
    mcq_total = (mcq_weight / total_weight) * total_points
    fib_total = (fib_weight / total_weight) * total_points
    sa_total = (sa_weight / total_weight) * total_points

    # Points per question (rounded to nearest 0.5)
    mcq_per = _round_to_half(mcq_total / mcq_count) if mcq_count > 0 else 0
    fib_per = _round_to_half(fib_total / fib_count) if fib_count > 0 else 0
    sa_per = _round_to_half(sa_total / sa_count) if sa_count > 0 else 0

    return {
        "mcq": mcq_per,
        "fib": fib_per,
        "sa": sa_per,
    }
