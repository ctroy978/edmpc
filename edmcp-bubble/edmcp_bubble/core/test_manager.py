"""
Bubble Test Manager - Database operations for bubble tests.

Manages storage of bubble tests, generated sheets, and answer keys
in the central edmcp database.
"""

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from edmcp_core.db import DatabaseManager


class BubbleTestManager:
    """Manages bubble test data in the central database."""

    def __init__(self, db_path: Optional[str] = None):
        """
        Initialize the test manager.

        Args:
            db_path: Path to database file. If None, uses central database.
        """
        if db_path is None:
            # Use central database in edmcp/data/
            db_path = str(Path(__file__).parent.parent.parent.parent / "data" / "edmcp.db")

        self.db = DatabaseManager(db_path)
        self._create_tables()

    def _create_tables(self):
        """Create bubble test tables if they don't exist."""
        cursor = self.db.conn.cursor()

        # Bubble tests table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS bubble_tests (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                created_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'CREATED',
                description TEXT,
                metadata TEXT,
                archived INTEGER NOT NULL DEFAULT 0
            )
        """)

        # Migration: add archived column if it doesn't exist
        cursor.execute("PRAGMA table_info(bubble_tests)")
        columns = [row["name"] for row in cursor.fetchall()]
        if "archived" not in columns:
            cursor.execute(
                "ALTER TABLE bubble_tests ADD COLUMN archived INTEGER NOT NULL DEFAULT 0"
            )

        # Bubble sheets table (stores PDF and layout)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS bubble_sheets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                test_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                paper_size TEXT NOT NULL DEFAULT 'A4',
                num_questions INTEGER NOT NULL,
                id_length INTEGER NOT NULL DEFAULT 6,
                id_orientation TEXT NOT NULL DEFAULT 'vertical',
                draw_border INTEGER NOT NULL DEFAULT 0,
                layout_json TEXT,
                pdf_content BLOB,
                FOREIGN KEY (test_id) REFERENCES bubble_tests (id)
            )
        """)

        # Answer keys table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS answer_keys (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                test_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                key_data TEXT NOT NULL,
                total_points REAL,
                FOREIGN KEY (test_id) REFERENCES bubble_tests (id)
            )
        """)

        # Grading jobs table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS grading_jobs (
                id TEXT PRIMARY KEY,
                test_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'CREATED',
                scan_pdf BLOB,
                num_pages INTEGER,
                num_students INTEGER DEFAULT 0,
                error_message TEXT,
                FOREIGN KEY (test_id) REFERENCES bubble_tests (id)
            )
        """)

        # Student responses table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS student_responses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL,
                page_number INTEGER NOT NULL,
                student_id TEXT,
                answers_json TEXT NOT NULL,
                score REAL,
                percent_grade REAL,
                scan_status TEXT NOT NULL DEFAULT 'PENDING',
                scan_warnings TEXT,
                FOREIGN KEY (job_id) REFERENCES grading_jobs (id)
            )
        """)

        # Grading reports table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS grading_reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL,
                report_type TEXT NOT NULL,
                filename TEXT,
                content BLOB,
                created_at TEXT NOT NULL,
                FOREIGN KEY (job_id) REFERENCES grading_jobs (id)
            )
        """)

        self.db.conn.commit()

    def create_test(self, name: str, description: Optional[str] = None) -> str:
        """
        Create a new bubble test.

        Args:
            name: User-friendly test name
            description: Optional description

        Returns:
            Test ID (e.g., "bt_20260125_abc12345")
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        unique_suffix = str(uuid.uuid4())[:8]
        test_id = f"bt_{timestamp}_{unique_suffix}"
        created_at = datetime.now().isoformat()

        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            INSERT INTO bubble_tests (id, name, created_at, status, description)
            VALUES (?, ?, ?, 'CREATED', ?)
            """,
            (test_id, name, created_at, description),
        )
        self.db.conn.commit()

        return test_id

    def store_sheet(
        self,
        test_id: str,
        pdf_bytes: bytes,
        layout: Dict[str, Any],
        num_questions: int,
        paper_size: str = "A4",
        id_length: int = 6,
        id_orientation: str = "vertical",
        draw_border: bool = False,
    ) -> int:
        """
        Store a generated bubble sheet.

        Args:
            test_id: Parent test ID
            pdf_bytes: PDF content as bytes
            layout: Layout dictionary from generator
            num_questions: Number of questions
            paper_size: Paper size used
            id_length: Student ID length
            id_orientation: ID orientation
            draw_border: Whether border was drawn

        Returns:
            Sheet ID
        """
        created_at = datetime.now().isoformat()
        layout_json = json.dumps(layout)

        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            INSERT INTO bubble_sheets
            (test_id, created_at, paper_size, num_questions, id_length,
             id_orientation, draw_border, layout_json, pdf_content)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                test_id,
                created_at,
                paper_size,
                num_questions,
                id_length,
                id_orientation,
                1 if draw_border else 0,
                layout_json,
                pdf_bytes,
            ),
        )

        # Update test status
        cursor.execute(
            "UPDATE bubble_tests SET status = 'SHEET_GENERATED' WHERE id = ?",
            (test_id,),
        )

        self.db.conn.commit()
        sheet_id = cursor.lastrowid
        assert sheet_id is not None
        return sheet_id

    def get_test(self, test_id: str) -> Optional[Dict[str, Any]]:
        """
        Get test details with status info.

        Args:
            test_id: Test ID

        Returns:
            Test dict with keys: id, name, created_at, status, description,
            has_sheet, has_answer_key, or None if not found
        """
        cursor = self.db.conn.cursor()

        # Get test
        cursor.execute("SELECT * FROM bubble_tests WHERE id = ?", (test_id,))
        row = cursor.fetchone()
        if not row:
            return None

        test = dict(row)

        # Check for sheet
        cursor.execute(
            "SELECT COUNT(*) as count FROM bubble_sheets WHERE test_id = ?",
            (test_id,),
        )
        test["has_sheet"] = cursor.fetchone()["count"] > 0

        # Check for answer key
        cursor.execute(
            "SELECT COUNT(*) as count FROM answer_keys WHERE test_id = ?",
            (test_id,),
        )
        test["has_answer_key"] = cursor.fetchone()["count"] > 0

        return test

    def list_tests(
        self,
        limit: int = 50,
        offset: int = 0,
        status: Optional[str] = None,
        search: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        include_archived: bool = False,
        sort_by: str = "created_at",
        sort_order: str = "desc",
    ) -> Dict[str, Any]:
        """
        List bubble tests with filtering, sorting, and pagination.

        Args:
            limit: Maximum number of tests to return
            offset: Number of tests to skip (for pagination)
            status: Filter by status (CREATED, SHEET_GENERATED, KEY_ADDED)
            search: Search in name and description
            date_from: Filter tests created on or after this date (ISO format)
            date_to: Filter tests created on or before this date (ISO format)
            include_archived: Whether to include archived tests
            sort_by: Field to sort by (created_at, name, status)
            sort_order: Sort direction (asc, desc)

        Returns:
            Dict with keys: tests (list), total (int), limit (int), offset (int)
        """
        cursor = self.db.conn.cursor()

        # Build WHERE clause
        conditions = []
        params: List[Any] = []

        if not include_archived:
            conditions.append("bt.archived = 0")

        if status:
            conditions.append("bt.status = ?")
            params.append(status)

        if search:
            conditions.append("(bt.name LIKE ? OR bt.description LIKE ?)")
            search_pattern = f"%{search}%"
            params.extend([search_pattern, search_pattern])

        if date_from:
            conditions.append("bt.created_at >= ?")
            params.append(date_from)

        if date_to:
            # Add time component to include the entire day
            conditions.append("bt.created_at < ?")
            params.append(date_to + "T23:59:59.999999")

        where_clause = " AND ".join(conditions) if conditions else "1=1"

        # Validate sort_by to prevent SQL injection
        valid_sort_fields = {"created_at", "name", "status"}
        if sort_by not in valid_sort_fields:
            sort_by = "created_at"

        sort_direction = "DESC" if sort_order.lower() == "desc" else "ASC"

        # Get total count
        count_query = f"""
            SELECT COUNT(*) as total
            FROM bubble_tests bt
            WHERE {where_clause}
        """
        cursor.execute(count_query, params)
        total = cursor.fetchone()["total"]

        # Get paginated results
        query = f"""
            SELECT bt.*,
                   (SELECT COUNT(*) FROM bubble_sheets WHERE test_id = bt.id) as sheet_count,
                   (SELECT COUNT(*) FROM answer_keys WHERE test_id = bt.id) as key_count
            FROM bubble_tests bt
            WHERE {where_clause}
            ORDER BY bt.{sort_by} {sort_direction}
            LIMIT ? OFFSET ?
        """
        params.extend([limit, offset])
        cursor.execute(query, params)

        tests = []
        for row in cursor.fetchall():
            test = dict(row)
            test["has_sheet"] = test.pop("sheet_count") > 0
            test["has_answer_key"] = test.pop("key_count") > 0
            test["archived"] = bool(test.get("archived", 0))
            tests.append(test)

        return {
            "tests": tests,
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    def get_sheet(self, test_id: str) -> Optional[Dict[str, Any]]:
        """
        Get bubble sheet for a test.

        Args:
            test_id: Test ID

        Returns:
            Sheet dict with pdf_content and layout, or None if not found
        """
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            SELECT * FROM bubble_sheets WHERE test_id = ?
            ORDER BY created_at DESC LIMIT 1
            """,
            (test_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None

        sheet = dict(row)
        # Parse layout JSON
        if sheet["layout_json"]:
            sheet["layout"] = json.loads(sheet["layout_json"])
        else:
            sheet["layout"] = None

        return sheet

    def get_sheet_pdf(self, test_id: str) -> Optional[bytes]:
        """
        Get just the PDF content for a test.

        Args:
            test_id: Test ID

        Returns:
            PDF bytes or None if not found
        """
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            SELECT pdf_content FROM bubble_sheets WHERE test_id = ?
            ORDER BY created_at DESC LIMIT 1
            """,
            (test_id,),
        )
        row = cursor.fetchone()
        return row["pdf_content"] if row else None

    def get_sheet_layout(self, test_id: str) -> Optional[Dict[str, Any]]:
        """
        Get just the layout JSON for a test.

        Args:
            test_id: Test ID

        Returns:
            Layout dict or None if not found
        """
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            SELECT layout_json FROM bubble_sheets WHERE test_id = ?
            ORDER BY created_at DESC LIMIT 1
            """,
            (test_id,),
        )
        row = cursor.fetchone()
        if row and row["layout_json"]:
            return json.loads(row["layout_json"])
        return None

    def set_answer_key(
        self, test_id: str, answers: List[Dict[str, Any]]
    ) -> int:
        """
        Store or update answer key for a test.

        Args:
            test_id: Test ID
            answers: List of answer dicts with keys: question, answer, points
                     e.g., [{"question": "Q1", "answer": "a", "points": 1.0}]

        Returns:
            Answer key ID
        """
        # Calculate total points
        total_points = sum(a.get("points", 1.0) for a in answers)
        key_data = json.dumps(answers)
        created_at = datetime.now().isoformat()

        cursor = self.db.conn.cursor()

        # Delete existing answer key for this test
        cursor.execute("DELETE FROM answer_keys WHERE test_id = ?", (test_id,))

        # Insert new answer key
        cursor.execute(
            """
            INSERT INTO answer_keys (test_id, created_at, key_data, total_points)
            VALUES (?, ?, ?, ?)
            """,
            (test_id, created_at, key_data, total_points),
        )

        # Update test status
        cursor.execute(
            """
            UPDATE bubble_tests
            SET status = CASE
                WHEN status = 'SHEET_GENERATED' THEN 'KEY_ADDED'
                ELSE status
            END
            WHERE id = ?
            """,
            (test_id,),
        )

        self.db.conn.commit()
        key_id = cursor.lastrowid
        assert key_id is not None
        return key_id

    def get_answer_key(self, test_id: str) -> Optional[Dict[str, Any]]:
        """
        Get answer key for a test.

        Args:
            test_id: Test ID

        Returns:
            Answer key dict with keys: id, test_id, created_at, answers, total_points
            or None if not found
        """
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            SELECT * FROM answer_keys WHERE test_id = ?
            ORDER BY created_at DESC LIMIT 1
            """,
            (test_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None

        result = dict(row)
        result["answers"] = json.loads(result.pop("key_data"))
        return result

    def delete_test(self, test_id: str) -> bool:
        """
        Delete a test and all associated data.

        Args:
            test_id: Test ID

        Returns:
            True if deleted, False if not found
        """
        cursor = self.db.conn.cursor()

        # Check if exists
        cursor.execute("SELECT 1 FROM bubble_tests WHERE id = ?", (test_id,))
        if not cursor.fetchone():
            return False

        # Delete in order (foreign keys)
        cursor.execute("DELETE FROM answer_keys WHERE test_id = ?", (test_id,))
        cursor.execute("DELETE FROM bubble_sheets WHERE test_id = ?", (test_id,))
        cursor.execute("DELETE FROM bubble_tests WHERE id = ?", (test_id,))

        self.db.conn.commit()
        return True

    def archive_test(self, test_id: str) -> bool:
        """
        Archive a test (soft delete).

        Args:
            test_id: Test ID

        Returns:
            True if archived, False if not found
        """
        cursor = self.db.conn.cursor()
        cursor.execute(
            "UPDATE bubble_tests SET archived = 1 WHERE id = ? AND archived = 0",
            (test_id,),
        )
        self.db.conn.commit()
        return cursor.rowcount > 0

    def unarchive_test(self, test_id: str) -> bool:
        """
        Unarchive a test (restore from archive).

        Args:
            test_id: Test ID

        Returns:
            True if unarchived, False if not found or not archived
        """
        cursor = self.db.conn.cursor()
        cursor.execute(
            "UPDATE bubble_tests SET archived = 0 WHERE id = ? AND archived = 1",
            (test_id,),
        )
        self.db.conn.commit()
        return cursor.rowcount > 0

    def close(self):
        """Close database connection."""
        self.db.close()
