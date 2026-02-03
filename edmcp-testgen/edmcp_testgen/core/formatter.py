"""
Formatter - Format tests and answer keys for output.
"""

from typing import Any, Dict, List, Optional

from edmcp_testgen.core.test_job_manager import TestJobManager


class Formatter:
    """Format tests and answer keys for various output formats."""

    def __init__(self, job_manager: TestJobManager):
        self.job_manager = job_manager

    def format_test(
        self,
        job_id: str,
        organize_by: str = "type",
        include_point_values: bool = True,
    ) -> Dict[str, Any]:
        """
        Format the test for display/export.

        Args:
            job_id: The job ID
            organize_by: "type" (group by question type) or "number" (sequential)
            include_point_values: Whether to show point values

        Returns:
            Formatted test dict
        """
        job = self.job_manager.get_job(job_id)
        if not job:
            return {"status": "error", "message": f"Job not found: {job_id}"}

        questions = self.job_manager.get_job_questions(job_id)
        if not questions:
            return {"status": "error", "message": "No questions generated"}

        # Calculate totals
        total_points = sum(q.get("points", 1.0) for q in questions)

        if organize_by == "type":
            formatted = self._format_by_type(questions, include_point_values)
        else:
            formatted = self._format_sequential(questions, include_point_values)

        # Build word bank if enabled
        word_bank = None
        if job.get("include_word_bank"):
            word_bank = self._build_word_bank(questions)

        return {
            "status": "success",
            "job_id": job_id,
            "name": job.get("name", "Untitled Test"),
            "description": job.get("description"),
            "total_questions": len(questions),
            "total_points": total_points,
            "sections": formatted,
            "word_bank": word_bank,
        }

    def _format_by_type(
        self,
        questions: List[Dict[str, Any]],
        include_point_values: bool,
    ) -> List[Dict[str, Any]]:
        """Format questions grouped by type."""
        sections = []

        # Multiple Choice
        mcq = [q for q in questions if q.get("question_type") == "mcq"]
        if mcq:
            sections.append({
                "title": "Multiple Choice",
                "instructions": "Choose the best answer for each question.",
                "questions": self._format_mcq_questions(mcq, include_point_values),
            })

        # Fill in the Blank
        fib = [q for q in questions if q.get("question_type") == "fib"]
        if fib:
            sections.append({
                "title": "Fill in the Blank",
                "instructions": "Complete each sentence with the correct word or phrase.",
                "questions": self._format_fib_questions(fib, include_point_values),
            })

        # Short Answer
        sa = [q for q in questions if q.get("question_type") == "sa"]
        if sa:
            sections.append({
                "title": "Short Answer",
                "instructions": "Answer each question in complete sentences.",
                "questions": self._format_sa_questions(sa, include_point_values),
            })

        return sections

    def _format_sequential(
        self,
        questions: List[Dict[str, Any]],
        include_point_values: bool,
    ) -> List[Dict[str, Any]]:
        """Format questions in sequential order."""
        formatted_questions = []

        for q in questions:
            q_type = q.get("question_type", "unknown")
            if q_type == "mcq":
                formatted_questions.extend(self._format_mcq_questions([q], include_point_values))
            elif q_type == "fib":
                formatted_questions.extend(self._format_fib_questions([q], include_point_values))
            elif q_type == "sa":
                formatted_questions.extend(self._format_sa_questions([q], include_point_values))

        return [{
            "title": "Questions",
            "instructions": "",
            "questions": formatted_questions,
        }]

    def _format_mcq_questions(
        self,
        questions: List[Dict[str, Any]],
        include_point_values: bool,
    ) -> List[Dict[str, Any]]:
        """Format MCQ questions for display."""
        formatted = []
        for q in questions:
            item = {
                "number": q.get("question_number"),
                "type": "mcq",
                "text": q.get("question_text", ""),
                "options": q.get("options", []),
            }
            if include_point_values:
                item["points"] = q.get("points", 1.0)
            formatted.append(item)
        return formatted

    def _format_fib_questions(
        self,
        questions: List[Dict[str, Any]],
        include_point_values: bool,
    ) -> List[Dict[str, Any]]:
        """Format FIB questions for display."""
        formatted = []
        for q in questions:
            item = {
                "number": q.get("question_number"),
                "type": "fib",
                "text": q.get("question_text", ""),
            }
            if include_point_values:
                item["points"] = q.get("points", 1.0)
            formatted.append(item)
        return formatted

    def _format_sa_questions(
        self,
        questions: List[Dict[str, Any]],
        include_point_values: bool,
    ) -> List[Dict[str, Any]]:
        """Format SA questions for display."""
        formatted = []
        for q in questions:
            item = {
                "number": q.get("question_number"),
                "type": "sa",
                "text": q.get("question_text", ""),
            }
            if include_point_values:
                item["points"] = q.get("points", 1.0)
            formatted.append(item)
        return formatted

    def _build_word_bank(self, questions: List[Dict[str, Any]]) -> List[str]:
        """Build a word bank from FIB answers."""
        import random

        words = []
        for q in questions:
            if q.get("question_type") == "fib":
                answer = q.get("correct_answer", "")
                if answer:
                    words.append(answer)

        # Shuffle to not reveal order
        random.shuffle(words)
        return words

    def format_answer_key(
        self,
        job_id: str,
        include_rubrics: bool = True,
    ) -> Dict[str, Any]:
        """
        Format the complete answer key.

        Args:
            job_id: The job ID
            include_rubrics: Whether to include rubrics for SA questions

        Returns:
            Formatted answer key dict
        """
        job = self.job_manager.get_job(job_id)
        if not job:
            return {"status": "error", "message": f"Job not found: {job_id}"}

        questions = self.job_manager.get_job_questions(job_id)
        if not questions:
            return {"status": "error", "message": "No questions generated"}

        total_points = sum(q.get("points", 1.0) for q in questions)

        answers = []
        for q in questions:
            q_type = q.get("question_type", "unknown")

            answer_entry = {
                "number": q.get("question_number"),
                "type": q_type,
                "points": q.get("points", 1.0),
            }

            if q_type == "mcq":
                answer_entry["correct_answer"] = q.get("correct_answer", "")
                answer_entry["question_text"] = q.get("question_text", "")[:100] + "..."

            elif q_type == "fib":
                answer_entry["correct_answer"] = q.get("correct_answer", "")
                # Include question context
                answer_entry["question_text"] = q.get("question_text", "")

            elif q_type == "sa":
                answer_entry["model_answer"] = q.get("model_answer", "")
                answer_entry["question_text"] = q.get("question_text", "")
                if include_rubrics and q.get("rubric"):
                    answer_entry["rubric"] = q.get("rubric")

            answers.append(answer_entry)

        return {
            "status": "success",
            "job_id": job_id,
            "name": job.get("name", "Untitled Test") + " - Answer Key",
            "total_questions": len(questions),
            "total_points": total_points,
            "answers": answers,
        }

    def format_test_text(
        self,
        job_id: str,
        include_header: bool = True,
    ) -> str:
        """
        Format the test as plain text.

        Returns:
            Plain text test
        """
        test_data = self.format_test(job_id)
        if test_data.get("status") == "error":
            return f"Error: {test_data.get('message')}"

        lines = []

        if include_header:
            lines.append(f"{'=' * 60}")
            lines.append(f"  {test_data.get('name', 'Test')}")
            if test_data.get("description"):
                lines.append(f"  {test_data['description']}")
            lines.append(f"  Total Points: {test_data.get('total_points', 0)}")
            lines.append(f"{'=' * 60}")
            lines.append("")
            lines.append("Name: ________________________  Date: ____________")
            lines.append("")

        for section in test_data.get("sections", []):
            lines.append(f"\n{section.get('title', 'Questions')}")
            lines.append("-" * 40)
            if section.get("instructions"):
                lines.append(f"Instructions: {section['instructions']}")
            lines.append("")

            for q in section.get("questions", []):
                q_num = q.get("number", "?")
                points_str = f" ({q.get('points', 1)} pts)" if q.get("points") else ""
                lines.append(f"{q_num}.{points_str} {q.get('text', '')}")

                if q.get("type") == "mcq" and q.get("options"):
                    for opt in q["options"]:
                        lines.append(f"    {opt.get('letter', '?')}. {opt.get('text', '')}")

                lines.append("")

        # Word bank if present
        if test_data.get("word_bank"):
            lines.append("\nWord Bank")
            lines.append("-" * 40)
            lines.append(", ".join(test_data["word_bank"]))
            lines.append("")

        return "\n".join(lines)

    def format_answer_key_text(
        self,
        job_id: str,
    ) -> str:
        """
        Format the answer key as plain text.

        Returns:
            Plain text answer key
        """
        key_data = self.format_answer_key(job_id)
        if key_data.get("status") == "error":
            return f"Error: {key_data.get('message')}"

        lines = []
        lines.append(f"{'=' * 60}")
        lines.append(f"  {key_data.get('name', 'Answer Key')}")
        lines.append(f"  Total Points: {key_data.get('total_points', 0)}")
        lines.append(f"{'=' * 60}")
        lines.append("")

        for answer in key_data.get("answers", []):
            q_num = answer.get("number", "?")
            q_type = answer.get("type", "unknown")
            points = answer.get("points", 1)

            if q_type == "mcq":
                lines.append(f"{q_num}. {answer.get('correct_answer', '?')} ({points} pts)")

            elif q_type == "fib":
                lines.append(f"{q_num}. {answer.get('correct_answer', '?')} ({points} pts)")

            elif q_type == "sa":
                lines.append(f"{q_num}. ({points} pts)")
                lines.append(f"   Model Answer: {answer.get('model_answer', '')}")
                if answer.get("rubric"):
                    rubric = answer["rubric"]
                    lines.append(f"   Rubric ({rubric.get('total_points', points)} pts):")
                    for criterion in rubric.get("criteria", []):
                        lines.append(f"     - {criterion.get('name')}: {criterion.get('points')} pts")
                        if criterion.get("full_credit"):
                            lines.append(f"       Full: {criterion['full_credit']}")

            lines.append("")

        return "\n".join(lines)

    def get_mcq_for_bubble(self, job_id: str) -> Dict[str, Any]:
        """
        Extract MCQ questions in format suitable for bubble sheet creation.

        Returns:
            Dict with MCQ data for bubble sheet integration
        """
        job = self.job_manager.get_job(job_id)
        if not job:
            return {"status": "error", "message": f"Job not found: {job_id}"}

        questions = self.job_manager.get_job_questions(job_id)
        mcq_questions = [q for q in questions if q.get("question_type") == "mcq"]

        if not mcq_questions:
            return {"status": "error", "message": "No MCQ questions in this test"}

        # Format for bubble sheet
        bubble_answers = []
        for q in mcq_questions:
            bubble_answers.append({
                "question": f"Q{q.get('question_number')}",
                "answer": q.get("correct_answer", "A"),
                "points": q.get("points", 1.0),
            })

        return {
            "status": "success",
            "job_id": job_id,
            "num_questions": len(mcq_questions),
            "answers": bubble_answers,
        }
