import sqlite3
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any, Union


class DatabaseManager:
    """
    Manages the SQLite database for the OCR-MCP pipeline.
    Handles storage of jobs, essays, and their states.
    """

    def __init__(self, db_path: Union[str, Path] = "edmcp.db"):
        self.db_path = str(db_path)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row  # Return rows as dict-like objects
        self._create_tables()

    def _create_tables(self):
        """Creates the necessary tables if they don't exist."""
        cursor = self.conn.cursor()

        # Jobs Table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'PENDING',
                name TEXT
            )
        """)

        # Essays Table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS essays (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL,
                student_name TEXT,
                raw_text TEXT,
                scrubbed_text TEXT,
                normalized_text TEXT,
                evaluation TEXT,
                grade TEXT,
                status TEXT NOT NULL DEFAULT 'PENDING',
                metadata TEXT,
                FOREIGN KEY (job_id) REFERENCES jobs (id)
            )
        """)

        # Reports Table (stores generated PDFs and other report files)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL,
                report_type TEXT NOT NULL,
                essay_id INTEGER,
                filename TEXT,
                content BLOB,
                created_at TEXT NOT NULL,
                FOREIGN KEY (job_id) REFERENCES jobs (id),
                FOREIGN KEY (essay_id) REFERENCES essays (id)
            )
        """)

        self.conn.commit()
        self._migrate_schema()

    def _migrate_schema(self):
        """Adds missing columns to existing tables."""
        cursor = self.conn.cursor()

        # Check for columns in essays
        cursor.execute("PRAGMA table_info(essays)")
        essays_columns = {row[1] for row in cursor.fetchall()}

        if "normalized_text" not in essays_columns:
            cursor.execute("ALTER TABLE essays ADD COLUMN normalized_text TEXT")
        if "evaluation" not in essays_columns:
            cursor.execute("ALTER TABLE essays ADD COLUMN evaluation TEXT")
        if "grade" not in essays_columns:
            cursor.execute("ALTER TABLE essays ADD COLUMN grade TEXT")

        # Check for columns in jobs
        cursor.execute("PRAGMA table_info(jobs)")
        jobs_columns = {row[1] for row in cursor.fetchall()}

        if "name" not in jobs_columns:
            cursor.execute("ALTER TABLE jobs ADD COLUMN name TEXT")
        if "rubric" not in jobs_columns:
            cursor.execute("ALTER TABLE jobs ADD COLUMN rubric TEXT")
        if "question_text" not in jobs_columns:
            cursor.execute("ALTER TABLE jobs ADD COLUMN question_text TEXT")
        if "essay_format" not in jobs_columns:
            cursor.execute("ALTER TABLE jobs ADD COLUMN essay_format TEXT")
        if "student_count" not in jobs_columns:
            cursor.execute("ALTER TABLE jobs ADD COLUMN student_count INTEGER")
        if "knowledge_base_topic" not in jobs_columns:
            cursor.execute("ALTER TABLE jobs ADD COLUMN knowledge_base_topic TEXT")

        self.conn.commit()

    def create_job(
        self,
        job_name: Optional[str] = None,
        rubric: Optional[str] = None,
        question_text: Optional[str] = None,
        essay_format: Optional[str] = None,
        student_count: Optional[int] = None,
        knowledge_base_topic: Optional[str] = None,
    ) -> str:
        """Creates a new job and returns its ID."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        unique_suffix = str(uuid.uuid4())[:8]
        job_id = f"job_{timestamp}_{unique_suffix}"

        created_at = datetime.now().isoformat()

        cursor = self.conn.cursor()
        cursor.execute(
            """INSERT INTO jobs
               (id, created_at, name, rubric, question_text, essay_format, student_count, knowledge_base_topic)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (job_id, created_at, job_name, rubric, question_text, essay_format, student_count, knowledge_base_topic),
        )
        self.conn.commit()
        return job_id

    def add_essay(
        self,
        job_id: str,
        student_name: Optional[str],
        raw_text: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> int:
        """Adds a new essay record to a job."""
        metadata_json = json.dumps(metadata) if metadata else None

        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT INTO essays (job_id, student_name, raw_text, metadata)
            VALUES (?, ?, ?, ?)
            """,
            (job_id, student_name, raw_text, metadata_json),
        )
        self.conn.commit()
        # lastrowid is guaranteed to be int for successful INSERT with AUTOINCREMENT
        essay_id = cursor.lastrowid
        assert essay_id is not None, "Failed to get essay ID after insert"
        return essay_id

    def update_essay_scrubbed(self, essay_id: int, scrubbed_text: str):
        """Updates an essay with scrubbed text and sets status to SCRUBBED."""
        cursor = self.conn.cursor()
        cursor.execute(
            """
            UPDATE essays 
            SET scrubbed_text = ?, status = 'SCRUBBED' 
            WHERE id = ?
            """,
            (scrubbed_text, essay_id),
        )
        self.conn.commit()

    def update_essay_normalized(self, essay_id: int, normalized_text: str):
        """Updates an essay with normalized text and sets status to NORMALIZED."""
        cursor = self.conn.cursor()
        cursor.execute(
            """
            UPDATE essays 
            SET normalized_text = ?, status = 'NORMALIZED' 
            WHERE id = ?
            """,
            (normalized_text, essay_id),
        )
        self.conn.commit()

    def update_essay_evaluation(
        self, essay_id: int, evaluation_json: str, grade: Optional[str] = None
    ):
        """Updates an essay with evaluation results and sets status to GRADED."""
        cursor = self.conn.cursor()
        cursor.execute(
            """
            UPDATE essays 
            SET evaluation = ?, grade = ?, status = 'GRADED' 
            WHERE id = ?
            """,
            (evaluation_json, grade, essay_id),
        )
        self.conn.commit()

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves job information by job_id."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
        row = cursor.fetchone()

        if row:
            return dict(row)
        return None

    def get_job_essays(self, job_id: str) -> List[Dict[str, Any]]:
        """Retrieves all essays for a specific job."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM essays WHERE job_id = ?", (job_id,))
        rows = cursor.fetchall()

        results = []
        for row in rows:
            item = dict(row)
            if item["metadata"]:
                try:
                    item["metadata"] = json.loads(item["metadata"])
                except json.JSONDecodeError:
                    pass
            results.append(item)
        return results

    def delete_job(self, job_id: str) -> bool:
        """
        Deletes a job and all its associated essays and reports from the database.
        Returns True if the job existed and was deleted, False otherwise.
        """
        cursor = self.conn.cursor()

        # Check if job exists
        cursor.execute("SELECT 1 FROM jobs WHERE id = ?", (job_id,))
        if not cursor.fetchone():
            return False

        # Delete reports (manual cascade since foreign_keys pragma might be off)
        cursor.execute("DELETE FROM reports WHERE job_id = ?", (job_id,))

        # Delete essays (manual cascade since foreign_keys pragma might be off)
        cursor.execute("DELETE FROM essays WHERE job_id = ?", (job_id,))

        # Delete job
        cursor.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        self.conn.commit()
        return True

    def get_old_jobs(self, cutoff_date: datetime) -> List[str]:
        """
        Retrieves IDs of jobs created before the cutoff_date.
        """
        cutoff_iso = cutoff_date.isoformat()
        cursor = self.conn.cursor()

        cursor.execute("SELECT id FROM jobs WHERE created_at < ?", (cutoff_iso,))
        rows = cursor.fetchall()

        return [row["id"] for row in rows]

    def search_jobs(
        self,
        query: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Searches for jobs matching a keyword in student names or content.
        """
        sql = """
            SELECT DISTINCT 
                j.id, j.created_at, j.name, j.status,
                e.student_name, e.raw_text
            FROM jobs j
            JOIN essays e ON j.id = e.job_id
            WHERE (
                e.student_name LIKE ? OR 
                e.raw_text LIKE ? OR
                j.name LIKE ?
            )
        """
        params = [f"%{query}%", f"%{query}%", f"%{query}%"]

        if start_date:
            sql += " AND j.created_at >= ?"
            params.append(start_date)
        if end_date:
            sql += " AND j.created_at <= ?"
            params.append(end_date)

        sql += " ORDER BY j.created_at DESC"

        cursor = self.conn.cursor()
        cursor.execute(sql, params)
        rows = cursor.fetchall()

        # Aggregate results by job to avoid duplicates
        jobs = {}
        for row in rows:
            job_id = row["id"]
            if job_id not in jobs:
                jobs[job_id] = {
                    "id": job_id,
                    "created_at": row["created_at"],
                    "name": row["name"],
                    "status": row["status"],
                    "matches": [],
                }

            # Add snippet match info
            snippet = ""
            reason = ""
            if query.lower() in (row["student_name"] or "").lower():
                reason = "Student Name Match"
                snippet = row["student_name"]
            elif query.lower() in (row["name"] or "").lower():
                reason = "Job Name Match"
                snippet = row["name"]
            else:
                reason = "Content Match"
                # Find the snippet
                text = row["raw_text"] or ""
                idx = text.lower().find(query.lower())
                start = max(0, idx - 30)
                end = min(len(text), idx + len(query) + 30)
                snippet = "..." + text[start:end] + "..."

            # Only add limited matches to keep payload small
            if len(jobs[job_id]["matches"]) < 3:
                jobs[job_id]["matches"].append({"reason": reason, "snippet": snippet})

        return list(jobs.values())

    def store_report(
        self,
        job_id: str,
        report_type: str,
        filename: str,
        content: bytes,
        essay_id: Optional[int] = None,
    ) -> int:
        """
        Stores a generated report (PDF, CSV, etc.) in the database.

        Args:
            job_id: The job this report belongs to
            report_type: Type of report (e.g., 'student_pdf', 'gradebook_csv')
            filename: Original filename
            content: Binary content of the file
            essay_id: Optional essay ID for per-student reports

        Returns:
            The report ID
        """
        created_at = datetime.now().isoformat()
        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT INTO reports (job_id, report_type, essay_id, filename, content, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (job_id, report_type, essay_id, filename, content, created_at),
        )
        self.conn.commit()
        report_id = cursor.lastrowid
        assert report_id is not None, "Failed to get report ID after insert"
        return report_id

    def get_student_pdf(self, essay_id: int) -> Optional[bytes]:
        """
        Retrieves the PDF report for a specific essay.

        Args:
            essay_id: The essay ID

        Returns:
            PDF content as bytes, or None if not found
        """
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT content FROM reports
            WHERE essay_id = ? AND report_type = 'student_pdf'
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (essay_id,),
        )
        row = cursor.fetchone()
        return row["content"] if row else None

    def get_report(
        self, job_id: str, report_type: str, essay_id: Optional[int] = None
    ) -> Optional[bytes]:
        """
        Retrieves a report by job_id, type, and optional essay_id.

        Args:
            job_id: The job ID
            report_type: Type of report
            essay_id: Optional essay ID for per-student reports

        Returns:
            Report content as bytes, or None if not found
        """
        cursor = self.conn.cursor()
        if essay_id is not None:
            cursor.execute(
                """
                SELECT content FROM reports
                WHERE job_id = ? AND report_type = ? AND essay_id = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (job_id, report_type, essay_id),
            )
        else:
            cursor.execute(
                """
                SELECT content FROM reports
                WHERE job_id = ? AND report_type = ? AND essay_id IS NULL
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (job_id, report_type),
            )
        row = cursor.fetchone()
        return row["content"] if row else None

    def get_report_with_metadata(
        self, job_id: str, report_type: str, essay_id: Optional[int] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Retrieves a report with full metadata (filename, content, timestamps).

        Args:
            job_id: The job ID
            report_type: Type of report ('gradebook_csv', 'student_feedback_zip', 'student_pdf')
            essay_id: Optional essay ID for per-student reports

        Returns:
            Dict with keys: id, job_id, report_type, essay_id, filename, content, created_at
            or None if not found
        """
        cursor = self.conn.cursor()
        if essay_id is not None:
            cursor.execute(
                """
                SELECT id, job_id, report_type, essay_id, filename, content, created_at
                FROM reports
                WHERE job_id = ? AND report_type = ? AND essay_id = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (job_id, report_type, essay_id),
            )
        else:
            cursor.execute(
                """
                SELECT id, job_id, report_type, essay_id, filename, content, created_at
                FROM reports
                WHERE job_id = ? AND report_type = ? AND essay_id IS NULL
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (job_id, report_type),
            )
        row = cursor.fetchone()
        if row:
            return {
                "id": row["id"],
                "job_id": row["job_id"],
                "report_type": row["report_type"],
                "essay_id": row["essay_id"],
                "filename": row["filename"],
                "content": row["content"],
                "created_at": row["created_at"],
            }
        return None

    def delete_job_reports(self, job_id: str) -> int:
        """
        Deletes all reports associated with a job.

        Args:
            job_id: The job ID

        Returns:
            Number of reports deleted
        """
        cursor = self.conn.cursor()
        cursor.execute("DELETE FROM reports WHERE job_id = ?", (job_id,))
        self.conn.commit()
        return cursor.rowcount

    def close(self):
        """Closes the database connection."""
        self.conn.close()
