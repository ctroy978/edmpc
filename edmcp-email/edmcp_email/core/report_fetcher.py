from typing import Dict, List, Optional, Tuple
from edmcp_core.db import DatabaseManager


class ReportFetcher:
    """
    Fetches report content from the database for email attachment.
    Abstracts DB access to keep Emailer testable.
    """

    EXTENSION_MAP = {
        "student_html": ".html",
        "student_pdf": ".pdf",
    }

    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager

    def fetch_for_student(
        self,
        job_id: str,
        report_type: str,
        essay_id: Optional[int],
        student_name: str,
    ) -> Optional[Tuple[bytes, str]]:
        """
        Fetches report content for a specific student.

        Args:
            job_id: The job ID
            report_type: Type of report (e.g., 'student_html', 'student_pdf')
            essay_id: The essay ID for per-student reports (None for job-level reports)
            student_name: Student's full name (used for filename generation)

        Returns:
            Tuple of (content_bytes, suggested_filename) or None if not found
        """
        cursor = self.db.conn.cursor()

        if essay_id is not None:
            cursor.execute(
                """
                SELECT content, filename FROM reports
                WHERE job_id = ? AND report_type = ? AND essay_id = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (job_id, report_type, essay_id),
            )
        else:
            cursor.execute(
                """
                SELECT content, filename FROM reports
                WHERE job_id = ? AND report_type = ? AND essay_id IS NULL
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (job_id, report_type),
            )

        row = cursor.fetchone()
        if not row or not row["content"]:
            return None

        content = row["content"]
        # Use stored filename if available, otherwise generate one
        if row["filename"]:
            filename = row["filename"]
        else:
            ext = self.EXTENSION_MAP.get(report_type, ".bin")
            safe_name = student_name.replace(" ", "_")
            filename = f"{safe_name}_feedback{ext}"

        return content, filename

    def list_available_reports(self, job_id: str) -> List[Dict]:
        """
        Returns report metadata (no content) for discovery.

        Args:
            job_id: The job ID to query

        Returns:
            List of dicts with report metadata grouped by type
        """
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            SELECT r.id, r.report_type, r.essay_id, r.filename, r.created_at,
                   e.student_name
            FROM reports r
            LEFT JOIN essays e ON r.essay_id = e.id
            WHERE r.job_id = ?
            ORDER BY r.report_type, e.student_name
            """,
            (job_id,),
        )
        rows = cursor.fetchall()
        return [
            {
                "report_id": row["id"],
                "report_type": row["report_type"],
                "essay_id": row["essay_id"],
                "filename": row["filename"],
                "student_name": row["student_name"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]
