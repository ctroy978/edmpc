from typing import Optional


def get_evaluation_prompt(essay_text: str, rubric: str, context_material: str, system_instructions: Optional[str] = None) -> str:
    """
    Constructs the prompt for AI-based essay evaluation.
    Forces detailed, structured JSON output with criteria-specific feedback.
    """

    # Build prompt sections dynamically
    sections = []

    # Instructions section
    sections.append(
        "You are an experienced, thoughtful writing instructor. "
        "Your feedback is detailed, encouraging, balanced, and constructive. "
        "You justify scores by referencing rubric expectations and thresholds, "
        "acknowledge strengths before offering suggestions, and write in a conversational, teacherly tone. "
        "You never revert to terse bullet points or abrupt lists."
    )

    # Essay question section (if provided)
    if system_instructions and system_instructions.strip():
        sections.append(f"""
---
# ESSAY QUESTION/PROMPT:
{system_instructions.strip()}

(This is provided for context. The rubric below may reference this question.)
""")

    # Context material section (only if provided)
    if context_material and context_material.strip():
        sections.append(f"""
---
# CONTEXT / SOURCE MATERIAL:
{context_material.strip()}

(This is provided for reference. The rubric below may expect students to engage with this material.)
""")

    # Rubric section
    sections.append(f"""
---
# GRADING RUBRIC:
{rubric}
""")

    # Essay text section
    sections.append(f"""
---
# STUDENT ESSAY:
{essay_text}
""")

    # Output instructions section
    sections.append(f"""
---
# OUTPUT INSTRUCTIONS:
Evaluate the student's essay strictly according to the provided grading rubric. First, identify the distinct criteria from the rubric.

For each criterion:
- Assign a score based on the points specified in the rubric.
- Write a single prose explanation of 100–200 words (4–8 sentences). This explanation must:
  1. Open by acknowledging specific strengths — cite evidence from the essay naturally within your sentences (do NOT list raw quotes at the end).
  2. Justify the score by referencing what the rubric expects at this level and the level above (e.g., "A top score typically requires X; your essay does Y well but Z would push it further").
  3. Offer 1–2 concrete, encouraging suggestions for improvement, phrased positively (e.g., "Adding X would elevate this to the next level").
  4. Use a conversational, teacherly tone throughout. Avoid pedantic focus on minor issues.
  5. Write in full paragraphs — no bullet points, no numbered lists, no headings within the explanation.

You must output ONLY a valid JSON object. The JSON must follow this exact structure:

{{
  "criteria": [
    {{
      "name": "Criterion Name",
      "score": "Numeric score or letter grade",
      "feedback": {{
        "explanation": "Full prose paragraph of 100-200 words explaining the score, praising strengths, and offering improvement suggestions."
      }}
    }}
  ],
  "overall_score": "Total score as a string (e.g. '95', 'A', '18/20')",
  "summary": "A brief overall summary of the essay's strengths and weaknesses."
}}

Do not add extra keys, explanations, or text outside the JSON.
""")

    # Join all sections together
    prompt = "\n".join(sections)
    return prompt.strip()
