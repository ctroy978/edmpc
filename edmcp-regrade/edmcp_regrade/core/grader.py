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

    def refine_comments(
        self,
        job_id: str,
        essay_ids: Optional[List[int]] = None,
        model: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Use AI to polish teacher draft comments into professional, encouraging feedback.
        Preserves teacher intent while improving clarity and tone.

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

                if essay.get("teacher_comments"):
                    parts.append(f"TEACHER OVERALL COMMENTS:\n{essay['teacher_comments']}\n")

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
