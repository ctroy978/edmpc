"""
Bubble Sheet Grader

Applies Canvas-style scoring to bubble sheet scan results.
Adapted from bubblexan/grade.py for integration with edmcp-bubble.
"""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple


@dataclass(frozen=True)
class QuestionSpec:
    """Specification for a single question's grading."""

    question_id: str
    correct_options: Set[str]
    points: float

    @property
    def is_multiple(self) -> bool:
        """Whether this is a multiple-select question."""
        return len(self.correct_options) > 1

    @property
    def num_correct(self) -> int:
        """Number of correct options."""
        return len(self.correct_options)


def _tokenize_answers(value: str) -> List[str]:
    """Normalize answer string to list of lowercase tokens."""
    if not value:
        return []
    text = str(value).strip()
    if not text:
        return []
    tokens = [part.strip().lower() for part in text.split(",") if part.strip()]
    return tokens


def _score_multiple_select(
    total_points: float,
    correct_options: int,
    selected_correct: int,
    selected_incorrect: int,
) -> float:
    """
    Score a multiple-select response using Canvas formula.

    Formula: score = max(0, (hits - incorrect) * points_per_option)
    """
    if total_points < 0:
        raise ValueError("total_points cannot be negative.")
    if correct_options <= 0:
        return 0.0
    if selected_correct < 0 or selected_incorrect < 0:
        raise ValueError("Selection counts cannot be negative.")
    if selected_correct > correct_options:
        raise ValueError("selected_correct cannot exceed number of correct options.")

    point_per_option = total_points / correct_options
    raw_score = (selected_correct - selected_incorrect) * point_per_option
    score = max(0.0, raw_score)
    return round(score + 1e-12, 2)


class BubbleSheetGrader:
    """Grader for applying answer keys to scan results."""

    def __init__(self, answer_key: List[Dict[str, Any]]):
        """
        Initialize grader with answer key.

        Args:
            answer_key: List of answer dicts from answer_keys.key_data
                        Each dict has: question, answer, points
        """
        self.question_specs: Dict[str, QuestionSpec] = {}
        self.question_order: List[str] = []
        self.total_points = 0.0

        for item in answer_key:
            question = item["question"]
            # Normalize question ID (e.g., "Q1" -> "Q01" or vice versa)
            question_id = question.upper()
            answer_str = item["answer"]
            points = float(item.get("points", 1.0))

            tokens = _tokenize_answers(answer_str)
            if not tokens:
                raise ValueError(f"Question '{question_id}' has no valid correct answers.")

            spec = QuestionSpec(
                question_id=question_id,
                correct_options=set(tokens),
                points=points,
            )
            self.question_specs[question_id] = spec
            self.question_order.append(question_id)
            self.total_points += points

    def grade_response(
        self, answers_json: str
    ) -> Tuple[float, float, Dict[str, float]]:
        """
        Grade a student's responses.

        Args:
            answers_json: JSON string of answers dict (question_num -> answer_str)

        Returns:
            Tuple of (total_score, percent_grade, per_question_scores)
        """
        answers = json.loads(answers_json)
        per_question: Dict[str, float] = {}
        total_score = 0.0

        for question_id, spec in self.question_specs.items():
            # Find matching answer - handle Q1 vs Q01 vs 1 formats
            student_answer = None
            for key, value in answers.items():
                # Normalize the key for comparison
                normalized_key = str(key).upper()
                if not normalized_key.startswith("Q"):
                    normalized_key = f"Q{normalized_key}"
                # Also try matching just the number part
                if normalized_key == question_id:
                    student_answer = value
                    break
                # Try numeric comparison
                try:
                    key_num = int(str(key).lstrip("Qq"))
                    qid_num = int(question_id.lstrip("Qq"))
                    if key_num == qid_num:
                        student_answer = value
                        break
                except ValueError:
                    pass

            if student_answer is None:
                student_answer = ""

            selected_tokens = set(_tokenize_answers(str(student_answer)))

            if not spec.is_multiple:
                # Single-select: exact match required
                score = spec.points if selected_tokens == spec.correct_options else 0.0
            else:
                # Multiple-select: partial credit
                hits = len(selected_tokens & spec.correct_options)
                extras = len(selected_tokens - spec.correct_options)
                score = _score_multiple_select(spec.points, spec.num_correct, hits, extras)

            per_question[question_id] = round(score, 2)
            total_score += score

        total_score = round(total_score, 2)
        percent_grade = round((total_score / self.total_points) * 100, 2) if self.total_points > 0 else 0.0

        return total_score, percent_grade, per_question

    def generate_gradebook_csv(self, responses: List[Dict[str, Any]]) -> bytes:
        """
        Generate gradebook CSV from graded responses.

        Args:
            responses: List of student response dicts with:
                       student_id, answers_json, score, percent_grade

        Returns:
            CSV content as bytes
        """
        output = io.StringIO()
        writer = csv.writer(output)

        # Build header
        question_cols = [f"Q{q.lstrip('Q')}" for q in self.question_order]
        header = ["Student_ID"] + question_cols + ["Total_Score", "Total_Possible", "Percent_Grade"]
        writer.writerow(header)

        # Write rows
        for resp in responses:
            student_id = resp.get("student_id", "UNKNOWN")
            answers = json.loads(resp.get("answers_json", "{}"))
            score = resp.get("score", 0.0)
            percent = resp.get("percent_grade", 0.0)

            row = [student_id]
            for q_id in self.question_order:
                # Find answer for this question
                answer = ""
                for key, value in answers.items():
                    try:
                        key_num = int(str(key).lstrip("Qq"))
                        qid_num = int(q_id.lstrip("Qq"))
                        if key_num == qid_num:
                            answer = str(value).upper() if value else ""
                            break
                    except ValueError:
                        if str(key).upper() == q_id:
                            answer = str(value).upper() if value else ""
                            break
                row.append(answer)

            row.extend([score, self.total_points, percent])
            writer.writerow(row)

        return output.getvalue().encode("utf-8")

    def get_stats(self, responses: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Calculate grading statistics.

        Args:
            responses: List of graded response dicts

        Returns:
            Dict with mean, min, max, std scores
        """
        if not responses:
            return {
                "mean_score": 0.0,
                "min_score": 0.0,
                "max_score": 0.0,
                "mean_percent": 0.0,
            }

        scores = [r.get("score", 0.0) for r in responses]
        percents = [r.get("percent_grade", 0.0) for r in responses]

        return {
            "mean_score": round(sum(scores) / len(scores), 2),
            "min_score": round(min(scores), 2),
            "max_score": round(max(scores), 2),
            "mean_percent": round(sum(percents) / len(percents), 2),
        }
