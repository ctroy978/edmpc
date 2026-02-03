"""
Question Generator - AI-powered question generation using xAI/Grok.
"""

import os
import sys
from typing import Any, Dict, List, Optional

from openai import (
    APITimeoutError,
    APIConnectionError,
    RateLimitError,
    InternalServerError,
)

from edmcp_core import (
    KnowledgeBaseManager,
    retry_with_backoff,
    extract_json_from_text,
    get_openai_client,
)

from edmcp_testgen.core.test_job_manager import TestJobManager
from edmcp_testgen.core.prompts import (
    SYSTEM_PROMPT,
    get_material_analysis_prompt,
    get_mcq_generation_prompt,
    get_fib_generation_prompt,
    get_sa_generation_prompt,
    get_question_regeneration_prompt,
    get_point_distribution_prompt,
)


AI_RETRIABLE_EXCEPTIONS = (
    APITimeoutError,
    APIConnectionError,
    RateLimitError,
    InternalServerError,
)


class QuestionGenerator:
    """AI-powered question generation using xAI/Grok."""

    def __init__(
        self,
        job_manager: TestJobManager,
        kb_manager: KnowledgeBaseManager,
    ):
        self.job_manager = job_manager
        self.kb_manager = kb_manager
        self._client = None

    def _get_client(self):
        """Get or create the AI client."""
        if self._client is None:
            self._client = get_openai_client(
                api_key=os.environ.get("XAI_API_KEY"),
                base_url=os.environ.get("XAI_BASE_URL"),
            )
        return self._client

    def _get_model(self) -> str:
        """Get the model to use for generation."""
        return os.environ.get("XAI_API_MODEL", "grok-3")

    @retry_with_backoff(retries=3, exceptions=AI_RETRIABLE_EXCEPTIONS)
    def _call_ai(self, prompt: str, max_tokens: int = 4000) -> str:
        """Call the AI model with retry logic."""
        client = self._get_client()
        model = self._get_model()

        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            max_tokens=max_tokens,
            temperature=0.7,
        )

        return response.choices[0].message.content.strip()

    def _retrieve_context(
        self,
        job_id: str,
        query: Optional[str] = None,
        top_k: int = 10,
    ) -> str:
        """Retrieve context from the knowledge base for a job.

        Uses vector similarity search to retrieve only the most relevant
        chunks from the ingested materials. This controls token usage by
        sending ~5,000 tokens of context instead of entire documents.
        """
        job = self.job_manager.get_job(job_id)
        if not job:
            raise ValueError(f"Job not found: {job_id}")

        topic = job.get("knowledge_topic")
        if not topic:
            raise ValueError(f"No knowledge topic set for job: {job_id}")

        # Use focus topics for query if available
        if query is None:
            focus_topics = job.get("focus_topics", [])
            if focus_topics:
                query = " ".join(focus_topics)
            else:
                query = "main concepts key information definitions facts examples"

        chunks = self.kb_manager.retrieve_context_chunks(query, topic, similarity_top_k=top_k)
        return "\n\n---\n\n".join(chunks)

    def _retrieve_context_for_type(
        self,
        job_id: str,
        question_type: str,
        top_k: int = 8,
    ) -> str:
        """Retrieve context optimized for a specific question type.

        Different question types benefit from different retrieval queries:
        - MCQ: facts, definitions, specific details
        - FIB: vocabulary, key terms, definitions
        - SA: concepts, relationships, processes, analysis
        """
        type_queries = {
            "mcq": "specific facts details definitions dates names important information",
            "fib": "key vocabulary terms definitions concepts terminology",
            "sa": "main ideas relationships cause effect processes analysis themes",
        }
        query = type_queries.get(question_type, "main concepts and key information")
        return self._retrieve_context(job_id, query=query, top_k=top_k)

    def generate_all_questions(self, job_id: str) -> Dict[str, Any]:
        """
        Generate all questions for a job based on its specifications.

        Returns:
            Dict with status, questions generated, and any warnings
        """
        job = self.job_manager.get_job(job_id)
        if not job:
            return {"status": "error", "message": f"Job not found: {job_id}"}

        if not job.get("knowledge_topic"):
            return {"status": "error", "message": "No materials added to job. Use add_materials_to_job first."}

        # Update status
        self.job_manager.update_status(job_id, "GENERATING")

        # Get specifications
        distribution = job.get("question_distribution", {"mcq": 10, "fib": 5, "sa": 5})
        difficulty = job.get("difficulty", "medium")
        grade_level = job.get("grade_level")
        focus_topics = job.get("focus_topics")
        total_points = job.get("total_points", 100)
        include_rubrics = job.get("include_rubrics", True)

        # Calculate point distribution
        points_per_type = get_point_distribution_prompt(distribution, total_points)

        # Clear any existing questions
        self.job_manager.clear_job_questions(job_id)

        generated_questions = []
        errors = []
        question_number = 1

        # Generate MCQ questions with MCQ-optimized context
        mcq_count = distribution.get("mcq", 0)
        if mcq_count > 0:
            print(f"[TestGen] Generating {mcq_count} MCQ questions...", file=sys.stderr)
            try:
                # Retrieve context optimized for MCQ (facts, definitions, details)
                mcq_context = self._retrieve_context_for_type(job_id, "mcq", top_k=8)
                print(f"[TestGen] Retrieved {len(mcq_context)} chars of MCQ context", file=sys.stderr)

                mcq_questions = self._generate_mcq(
                    context=mcq_context,
                    count=mcq_count,
                    difficulty=difficulty,
                    grade_level=grade_level,
                    points=points_per_type["mcq"],
                )
                for q in mcq_questions:
                    q["question_number"] = question_number
                    q_id = self.job_manager.store_question(
                        job_id=job_id,
                        question_number=question_number,
                        question_type="mcq",
                        question_text=q["question_text"],
                        correct_answer=q["correct_answer"],
                        points=q.get("points", points_per_type["mcq"]),
                        difficulty=q.get("difficulty", difficulty),
                        options=q.get("options"),
                        distractors_rationale=q.get("distractors_rationale"),
                        source_reference=q.get("source_reference"),
                    )
                    q["id"] = q_id
                    generated_questions.append(q)
                    question_number += 1
            except Exception as e:
                errors.append(f"MCQ generation error: {e}")
                print(f"[TestGen] MCQ error: {e}", file=sys.stderr)

        # Generate FIB questions with FIB-optimized context
        fib_count = distribution.get("fib", 0)
        if fib_count > 0:
            print(f"[TestGen] Generating {fib_count} FIB questions...", file=sys.stderr)
            try:
                # Retrieve context optimized for FIB (vocabulary, key terms)
                fib_context = self._retrieve_context_for_type(job_id, "fib", top_k=8)
                print(f"[TestGen] Retrieved {len(fib_context)} chars of FIB context", file=sys.stderr)

                existing = [q["question_text"] for q in generated_questions]
                fib_questions = self._generate_fib(
                    context=fib_context,
                    count=fib_count,
                    difficulty=difficulty,
                    grade_level=grade_level,
                    points=points_per_type["fib"],
                    existing_questions=existing,
                )
                for q in fib_questions:
                    q["question_number"] = question_number
                    q_id = self.job_manager.store_question(
                        job_id=job_id,
                        question_number=question_number,
                        question_type="fib",
                        question_text=q["question_text"],
                        correct_answer=q["correct_answer"],
                        points=q.get("points", points_per_type["fib"]),
                        difficulty=q.get("difficulty", difficulty),
                        source_reference=q.get("source_reference"),
                    )
                    q["id"] = q_id
                    generated_questions.append(q)
                    question_number += 1
            except Exception as e:
                errors.append(f"FIB generation error: {e}")
                print(f"[TestGen] FIB error: {e}", file=sys.stderr)

        # Generate SA questions with SA-optimized context
        sa_count = distribution.get("sa", 0)
        if sa_count > 0:
            print(f"[TestGen] Generating {sa_count} SA questions...", file=sys.stderr)
            try:
                # Retrieve context optimized for SA (concepts, relationships, analysis)
                sa_context = self._retrieve_context_for_type(job_id, "sa", top_k=10)
                print(f"[TestGen] Retrieved {len(sa_context)} chars of SA context", file=sys.stderr)

                existing = [q["question_text"] for q in generated_questions]
                sa_questions = self._generate_sa(
                    context=sa_context,
                    count=sa_count,
                    difficulty=difficulty,
                    grade_level=grade_level,
                    points=points_per_type["sa"],
                    include_rubrics=include_rubrics,
                    existing_questions=existing,
                )
                for q in sa_questions:
                    q["question_number"] = question_number
                    q_id = self.job_manager.store_question(
                        job_id=job_id,
                        question_number=question_number,
                        question_type="sa",
                        question_text=q["question_text"],
                        correct_answer=q.get("model_answer", ""),
                        points=q.get("points", points_per_type["sa"]),
                        difficulty=q.get("difficulty", difficulty),
                        model_answer=q.get("model_answer"),
                        rubric=q.get("rubric"),
                        source_reference=q.get("source_reference"),
                    )
                    q["id"] = q_id
                    generated_questions.append(q)
                    question_number += 1
            except Exception as e:
                errors.append(f"SA generation error: {e}")
                print(f"[TestGen] SA error: {e}", file=sys.stderr)

        # Update status
        if generated_questions:
            self.job_manager.update_status(job_id, "COMPLETE")
        else:
            self.job_manager.update_status(job_id, "CREATED")

        print(f"[TestGen] Generated {len(generated_questions)} questions for job {job_id}", file=sys.stderr)

        return {
            "status": "success" if not errors else "partial",
            "job_id": job_id,
            "questions_generated": len(generated_questions),
            "mcq_count": len([q for q in generated_questions if q.get("question_type") == "mcq"]),
            "fib_count": len([q for q in generated_questions if q.get("question_type") == "fib"]),
            "sa_count": len([q for q in generated_questions if q.get("question_type") == "sa"]),
            "errors": errors if errors else None,
        }

    def _generate_mcq(
        self,
        context: str,
        count: int,
        difficulty: str,
        grade_level: Optional[str],
        points: float,
    ) -> List[Dict[str, Any]]:
        """Generate multiple choice questions."""
        prompt = get_mcq_generation_prompt(
            context=context,
            count=count,
            difficulty=difficulty,
            grade_level=grade_level,
        )

        response = self._call_ai(prompt, max_tokens=6000)
        questions = extract_json_from_text(response)

        if not questions or not isinstance(questions, list):
            raise ValueError("Failed to parse MCQ response as JSON array")

        # Add metadata
        for q in questions:
            q["question_type"] = "mcq"
            q["points"] = points

        return questions

    def _generate_fib(
        self,
        context: str,
        count: int,
        difficulty: str,
        grade_level: Optional[str],
        points: float,
        existing_questions: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Generate fill-in-the-blank questions."""
        prompt = get_fib_generation_prompt(
            context=context,
            count=count,
            difficulty=difficulty,
            grade_level=grade_level,
            existing_questions=existing_questions,
        )

        response = self._call_ai(prompt, max_tokens=4000)
        questions = extract_json_from_text(response)

        if not questions or not isinstance(questions, list):
            raise ValueError("Failed to parse FIB response as JSON array")

        # Add metadata
        for q in questions:
            q["question_type"] = "fib"
            q["points"] = points

        return questions

    def _generate_sa(
        self,
        context: str,
        count: int,
        difficulty: str,
        grade_level: Optional[str],
        points: float,
        include_rubrics: bool,
        existing_questions: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Generate short answer questions."""
        prompt = get_sa_generation_prompt(
            context=context,
            count=count,
            difficulty=difficulty,
            grade_level=grade_level,
            include_rubrics=include_rubrics,
            existing_questions=existing_questions,
        )

        response = self._call_ai(prompt, max_tokens=6000)
        questions = extract_json_from_text(response)

        if not questions or not isinstance(questions, list):
            raise ValueError("Failed to parse SA response as JSON array")

        # Add metadata
        for q in questions:
            q["question_type"] = "sa"
            if "points" not in q:
                q["points"] = points

        return questions

    def regenerate_question(
        self,
        job_id: str,
        question_id: int,
        reason: Optional[str] = None,
        difficulty: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Regenerate a specific question.

        Args:
            job_id: The job ID
            question_id: The question ID to regenerate
            reason: Optional reason for regeneration
            difficulty: Optional new difficulty level

        Returns:
            Dict with the new question or error
        """
        question = self.job_manager.get_question(question_id)
        if not question:
            return {"status": "error", "message": f"Question not found: {question_id}"}

        if question["job_id"] != job_id:
            return {"status": "error", "message": "Question does not belong to this job"}

        try:
            context = self._retrieve_context(job_id)
        except Exception as e:
            return {"status": "error", "message": f"Failed to retrieve context: {e}"}

        prompt = get_question_regeneration_prompt(
            original_question=question,
            context=context,
            reason=reason,
            difficulty=difficulty,
        )

        try:
            response = self._call_ai(prompt, max_tokens=2000)
            new_question = extract_json_from_text(response)

            if not new_question:
                raise ValueError("Failed to parse regeneration response")

            # Handle both single object and array response
            if isinstance(new_question, list):
                new_question = new_question[0]

            # Update the question in the database
            self.job_manager.update_question(
                question_id=question_id,
                question_text=new_question.get("question_text"),
                correct_answer=new_question.get("correct_answer", new_question.get("model_answer")),
                options=new_question.get("options"),
                model_answer=new_question.get("model_answer"),
                rubric=new_question.get("rubric"),
                difficulty=difficulty or new_question.get("difficulty"),
                status="REGENERATED",
            )
            self.job_manager.increment_regeneration_count(question_id)

            return {
                "status": "success",
                "question_id": question_id,
                "new_question": new_question,
                "message": "Question regenerated successfully",
            }

        except Exception as e:
            return {"status": "error", "message": f"Regeneration failed: {e}"}

    def validate_coverage(self, job_id: str) -> Dict[str, Any]:
        """
        Validate that questions provide good coverage of the material.

        Returns:
            Dict with coverage analysis
        """
        job = self.job_manager.get_job(job_id)
        if not job:
            return {"status": "error", "message": f"Job not found: {job_id}"}

        questions = self.job_manager.get_job_questions(job_id)
        if not questions:
            return {"status": "warning", "message": "No questions generated yet"}

        # Analyze source references
        source_refs = [q.get("source_reference", "") for q in questions if q.get("source_reference")]

        # Count by difficulty
        difficulty_counts = {}
        for q in questions:
            d = q.get("difficulty", "medium")
            difficulty_counts[d] = difficulty_counts.get(d, 0) + 1

        # Count by type
        type_counts = {}
        for q in questions:
            t = q.get("question_type", "unknown")
            type_counts[t] = type_counts.get(t, 0) + 1

        return {
            "status": "success",
            "job_id": job_id,
            "total_questions": len(questions),
            "by_type": type_counts,
            "by_difficulty": difficulty_counts,
            "source_references": len(source_refs),
            "coverage_score": min(100, len(source_refs) / max(1, len(questions)) * 100),
        }
