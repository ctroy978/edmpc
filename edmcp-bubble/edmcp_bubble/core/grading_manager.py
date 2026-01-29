"""
Grading Job Manager

Orchestrates the grading workflow: job creation, scan processing, grading, and reporting.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from edmcp_core.db import DatabaseManager

from .grader import BubbleSheetGrader
from .pdf_converter import count_pdf_pages, pdf_bytes_to_images
from .scanner import BubbleSheetScanner


class GradingJobError(Exception):
    """Error during grading job operations."""

    pass


class GradingJobManager:
    """Manages grading jobs for bubble tests."""

    def __init__(self, db: DatabaseManager):
        """
        Initialize the grading job manager.

        Args:
            db: Database manager instance (shared with BubbleTestManager)
        """
        self.db = db

    def create_job(self, test_id: str) -> str:
        """
        Create a new grading job for a test.

        Args:
            test_id: Test ID (must have bubble sheet and answer key)

        Returns:
            Job ID (e.g., "gj_20260125_143052_abc12345")

        Raises:
            GradingJobError: If test not found or missing required data
        """
        cursor = self.db.conn.cursor()

        # Validate test exists
        cursor.execute(
            "SELECT id FROM bubble_tests WHERE id = ?", (test_id,)
        )
        row = cursor.fetchone()
        if not row:
            raise GradingJobError(f"Test not found: {test_id}")

        # Check for bubble sheet with layout
        cursor.execute(
            "SELECT id, layout_json FROM bubble_sheets WHERE test_id = ? ORDER BY created_at DESC LIMIT 1",
            (test_id,),
        )
        sheet = cursor.fetchone()
        if not sheet or not sheet["layout_json"]:
            raise GradingJobError(
                "Test must have a bubble sheet with layout. Generate a sheet first."
            )

        # Check for answer key
        cursor.execute(
            "SELECT id FROM answer_keys WHERE test_id = ? LIMIT 1",
            (test_id,),
        )
        key = cursor.fetchone()
        if not key:
            raise GradingJobError(
                "Test must have an answer key. Set the answer key first."
            )

        # Generate job ID
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        unique_suffix = str(uuid.uuid4())[:8]
        job_id = f"gj_{timestamp}_{unique_suffix}"
        created_at = datetime.now().isoformat()

        cursor.execute(
            """
            INSERT INTO grading_jobs (id, test_id, created_at, status)
            VALUES (?, ?, ?, 'CREATED')
            """,
            (job_id, test_id, created_at),
        )
        self.db.conn.commit()

        return job_id

    def upload_scans(self, job_id: str, pdf_bytes: bytes) -> Dict[str, Any]:
        """
        Upload scanned PDF to a grading job.

        Args:
            job_id: Grading job ID
            pdf_bytes: Raw PDF content

        Returns:
            Dict with num_pages

        Raises:
            GradingJobError: If job not found or wrong status
        """
        cursor = self.db.conn.cursor()

        # Validate job
        cursor.execute(
            "SELECT status FROM grading_jobs WHERE id = ?", (job_id,)
        )
        row = cursor.fetchone()
        if not row:
            raise GradingJobError(f"Grading job not found: {job_id}")

        if row["status"] not in ("CREATED", "UPLOADED"):
            raise GradingJobError(
                f"Job must be in CREATED or UPLOADED status for upload, got: {row['status']}"
            )

        # Count pages
        try:
            num_pages = count_pdf_pages(pdf_bytes)
        except Exception as e:
            raise GradingJobError(f"Failed to read PDF: {e}")

        # Store PDF and update status
        cursor.execute(
            """
            UPDATE grading_jobs
            SET scan_pdf = ?, num_pages = ?, status = 'UPLOADED'
            WHERE id = ?
            """,
            (pdf_bytes, num_pages, job_id),
        )
        self.db.conn.commit()

        return {"num_pages": num_pages}

    def process_scans(self, job_id: str) -> Dict[str, Any]:
        """
        Process uploaded scans using computer vision.

        Args:
            job_id: Grading job ID

        Returns:
            Dict with num_students, num_errors

        Raises:
            GradingJobError: If job not ready for processing
        """
        cursor = self.db.conn.cursor()

        # Get job with PDF
        cursor.execute(
            """
            SELECT gj.*, bt.id as bt_id
            FROM grading_jobs gj
            JOIN bubble_tests bt ON gj.test_id = bt.id
            WHERE gj.id = ?
            """,
            (job_id,),
        )
        job = cursor.fetchone()
        if not job:
            raise GradingJobError(f"Grading job not found: {job_id}")

        if job["status"] not in ("UPLOADED", "SCANNING"):
            raise GradingJobError(
                f"Job must be UPLOADED for processing, got: {job['status']}"
            )

        if not job["scan_pdf"]:
            raise GradingJobError("No PDF uploaded for this job")

        # Get layout
        cursor.execute(
            """
            SELECT layout_json FROM bubble_sheets
            WHERE test_id = ?
            ORDER BY created_at DESC LIMIT 1
            """,
            (job["test_id"],),
        )
        sheet = cursor.fetchone()
        if not sheet or not sheet["layout_json"]:
            raise GradingJobError("No bubble sheet layout found for test")

        layout = json.loads(sheet["layout_json"])

        # Update status to SCANNING
        cursor.execute(
            "UPDATE grading_jobs SET status = 'SCANNING' WHERE id = ?", (job_id,)
        )
        self.db.conn.commit()

        # Clear any existing responses for re-processing
        cursor.execute("DELETE FROM student_responses WHERE job_id = ?", (job_id,))
        self.db.conn.commit()

        # Process each page
        scanner = BubbleSheetScanner(layout)
        num_students = 0
        num_errors = 0

        try:
            for page_num, image in pdf_bytes_to_images(job["scan_pdf"]):
                try:
                    result = scanner.scan_image(page_num, image)
                    scan_status = "OK" if result.student_id != "ERROR" else "ERROR"
                    if result.student_id == "ERROR":
                        num_errors += 1
                    else:
                        num_students += 1

                    cursor.execute(
                        """
                        INSERT INTO student_responses
                        (job_id, page_number, student_id, answers_json, scan_status, scan_warnings)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            job_id,
                            page_num,
                            result.student_id,
                            json.dumps(result.answers),
                            scan_status,
                            json.dumps(result.warnings) if result.warnings else None,
                        ),
                    )
                except Exception as e:
                    num_errors += 1
                    cursor.execute(
                        """
                        INSERT INTO student_responses
                        (job_id, page_number, student_id, answers_json, scan_status, scan_warnings)
                        VALUES (?, ?, 'ERROR', '{}', 'ERROR', ?)
                        """,
                        (job_id, page_num, json.dumps([str(e)])),
                    )

            # Update job status
            cursor.execute(
                """
                UPDATE grading_jobs
                SET status = 'SCANNED', num_students = ?
                WHERE id = ?
                """,
                (num_students, job_id),
            )
            self.db.conn.commit()

        except Exception as e:
            cursor.execute(
                """
                UPDATE grading_jobs
                SET status = 'ERROR', error_message = ?
                WHERE id = ?
                """,
                (str(e), job_id),
            )
            self.db.conn.commit()
            raise GradingJobError(f"Scan processing failed: {e}")

        return {"num_students": num_students, "num_errors": num_errors}

    def grade_job(self, job_id: str) -> Dict[str, Any]:
        """
        Grade all responses in a job against the answer key.

        Args:
            job_id: Grading job ID

        Returns:
            Dict with statistics (mean_score, min, max, etc.)

        Raises:
            GradingJobError: If job not ready for grading
        """
        cursor = self.db.conn.cursor()

        # Get job
        cursor.execute(
            "SELECT * FROM grading_jobs WHERE id = ?", (job_id,)
        )
        job = cursor.fetchone()
        if not job:
            raise GradingJobError(f"Grading job not found: {job_id}")

        if job["status"] not in ("SCANNED", "GRADING"):
            raise GradingJobError(
                f"Job must be SCANNED for grading, got: {job['status']}"
            )

        # Get answer key
        cursor.execute(
            """
            SELECT key_data FROM answer_keys
            WHERE test_id = ?
            ORDER BY created_at DESC LIMIT 1
            """,
            (job["test_id"],),
        )
        key_row = cursor.fetchone()
        if not key_row:
            raise GradingJobError("No answer key found for test")

        answer_key = json.loads(key_row["key_data"])

        # Update status
        cursor.execute(
            "UPDATE grading_jobs SET status = 'GRADING' WHERE id = ?", (job_id,)
        )
        self.db.conn.commit()

        # Grade each response
        grader = BubbleSheetGrader(answer_key)

        cursor.execute(
            """
            SELECT id, answers_json FROM student_responses
            WHERE job_id = ? AND scan_status = 'OK'
            """,
            (job_id,),
        )
        responses = cursor.fetchall()

        for resp in responses:
            try:
                score, percent, _ = grader.grade_response(resp["answers_json"])
                cursor.execute(
                    """
                    UPDATE student_responses
                    SET score = ?, percent_grade = ?
                    WHERE id = ?
                    """,
                    (score, percent, resp["id"]),
                )
            except Exception as e:
                # Log error but continue grading
                cursor.execute(
                    """
                    UPDATE student_responses
                    SET scan_warnings = COALESCE(scan_warnings, '[]') || ?
                    WHERE id = ?
                    """,
                    (json.dumps([f"Grading error: {e}"]), resp["id"]),
                )

        self.db.conn.commit()

        # Get graded responses for stats and CSV
        cursor.execute(
            """
            SELECT student_id, answers_json, score, percent_grade
            FROM student_responses
            WHERE job_id = ? AND scan_status = 'OK'
            """,
            (job_id,),
        )
        graded_responses = [dict(r) for r in cursor.fetchall()]

        # Generate gradebook CSV
        csv_content = grader.generate_gradebook_csv(graded_responses)
        created_at = datetime.now().isoformat()

        # Delete existing gradebook report
        cursor.execute(
            "DELETE FROM grading_reports WHERE job_id = ? AND report_type = 'gradebook'",
            (job_id,),
        )

        cursor.execute(
            """
            INSERT INTO grading_reports (job_id, report_type, filename, content, created_at)
            VALUES (?, 'gradebook', 'gradebook.csv', ?, ?)
            """,
            (job_id, csv_content, created_at),
        )

        # Update job status
        cursor.execute(
            "UPDATE grading_jobs SET status = 'COMPLETED' WHERE id = ?", (job_id,)
        )
        self.db.conn.commit()

        # Calculate stats
        stats = grader.get_stats(graded_responses)

        return stats

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        """
        Get grading job details.

        Args:
            job_id: Grading job ID

        Returns:
            Job dict or None if not found
        """
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            SELECT id, test_id, created_at, status, num_pages, num_students, error_message
            FROM grading_jobs WHERE id = ?
            """,
            (job_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None

        result = dict(row)

        # Get response counts by status
        cursor.execute(
            """
            SELECT scan_status, COUNT(*) as count
            FROM student_responses
            WHERE job_id = ?
            GROUP BY scan_status
            """,
            (job_id,),
        )
        status_counts = {r["scan_status"]: r["count"] for r in cursor.fetchall()}
        result["response_counts"] = status_counts

        return result

    def list_jobs(self, test_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        """
        List grading jobs for a test.

        Args:
            test_id: Test ID
            limit: Maximum number of jobs to return

        Returns:
            List of job dicts
        """
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            SELECT id, test_id, created_at, status, num_pages, num_students, error_message
            FROM grading_jobs
            WHERE test_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (test_id, limit),
        )

        return [dict(row) for row in cursor.fetchall()]

    def get_gradebook(self, job_id: str) -> Optional[bytes]:
        """
        Get gradebook CSV for a completed job.

        Args:
            job_id: Grading job ID

        Returns:
            CSV content as bytes, or None if not found
        """
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            SELECT content FROM grading_reports
            WHERE job_id = ? AND report_type = 'gradebook'
            ORDER BY created_at DESC LIMIT 1
            """,
            (job_id,),
        )
        row = cursor.fetchone()
        return row["content"] if row else None

    def get_responses(self, job_id: str) -> List[Dict[str, Any]]:
        """
        Get all student responses for a job.

        Args:
            job_id: Grading job ID

        Returns:
            List of response dicts
        """
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            SELECT id, page_number, student_id, answers_json, score, percent_grade,
                   scan_status, scan_warnings
            FROM student_responses
            WHERE job_id = ?
            ORDER BY page_number
            """,
            (job_id,),
        )

        responses = []
        for row in cursor.fetchall():
            resp = dict(row)
            # Parse JSON fields
            if resp["answers_json"]:
                resp["answers"] = json.loads(resp["answers_json"])
            if resp["scan_warnings"]:
                resp["warnings"] = json.loads(resp["scan_warnings"])
            responses.append(resp)

        return responses
