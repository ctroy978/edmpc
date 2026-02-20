"""
Grader - AI evaluation loop for essay regrading with RAG context.
"""

import json
import os
import sys
from typing import Any, Dict, List, Optional

from edmcp_core import extract_json_from_text, get_openai_client
from openai import APITimeoutError, APIConnectionError, RateLimitError, InternalServerError

from edmcp_regrade.core.prompts import get_evaluation_prompt
from edmcp_regrade.core.regrade_job_manager import RegradeJobManager

AI_RETRIABLE_EXCEPTIONS = (APITimeoutError, APIConnectionError, RateLimitError, InternalServerError)

EVALUATION_SCHEMA = {
    "type": "object",
    "properties": {
        "criteria": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "score": {"type": ["string", "number"]},
                    "feedback": {
                        "type": "object",
                        "properties": {
                            "justification": {"type": "string"},
                            "examples": {"type": "array", "items": {"type": "string"}},
                            "advice": {"type": "string"},
                            "rewritten_example": {"type": "string"},
                        },
                        "required": ["justification", "examples", "advice", "rewritten_example"],
                        "additionalProperties": False,
                    },
                },
                "required": ["name", "score", "feedback"],
                "additionalProperties": False,
            },
        },
        "overall_score": {"type": "string"},
        "summary": {"type": "string"},
    },
    "required": ["criteria", "overall_score", "summary"],
    "additionalProperties": False,
}


class Grader:
    """Handles AI-powered essay evaluation with optional RAG context."""

    def __init__(self, job_manager: RegradeJobManager, kb_manager=None):
        self.job_manager = job_manager
        self.kb_manager = kb_manager

    def grade_job(
        self,
        job_id: str,
        model: Optional[str] = None,
        system_instructions: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Grade all PENDING essays in a job."""
        job = self.job_manager.get_job(job_id)
        if not job:
            return {"status": "error", "message": f"Job not found: {job_id}"}

        rubric = job.get("rubric")
        if not rubric:
            return {"status": "error", "message": f"No rubric set for job {job_id}. Set a rubric first."}

        essays = self.job_manager.get_job_essays(job_id, status="PENDING")
        if not essays:
            return {"status": "warning", "message": f"No pending essays to grade in job {job_id}"}

        # Resolve question_text from job if not overridden
        question_text = system_instructions or job.get("question_text") or ""

        # Set job status to GRADING
        self.job_manager.update_status(job_id, "GRADING")

        model = model or os.environ.get("EVALUATION_API_MODEL") or os.environ.get("XAI_API_MODEL") or "grok-beta"

        try:
            client = get_openai_client(
                api_key=os.environ.get("EVALUATION_API_KEY") or os.environ.get("XAI_API_KEY"),
                base_url=os.environ.get("EVALUATION_BASE_URL") or os.environ.get("XAI_BASE_URL"),
            )
        except Exception as e:
            self.job_manager.update_status(job_id, "PENDING")
            return {"status": "error", "message": f"Failed to get AI client: {e}"}

        response_format = {
            "type": "json_schema",
            "json_schema": {"name": "essay_evaluation", "strict": True, "schema": EVALUATION_SCHEMA},
        }

        # Check for RAG topic
        kb_topic = job.get("knowledge_base_topic")

        evaluated_count = 0
        errors = []

        print(f"[Regrade] Grading {len(essays)} essays for job {job_id}...", file=sys.stderr)

        for essay in essays:
            try:
                essay_id = essay["id"]
                essay_text = essay.get("essay_text", "")

                if not essay_text:
                    continue

                # Retrieve RAG context if available
                context_material = ""
                if kb_topic and self.kb_manager:
                    try:
                        chunks = self.kb_manager.retrieve_context_chunks(
                            essay_text[:500], kb_topic
                        )
                        if chunks:
                            context_material = "\n\n---\n\n".join(chunks)
                    except Exception as e:
                        print(f"[Regrade] RAG retrieval failed for essay {essay_id}: {e}", file=sys.stderr)

                prompt = get_evaluation_prompt(essay_text, rubric, context_material, question_text)

                messages = [
                    {"role": "system", "content": "You are a professional academic evaluator."},
                    {"role": "user", "content": prompt},
                ]

                response = client.chat.completions.create(
                    model=model,
                    messages=messages,
                    response_format=response_format,
                    max_tokens=4000,
                    temperature=0.1,
                )
                raw_eval_text = response.choices[0].message.content.strip()

                eval_data = extract_json_from_text(raw_eval_text)
                if not eval_data:
                    raise ValueError(f"Failed to extract valid JSON from AI response")

                eval_json_str = json.dumps(eval_data)
                grade = str(eval_data.get("overall_score") or eval_data.get("score") or "")

                self.job_manager.update_essay_evaluation(essay_id, eval_json_str, grade)
                evaluated_count += 1

                print(f"[Regrade] Graded essay {essay_id} ({essay.get('student_identifier', 'unknown')}): {grade}", file=sys.stderr)

            except Exception as e:
                error_msg = f"Essay {essay['id']}: {str(e)}"
                print(f"[Regrade] Error grading essay {essay['id']}: {e}", file=sys.stderr)
                errors.append(error_msg)

        # Update job status
        self.job_manager.update_status(job_id, "READY_FOR_REVIEW")

        print(f"[Regrade] Job {job_id} complete. {evaluated_count}/{len(essays)} essays graded.", file=sys.stderr)

        return {
            "status": "success",
            "job_id": job_id,
            "evaluated_count": evaluated_count,
            "total_essays": len(essays),
            "errors": errors if errors else None,
        }

    def generate_merged_report(
        self,
        job_id: str,
        essay_id: int,
        teacher_notes: Optional[str] = None,
        criteria_overrides: Optional[List[Dict[str, Any]]] = None,
        model: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Blend AI evaluation + teacher overrides + notes into per-criterion justification
        text, deferring to the teacher wherever they disagree with the AI.

        Args:
            job_id: The job ID
            essay_id: The essay ID
            teacher_notes: Free-form teacher notes (authoritative)
            criteria_overrides: List of {"name": ..., "score": ...} teacher score overrides
            model: Optional model override

        Returns:
            {"status": "success",
             "criteria_justifications": [{"name": "...", "blended_justification": "..."}, ...],
             "essay_id": ...}
        """
        job = self.job_manager.get_job(job_id)
        if not job:
            return {"status": "error", "message": f"Job not found: {job_id}"}

        essay = self.job_manager.get_essay(essay_id)
        if not essay:
            return {"status": "error", "message": f"Essay not found: {essay_id}"}
        if essay["job_id"] != job_id:
            return {"status": "error", "message": "Essay does not belong to this job"}

        model = model or os.environ.get("EVALUATION_API_MODEL") or os.environ.get("XAI_API_MODEL") or "grok-beta"

        try:
            client = get_openai_client(
                api_key=os.environ.get("EVALUATION_API_KEY") or os.environ.get("XAI_API_KEY"),
                base_url=os.environ.get("EVALUATION_BASE_URL") or os.environ.get("XAI_BASE_URL"),
            )
        except Exception as e:
            return {"status": "error", "message": f"Failed to get AI client: {e}"}

        # Build context sections
        rubric_text = job.get("rubric") or ""
        assignment_title = job.get("assignment_title") or job.get("name") or ""

        ai_eval = essay.get("evaluation") or {}
        ai_eval_parts = []
        if isinstance(ai_eval, dict):
            for c in ai_eval.get("criteria", []):
                cname = c.get("name", "")
                cscore = c.get("score", "")
                feedback = c.get("feedback", {})
                just = feedback.get("justification", "") if isinstance(feedback, dict) else str(feedback)
                advice = feedback.get("advice", "") if isinstance(feedback, dict) else ""
                ai_eval_parts.append(f"  {cname} — Score: {cscore}")
                if just:
                    ai_eval_parts.append(f"    Justification: {just}")
                if advice:
                    ai_eval_parts.append(f"    Advice: {advice}")
            ai_overall = ai_eval.get("overall_score", "")
            if ai_overall:
                ai_eval_parts.append(f"  Overall Score: {ai_overall}")
            ai_summary = ai_eval.get("summary", "")
            if ai_summary:
                ai_eval_parts.append(f"  Summary: {ai_summary}")

        ai_eval_text = "\n".join(ai_eval_parts) if ai_eval_parts else "No AI evaluation available."

        overrides_text = "None"
        if criteria_overrides:
            lines = [f"  {o.get('name', '')}: {o.get('score', '')}" for o in criteria_overrides]
            overrides_text = "\n".join(lines)

        notes_text = teacher_notes.strip() if teacher_notes and teacher_notes.strip() else "None"

        # Build list of criterion names for the prompt
        criteria_names = []
        if isinstance(ai_eval, dict):
            for c in ai_eval.get("criteria", []):
                cname = c.get("name", "")
                if cname:
                    criteria_names.append(cname)

        prompt = f"""ROLE: You are blending an AI evaluation and authoritative teacher input into per-criterion justification text for a student feedback report.

DEFERENCE RULE: Where teacher notes or score overrides differ from the AI assessment, DEFER TO THE TEACHER. Reflect the teacher's view in the blended justification without mentioning the AI or that there was any disagreement.

ASSIGNMENT CONTEXT:
Title: {assignment_title}
Rubric:
{rubric_text or "(no rubric provided)"}

AI EVALUATION (for reference only — teacher overrides are authoritative):
{ai_eval_text}

TEACHER SCORE OVERRIDES (authoritative — use these scores):
{overrides_text}

TEACHER NOTES (authoritative free-form input — incorporate this reasoning into relevant criteria):
{notes_text}

OUTPUT INSTRUCTIONS:
Return a JSON array with one object per criterion. Each object must have:
  "name": the criterion name exactly as listed below
  "blended_justification": 2-3 sentences blending AI justification with teacher notes where applicable

Rules:
- If teacher notes speak to a criterion, incorporate them authoritatively (defer to teacher over AI)
- If teacher notes do not address a criterion, use the AI justification as-is
- Do NOT fabricate teacher input for criteria the teacher did not address
- Do NOT mention "AI", "the teacher", "override", or any meta-commentary
- Tone: professional, encouraging, constructive

Criteria to cover (in this order):
{chr(10).join(f"- {n}" for n in criteria_names)}

Output ONLY valid JSON — no prose, no markdown fences, no extra commentary."""

        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": "You produce structured JSON feedback for student reports."},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=2000,
                temperature=0.4,
                response_format={"type": "json_object"},
            )
            raw = response.choices[0].message.content.strip()

            # Parse the response — expect {"criteria": [...]} or a bare array
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                return {"status": "error", "message": f"AI returned invalid JSON: {raw[:200]}"}

            # Normalise: accept {"criteria": [...]} or a top-level list
            if isinstance(parsed, dict):
                criteria_list = parsed.get("criteria") or parsed.get("criteria_justifications") or []
                # Fall back: if the dict itself contains name/blended_justification it's a single item
                if not criteria_list and "name" in parsed:
                    criteria_list = [parsed]
            elif isinstance(parsed, list):
                criteria_list = parsed
            else:
                criteria_list = []

            print(f"[Regrade] Generated blended justifications for essay {essay_id}", file=sys.stderr)

            return {
                "status": "success",
                "criteria_justifications": criteria_list,
                "essay_id": essay_id,
            }

        except Exception as e:
            print(f"[Regrade] Error generating merged report for essay {essay_id}: {e}", file=sys.stderr)
            return {"status": "error", "message": f"Report generation failed: {e}"}

    def refine_comments(
        self,
        job_id: str,
        essay_ids: Optional[List[int]] = None,
        model: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Use AI to polish teacher draft comments into professional, encouraging feedback.
        Preserves teacher intent while improving clarity and tone.

        Skips essays that already have a generated report (report_generated: true).

        Args:
            job_id: The job ID
            essay_ids: Specific essay IDs to refine. If None, refines all reviewed essays.
            model: Optional model override
        """
        job = self.job_manager.get_job(job_id)
        if not job:
            return {"status": "error", "message": f"Job not found: {job_id}"}

        # Get essays that have teacher comments
        all_essays = self.job_manager.get_job_essays(job_id, include_text=True)
        essays_to_refine = []
        for e in all_essays:
            if essay_ids and e["id"] not in essay_ids:
                continue
            # Skip essays that already have a generated report
            tc_raw = e.get("teacher_comments") or ""
            if tc_raw:
                try:
                    parsed = json.loads(tc_raw)
                    if isinstance(parsed, dict) and parsed.get("report_generated"):
                        print(
                            f"[Regrade] Skipping essay {e['id']} — already has generated report",
                            file=sys.stderr,
                        )
                        continue
                except (json.JSONDecodeError, TypeError):
                    pass
            if e.get("teacher_comments") or e.get("teacher_annotations"):
                essays_to_refine.append(e)

        if not essays_to_refine:
            return {"status": "warning", "message": "No essays with teacher comments to refine"}

        model = model or os.environ.get("EVALUATION_API_MODEL") or os.environ.get("XAI_API_MODEL") or "grok-beta"

        try:
            client = get_openai_client(
                api_key=os.environ.get("EVALUATION_API_KEY") or os.environ.get("XAI_API_KEY"),
                base_url=os.environ.get("EVALUATION_BASE_URL") or os.environ.get("XAI_BASE_URL"),
            )
        except Exception as e:
            return {"status": "error", "message": f"Failed to get AI client: {e}"}

        refined_count = 0
        errors = []

        print(f"[Regrade] Refining comments for {len(essays_to_refine)} essays in job {job_id}...", file=sys.stderr)

        for essay in essays_to_refine:
            try:
                essay_id = essay["id"]

                # Build the refinement prompt
                parts = []
                parts.append("You are an expert writing coach helping a teacher provide professional, encouraging feedback to students.")
                parts.append("Below are the teacher's draft comments and annotations for a student essay. Your task:")
                parts.append("1. Preserve the teacher's intent and specific observations exactly")
                parts.append("2. Make the language clearer, more professional, and encouraging")
                parts.append("3. Tie feedback to the rubric criteria where applicable")
                parts.append("4. Keep the teacher's voice — don't make it sound generic")
                parts.append("")

                if job.get("rubric"):
                    parts.append(f"RUBRIC:\n{job['rubric']}\n")

                # Extract teacher comments — handle new JSON format
                tc_raw = essay.get("teacher_comments") or ""
                teacher_comments_text = tc_raw
                if tc_raw:
                    try:
                        parsed_tc = json.loads(tc_raw)
                        if isinstance(parsed_tc, dict):
                            # Prefer teacher_notes from new format
                            notes = parsed_tc.get("teacher_notes", "")
                            teacher_comments_text = notes if notes else ""
                    except (json.JSONDecodeError, TypeError):
                        pass

                if teacher_comments_text:
                    parts.append(f"TEACHER OVERALL COMMENTS:\n{teacher_comments_text}\n")

                annotations = essay.get("teacher_annotations")
                if annotations:
                    if isinstance(annotations, str):
                        annotations = json.loads(annotations)
                    if isinstance(annotations, list):
                        parts.append("TEACHER INLINE ANNOTATIONS:")
                        for ann in annotations:
                            text = ann.get("selected_text", "")
                            comment = ann.get("comment", "")
                            parts.append(f"  - On \"{text}\": {comment}")
                        parts.append("")

                ai_eval = essay.get("evaluation")
                if ai_eval and isinstance(ai_eval, dict):
                    parts.append(f"AI GRADE: {ai_eval.get('overall_score', 'N/A')}")
                    parts.append(f"AI SUMMARY: {ai_eval.get('summary', 'N/A')}\n")

                parts.append("Return a JSON object with this structure:")
                parts.append('{')
                parts.append('  "refined_comments": "The polished overall comment",')
                parts.append('  "refined_annotations": [{"selected_text": "...", "comment": "refined comment"}]')
                parts.append('}')
                parts.append("Only include refined_annotations if there were inline annotations to refine.")
                parts.append("Return ONLY valid JSON, no extra text.")

                prompt = "\n".join(parts)

                response = client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": "You refine teacher feedback to be professional and encouraging."},
                        {"role": "user", "content": prompt},
                    ],
                    max_tokens=3000,
                    temperature=0.3,
                )
                raw_text = response.choices[0].message.content.strip()

                from edmcp_core import extract_json_from_text
                refined_data = extract_json_from_text(raw_text)
                if not refined_data:
                    raise ValueError("Failed to extract valid JSON from AI response")

                # Store refined versions — update teacher_comments and teacher_annotations
                refined_comments = refined_data.get("refined_comments")
                refined_annotations = refined_data.get("refined_annotations")

                updates: Dict[str, Optional[str]] = {}
                if refined_comments:
                    updates["teacher_comments"] = refined_comments
                if refined_annotations:
                    updates["teacher_annotations"] = json.dumps(refined_annotations)

                if updates:
                    self.job_manager.update_essay_review(essay_id, **updates)
                    refined_count += 1

                print(f"[Regrade] Refined comments for essay {essay_id}", file=sys.stderr)

            except Exception as e:
                error_msg = f"Essay {essay['id']}: {str(e)}"
                print(f"[Regrade] Error refining essay {essay['id']}: {e}", file=sys.stderr)
                errors.append(error_msg)

        print(f"[Regrade] Refinement complete. {refined_count}/{len(essays_to_refine)} essays refined.", file=sys.stderr)

        return {
            "status": "success",
            "job_id": job_id,
            "refined_count": refined_count,
            "total_essays": len(essays_to_refine),
            "errors": errors if errors else None,
        }
