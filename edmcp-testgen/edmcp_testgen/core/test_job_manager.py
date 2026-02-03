"""
Test Job Manager - Database operations for test generation jobs.

Manages storage of test jobs, questions, and materials in the central edmcp database.
"""

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from edmcp_core.db import DatabaseManager


class TestJobManager:
    """Manages test generation job data in the central database."""

    def __init__(self, db_path: Optional[str] = None):
        """
        Initialize the test job manager.

        Args:
            db_path: Path to database file. If None, uses central database.
        """
        if db_path is None:
            db_path = str(Path(__file__).parent.parent.parent.parent / "data" / "edmcp.db")

        self.db = DatabaseManager(db_path)
        self._create_tables()

    def _create_tables(self):
        """Create test generation tables if they don't exist."""
        cursor = self.db.conn.cursor()

        # Test jobs table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS test_jobs (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'CREATED',
                name TEXT,
                description TEXT,
                knowledge_topic TEXT,

                -- Core Specifications
                total_questions INTEGER DEFAULT 20,
                total_points REAL DEFAULT 100,
                difficulty TEXT DEFAULT 'medium',
                grade_level TEXT,

                -- Question Type Distribution (JSON)
                question_distribution TEXT,

                -- Optional Controls
                focus_topics TEXT,
                source_weighting TEXT,
                include_word_bank INTEGER DEFAULT 0,
                include_rubrics INTEGER DEFAULT 1,

                -- Generated Content
                generated_test TEXT,
                generated_key TEXT,

                metadata TEXT,
                archived INTEGER NOT NULL DEFAULT 0
            )
        """)

        # Test questions table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS test_questions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL,
                question_number INTEGER NOT NULL,
                question_type TEXT NOT NULL,
                question_text TEXT NOT NULL,

                -- MCQ-specific
                options TEXT,
                correct_answer TEXT,
                distractors_rationale TEXT,

                -- SA-specific
                model_answer TEXT,
                rubric TEXT,

                -- Metadata
                points REAL DEFAULT 1.0,
                difficulty TEXT,
                source_reference TEXT,
                status TEXT DEFAULT 'GENERATED',
                regeneration_count INTEGER DEFAULT 0,

                FOREIGN KEY (job_id) REFERENCES test_jobs (id)
            )
        """)

        # Test materials table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS test_materials (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL,
                file_path TEXT,
                file_name TEXT,
                content_preview TEXT,
                content_type TEXT,
                ingested_at TEXT,

                FOREIGN KEY (job_id) REFERENCES test_jobs (id)
            )
        """)

        self.db.conn.commit()

    def create_job(
        self,
        name: Optional[str] = None,
        description: Optional[str] = None,
        total_questions: int = 20,
        total_points: float = 100.0,
        difficulty: str = "medium",
        grade_level: Optional[str] = None,
        question_distribution: Optional[Dict[str, int]] = None,
        focus_topics: Optional[List[str]] = None,
        source_weighting: Optional[Dict[str, float]] = None,
        include_word_bank: bool = False,
        include_rubrics: bool = True,
    ) -> str:
        """
        Create a new test generation job.

        Args:
            name: User-friendly job name
            description: Optional description
            total_questions: Total number of questions to generate (default 20)
            total_points: Total points for the test (default 100)
            difficulty: Overall difficulty level (easy, medium, hard)
            grade_level: Target grade level
            question_distribution: Dict of question type to count, e.g., {"mcq": 10, "fib": 5, "sa": 5}
            focus_topics: List of topics to emphasize
            source_weighting: Dict of source to weight percentage
            include_word_bank: Whether to include word bank for FIB
            include_rubrics: Whether to include rubrics for SA

        Returns:
            Job ID (e.g., "tg_20260131_abc12345")
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        unique_suffix = str(uuid.uuid4())[:8]
        job_id = f"tg_{timestamp}_{unique_suffix}"
        created_at = datetime.now().isoformat()

        # Default distribution if not provided
        if question_distribution is None:
            # 50% MCQ, 30% FIB, 20% SA
            mcq_count = int(total_questions * 0.5)
            fib_count = int(total_questions * 0.3)
            sa_count = total_questions - mcq_count - fib_count
            question_distribution = {"mcq": mcq_count, "fib": fib_count, "sa": sa_count}

        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            INSERT INTO test_jobs (
                id, created_at, status, name, description,
                total_questions, total_points, difficulty, grade_level,
                question_distribution, focus_topics, source_weighting,
                include_word_bank, include_rubrics
            )
            VALUES (?, ?, 'CREATED', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                created_at,
                name,
                description,
                total_questions,
                total_points,
                difficulty,
                grade_level,
                json.dumps(question_distribution),
                json.dumps(focus_topics) if focus_topics else None,
                json.dumps(source_weighting) if source_weighting else None,
                1 if include_word_bank else 0,
                1 if include_rubrics else 0,
            ),
        )
        self.db.conn.commit()

        return job_id

    def update_job_specs(
        self,
        job_id: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
        total_questions: Optional[int] = None,
        total_points: Optional[float] = None,
        difficulty: Optional[str] = None,
        grade_level: Optional[str] = None,
        question_distribution: Optional[Dict[str, int]] = None,
        focus_topics: Optional[List[str]] = None,
        include_word_bank: Optional[bool] = None,
        include_rubrics: Optional[bool] = None,
    ) -> bool:
        """
        Update job specifications.

        Returns:
            True if job was updated, False if not found
        """
        updates = []
        params = []

        if name is not None:
            updates.append("name = ?")
            params.append(name)
        if description is not None:
            updates.append("description = ?")
            params.append(description)
        if total_questions is not None:
            updates.append("total_questions = ?")
            params.append(total_questions)
        if total_points is not None:
            updates.append("total_points = ?")
            params.append(total_points)
        if difficulty is not None:
            updates.append("difficulty = ?")
            params.append(difficulty)
        if grade_level is not None:
            updates.append("grade_level = ?")
            params.append(grade_level)
        if question_distribution is not None:
            updates.append("question_distribution = ?")
            params.append(json.dumps(question_distribution))
        if focus_topics is not None:
            updates.append("focus_topics = ?")
            params.append(json.dumps(focus_topics))
        if include_word_bank is not None:
            updates.append("include_word_bank = ?")
            params.append(1 if include_word_bank else 0)
        if include_rubrics is not None:
            updates.append("include_rubrics = ?")
            params.append(1 if include_rubrics else 0)

        if not updates:
            return False

        params.append(job_id)
        sql = f"UPDATE test_jobs SET {', '.join(updates)} WHERE id = ?"

        cursor = self.db.conn.cursor()
        cursor.execute(sql, params)
        self.db.conn.commit()

        return cursor.rowcount > 0

    def set_knowledge_topic(self, job_id: str, topic: str) -> bool:
        """Set the knowledge base topic for a job."""
        cursor = self.db.conn.cursor()
        cursor.execute(
            "UPDATE test_jobs SET knowledge_topic = ? WHERE id = ?",
            (topic, job_id),
        )
        self.db.conn.commit()
        return cursor.rowcount > 0

    def update_status(self, job_id: str, status: str) -> bool:
        """Update job status."""
        cursor = self.db.conn.cursor()
        cursor.execute(
            "UPDATE test_jobs SET status = ? WHERE id = ?",
            (status, job_id),
        )
        self.db.conn.commit()
        return cursor.rowcount > 0

    def add_material(
        self,
        job_id: str,
        file_path: str,
        file_name: str,
        content_preview: str,
        content_type: str,
    ) -> int:
        """
        Track an ingested material file.

        Returns:
            Material ID
        """
        ingested_at = datetime.now().isoformat()

        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            INSERT INTO test_materials (job_id, file_path, file_name, content_preview, content_type, ingested_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (job_id, file_path, file_name, content_preview[:500], content_type, ingested_at),
        )
        self.db.conn.commit()

        material_id = cursor.lastrowid
        assert material_id is not None
        return material_id

    def get_job_materials(self, job_id: str) -> List[Dict[str, Any]]:
        """Get all materials for a job."""
        cursor = self.db.conn.cursor()
        cursor.execute(
            "SELECT * FROM test_materials WHERE job_id = ? ORDER BY ingested_at",
            (job_id,),
        )
        return [dict(row) for row in cursor.fetchall()]

    def store_question(
        self,
        job_id: str,
        question_number: int,
        question_type: str,
        question_text: str,
        correct_answer: str,
        points: float = 1.0,
        difficulty: str = "medium",
        options: Optional[List[Dict[str, str]]] = None,
        distractors_rationale: Optional[str] = None,
        model_answer: Optional[str] = None,
        rubric: Optional[Dict[str, Any]] = None,
        source_reference: Optional[str] = None,
    ) -> int:
        """
        Store a generated question.

        Returns:
            Question ID
        """
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            INSERT INTO test_questions (
                job_id, question_number, question_type, question_text,
                options, correct_answer, distractors_rationale,
                model_answer, rubric, points, difficulty, source_reference, status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'GENERATED')
            """,
            (
                job_id,
                question_number,
                question_type,
                question_text,
                json.dumps(options) if options else None,
                correct_answer,
                distractors_rationale,
                model_answer,
                json.dumps(rubric) if rubric else None,
                points,
                difficulty,
                source_reference,
            ),
        )
        self.db.conn.commit()

        question_id = cursor.lastrowid
        assert question_id is not None
        return question_id

    def get_job_questions(self, job_id: str) -> List[Dict[str, Any]]:
        """Get all questions for a job."""
        cursor = self.db.conn.cursor()
        cursor.execute(
            "SELECT * FROM test_questions WHERE job_id = ? ORDER BY question_number",
            (job_id,),
        )

        questions = []
        for row in cursor.fetchall():
            q = dict(row)
            if q["options"]:
                q["options"] = json.loads(q["options"])
            if q["rubric"]:
                q["rubric"] = json.loads(q["rubric"])
            questions.append(q)

        return questions

    def get_question(self, question_id: int) -> Optional[Dict[str, Any]]:
        """Get a specific question by ID."""
        cursor = self.db.conn.cursor()
        cursor.execute("SELECT * FROM test_questions WHERE id = ?", (question_id,))
        row = cursor.fetchone()

        if not row:
            return None

        q = dict(row)
        if q["options"]:
            q["options"] = json.loads(q["options"])
        if q["rubric"]:
            q["rubric"] = json.loads(q["rubric"])

        return q

    def update_question(
        self,
        question_id: int,
        question_text: Optional[str] = None,
        correct_answer: Optional[str] = None,
        options: Optional[List[Dict[str, str]]] = None,
        model_answer: Optional[str] = None,
        rubric: Optional[Dict[str, Any]] = None,
        points: Optional[float] = None,
        difficulty: Optional[str] = None,
        status: Optional[str] = None,
    ) -> bool:
        """Update a question."""
        updates = []
        params = []

        if question_text is not None:
            updates.append("question_text = ?")
            params.append(question_text)
        if correct_answer is not None:
            updates.append("correct_answer = ?")
            params.append(correct_answer)
        if options is not None:
            updates.append("options = ?")
            params.append(json.dumps(options))
        if model_answer is not None:
            updates.append("model_answer = ?")
            params.append(model_answer)
        if rubric is not None:
            updates.append("rubric = ?")
            params.append(json.dumps(rubric))
        if points is not None:
            updates.append("points = ?")
            params.append(points)
        if difficulty is not None:
            updates.append("difficulty = ?")
            params.append(difficulty)
        if status is not None:
            updates.append("status = ?")
            params.append(status)

        if not updates:
            return False

        params.append(question_id)
        sql = f"UPDATE test_questions SET {', '.join(updates)} WHERE id = ?"

        cursor = self.db.conn.cursor()
        cursor.execute(sql, params)
        self.db.conn.commit()

        return cursor.rowcount > 0

    def increment_regeneration_count(self, question_id: int) -> bool:
        """Increment the regeneration count for a question."""
        cursor = self.db.conn.cursor()
        cursor.execute(
            "UPDATE test_questions SET regeneration_count = regeneration_count + 1 WHERE id = ?",
            (question_id,),
        )
        self.db.conn.commit()
        return cursor.rowcount > 0

    def delete_question(self, question_id: int) -> bool:
        """Delete a question."""
        cursor = self.db.conn.cursor()
        cursor.execute("DELETE FROM test_questions WHERE id = ?", (question_id,))
        self.db.conn.commit()
        return cursor.rowcount > 0

    def clear_job_questions(self, job_id: str) -> int:
        """Delete all questions for a job. Returns count deleted."""
        cursor = self.db.conn.cursor()
        cursor.execute("DELETE FROM test_questions WHERE job_id = ?", (job_id,))
        self.db.conn.commit()
        return cursor.rowcount

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        """
        Get job details with parsed JSON fields.

        Returns:
            Job dict or None if not found
        """
        cursor = self.db.conn.cursor()
        cursor.execute("SELECT * FROM test_jobs WHERE id = ?", (job_id,))
        row = cursor.fetchone()

        if not row:
            return None

        job = dict(row)

        # Parse JSON fields
        if job["question_distribution"]:
            job["question_distribution"] = json.loads(job["question_distribution"])
        if job["focus_topics"]:
            job["focus_topics"] = json.loads(job["focus_topics"])
        if job["source_weighting"]:
            job["source_weighting"] = json.loads(job["source_weighting"])
        if job["generated_test"]:
            job["generated_test"] = json.loads(job["generated_test"])
        if job["generated_key"]:
            job["generated_key"] = json.loads(job["generated_key"])
        if job["metadata"]:
            job["metadata"] = json.loads(job["metadata"])

        # Convert boolean fields
        job["include_word_bank"] = bool(job["include_word_bank"])
        job["include_rubrics"] = bool(job["include_rubrics"])
        job["archived"] = bool(job["archived"])

        # Add counts
        cursor.execute(
            "SELECT COUNT(*) as count FROM test_questions WHERE job_id = ?",
            (job_id,),
        )
        job["question_count"] = cursor.fetchone()["count"]

        cursor.execute(
            "SELECT COUNT(*) as count FROM test_materials WHERE job_id = ?",
            (job_id,),
        )
        job["material_count"] = cursor.fetchone()["count"]

        return job

    def list_jobs(
        self,
        limit: int = 50,
        offset: int = 0,
        status: Optional[str] = None,
        search: Optional[str] = None,
        include_archived: bool = False,
        sort_by: str = "created_at",
        sort_order: str = "desc",
    ) -> Dict[str, Any]:
        """
        List test jobs with filtering and pagination.

        Returns:
            Dict with keys: jobs (list), total (int), limit (int), offset (int)
        """
        cursor = self.db.conn.cursor()

        conditions = []
        params: List[Any] = []

        if not include_archived:
            conditions.append("archived = 0")

        if status:
            conditions.append("status = ?")
            params.append(status)

        if search:
            conditions.append("(name LIKE ? OR description LIKE ?)")
            search_pattern = f"%{search}%"
            params.extend([search_pattern, search_pattern])

        where_clause = " AND ".join(conditions) if conditions else "1=1"

        # Validate sort_by
        valid_sort_fields = {"created_at", "name", "status"}
        if sort_by not in valid_sort_fields:
            sort_by = "created_at"

        sort_direction = "DESC" if sort_order.lower() == "desc" else "ASC"

        # Get total count
        cursor.execute(f"SELECT COUNT(*) as total FROM test_jobs WHERE {where_clause}", params)
        total = cursor.fetchone()["total"]

        # Get paginated results
        query = f"""
            SELECT id, created_at, status, name, description, total_questions, difficulty,
                   (SELECT COUNT(*) FROM test_questions WHERE job_id = test_jobs.id) as question_count,
                   (SELECT COUNT(*) FROM test_materials WHERE job_id = test_jobs.id) as material_count
            FROM test_jobs
            WHERE {where_clause}
            ORDER BY {sort_by} {sort_direction}
            LIMIT ? OFFSET ?
        """
        params.extend([limit, offset])
        cursor.execute(query, params)

        jobs = [dict(row) for row in cursor.fetchall()]

        return {
            "jobs": jobs,
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    def archive_job(self, job_id: str) -> bool:
        """Archive a job (soft delete)."""
        cursor = self.db.conn.cursor()
        cursor.execute(
            "UPDATE test_jobs SET archived = 1 WHERE id = ? AND archived = 0",
            (job_id,),
        )
        self.db.conn.commit()
        return cursor.rowcount > 0

    def unarchive_job(self, job_id: str) -> bool:
        """Unarchive a job."""
        cursor = self.db.conn.cursor()
        cursor.execute(
            "UPDATE test_jobs SET archived = 0 WHERE id = ? AND archived = 1",
            (job_id,),
        )
        self.db.conn.commit()
        return cursor.rowcount > 0

    def delete_job(self, job_id: str) -> bool:
        """Delete a job and all associated data."""
        cursor = self.db.conn.cursor()

        # Check if exists
        cursor.execute("SELECT 1 FROM test_jobs WHERE id = ?", (job_id,))
        if not cursor.fetchone():
            return False

        # Delete in order (foreign keys)
        cursor.execute("DELETE FROM test_questions WHERE job_id = ?", (job_id,))
        cursor.execute("DELETE FROM test_materials WHERE job_id = ?", (job_id,))
        cursor.execute("DELETE FROM test_jobs WHERE id = ?", (job_id,))

        self.db.conn.commit()
        return True

    def store_generated_test(self, job_id: str, test_data: Dict[str, Any]) -> bool:
        """Store the full generated test JSON."""
        cursor = self.db.conn.cursor()
        cursor.execute(
            "UPDATE test_jobs SET generated_test = ?, status = 'COMPLETE' WHERE id = ?",
            (json.dumps(test_data), job_id),
        )
        self.db.conn.commit()
        return cursor.rowcount > 0

    def store_generated_key(self, job_id: str, key_data: Dict[str, Any]) -> bool:
        """Store the full generated answer key JSON."""
        cursor = self.db.conn.cursor()
        cursor.execute(
            "UPDATE test_jobs SET generated_key = ? WHERE id = ?",
            (json.dumps(key_data), job_id),
        )
        self.db.conn.commit()
        return cursor.rowcount > 0

    def close(self):
        """Close database connection."""
        self.db.close()
