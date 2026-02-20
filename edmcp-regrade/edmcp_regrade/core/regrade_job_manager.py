"""
Regrade Job Manager - Database operations for essay regrading jobs.

Manages storage of regrade jobs, essays, and their states in the central edmcp database.
"""

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from edmcp_core.db import DatabaseManager


class RegradeJobManager:
    """Manages regrade job data in the central database."""

    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            db_path = str(Path(__file__).parent.parent.parent.parent / "data" / "edmcp.db")

        self.db = DatabaseManager(db_path)
        self._create_tables()

    def _create_tables(self):
        """Create regrade tables if they don't exist."""
        cursor = self.db.conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS regrade_jobs (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'PENDING',
                name TEXT NOT NULL,
                class_name TEXT,
                assignment_title TEXT,
                due_date TEXT,
                rubric TEXT,
                question_text TEXT,
                knowledge_base_topic TEXT,
                essay_count INTEGER NOT NULL DEFAULT 0,
                graded_count INTEGER NOT NULL DEFAULT 0,
                metadata TEXT,
                archived INTEGER NOT NULL DEFAULT 0
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS regrade_essays (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL,
                student_identifier TEXT,
                essay_text TEXT,
                evaluation TEXT,
                grade TEXT,
                status TEXT NOT NULL DEFAULT 'PENDING',
                teacher_grade TEXT,
                teacher_comments TEXT,
                teacher_annotations TEXT,
                FOREIGN KEY (job_id) REFERENCES regrade_jobs (id)
            )
        """)

        self.db.conn.commit()

    # ========================================================================
    # Job CRUD
    # ========================================================================

    def create_job(
        self,
        name: str,
        rubric: Optional[str] = None,
        class_name: Optional[str] = None,
        assignment_title: Optional[str] = None,
        due_date: Optional[str] = None,
        question_text: Optional[str] = None,
    ) -> str:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        unique_suffix = str(uuid.uuid4())[:8]
        job_id = f"rg_{timestamp}_{unique_suffix}"
        now = datetime.now().isoformat()

        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            INSERT INTO regrade_jobs (
                id, created_at, updated_at, status, name,
                class_name, assignment_title, due_date, rubric, question_text
            )
            VALUES (?, ?, ?, 'PENDING', ?, ?, ?, ?, ?, ?)
            """,
            (job_id, now, now, name, class_name, assignment_title, due_date, rubric, question_text),
        )
        self.db.conn.commit()
        return job_id

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        cursor = self.db.conn.cursor()
        cursor.execute("SELECT * FROM regrade_jobs WHERE id = ?", (job_id,))
        row = cursor.fetchone()
        if not row:
            return None

        job = dict(row)
        if job["metadata"]:
            job["metadata"] = json.loads(job["metadata"])
        job["archived"] = bool(job["archived"])
        return job

    def update_status(self, job_id: str, status: str) -> bool:
        now = datetime.now().isoformat()
        cursor = self.db.conn.cursor()
        cursor.execute(
            "UPDATE regrade_jobs SET status = ?, updated_at = ? WHERE id = ?",
            (status, now, job_id),
        )
        self.db.conn.commit()
        return cursor.rowcount > 0

    def set_knowledge_topic(self, job_id: str, topic: str) -> bool:
        now = datetime.now().isoformat()
        cursor = self.db.conn.cursor()
        cursor.execute(
            "UPDATE regrade_jobs SET knowledge_base_topic = ?, updated_at = ? WHERE id = ?",
            (topic, now, job_id),
        )
        self.db.conn.commit()
        return cursor.rowcount > 0

    def _update_essay_count(self, job_id: str):
        """Recalculate and update the denormalized essay_count."""
        cursor = self.db.conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) as cnt FROM regrade_essays WHERE job_id = ?",
            (job_id,),
        )
        count = cursor.fetchone()["cnt"]
        now = datetime.now().isoformat()
        cursor.execute(
            "UPDATE regrade_jobs SET essay_count = ?, updated_at = ? WHERE id = ?",
            (count, now, job_id),
        )
        self.db.conn.commit()

    def _update_graded_count(self, job_id: str):
        """Recalculate and update the denormalized graded_count."""
        cursor = self.db.conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) as cnt FROM regrade_essays WHERE job_id = ? AND status = 'GRADED'",
            (job_id,),
        )
        count = cursor.fetchone()["cnt"]
        now = datetime.now().isoformat()
        cursor.execute(
            "UPDATE regrade_jobs SET graded_count = ?, updated_at = ? WHERE id = ?",
            (count, now, job_id),
        )
        self.db.conn.commit()

    def list_jobs(
        self,
        limit: int = 50,
        offset: int = 0,
        status: Optional[str] = None,
        class_name: Optional[str] = None,
        search: Optional[str] = None,
        include_archived: bool = False,
    ) -> Dict[str, Any]:
        cursor = self.db.conn.cursor()

        conditions = []
        params: List[Any] = []

        if not include_archived:
            conditions.append("archived = 0")

        if status:
            conditions.append("status = ?")
            params.append(status)

        if class_name:
            conditions.append("class_name = ?")
            params.append(class_name)

        if search:
            conditions.append("(name LIKE ? OR class_name LIKE ? OR assignment_title LIKE ?)")
            pattern = f"%{search}%"
            params.extend([pattern, pattern, pattern])

        where_clause = " AND ".join(conditions) if conditions else "1=1"

        # Total count
        cursor.execute(f"SELECT COUNT(*) as total FROM regrade_jobs WHERE {where_clause}", params)
        total = cursor.fetchone()["total"]

        # Paginated results
        query = f"""
            SELECT id, created_at, updated_at, status, name,
                   class_name, assignment_title, due_date,
                   essay_count, graded_count, archived
            FROM regrade_jobs
            WHERE {where_clause}
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
        """
        params.extend([limit, offset])
        cursor.execute(query, params)

        jobs = [dict(row) for row in cursor.fetchall()]
        for job in jobs:
            job["archived"] = bool(job["archived"])

        return {
            "jobs": jobs,
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    def search_jobs(
        self,
        query: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        sql = """
            SELECT DISTINCT
                j.id, j.created_at, j.name, j.status,
                j.class_name, j.assignment_title,
                e.student_identifier, e.essay_text
            FROM regrade_jobs j
            LEFT JOIN regrade_essays e ON j.id = e.job_id
            WHERE (
                j.name LIKE ? OR
                j.class_name LIKE ? OR
                j.assignment_title LIKE ? OR
                e.student_identifier LIKE ? OR
                e.essay_text LIKE ?
            )
        """
        pattern = f"%{query}%"
        params: List[Any] = [pattern, pattern, pattern, pattern, pattern]

        if start_date:
            sql += " AND j.created_at >= ?"
            params.append(start_date)
        if end_date:
            sql += " AND j.created_at <= ?"
            params.append(end_date)

        sql += " ORDER BY j.created_at DESC"

        cursor = self.db.conn.cursor()
        cursor.execute(sql, params)
        rows = cursor.fetchall()

        jobs: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            job_id = row["id"]
            if job_id not in jobs:
                jobs[job_id] = {
                    "id": job_id,
                    "created_at": row["created_at"],
                    "name": row["name"],
                    "status": row["status"],
                    "class_name": row["class_name"],
                    "assignment_title": row["assignment_title"],
                    "matches": [],
                }

            # Build snippet
            snippet = ""
            reason = ""
            if query.lower() in (row["name"] or "").lower():
                reason = "Job Name Match"
                snippet = row["name"]
            elif query.lower() in (row["class_name"] or "").lower():
                reason = "Class Name Match"
                snippet = row["class_name"]
            elif query.lower() in (row["assignment_title"] or "").lower():
                reason = "Assignment Title Match"
                snippet = row["assignment_title"]
            elif query.lower() in (row["student_identifier"] or "").lower():
                reason = "Student Match"
                snippet = row["student_identifier"]
            elif row["essay_text"]:
                reason = "Content Match"
                text = row["essay_text"]
                idx = text.lower().find(query.lower())
                start = max(0, idx - 30)
                end = min(len(text), idx + len(query) + 30)
                snippet = "..." + text[start:end] + "..."

            if snippet and len(jobs[job_id]["matches"]) < 3:
                jobs[job_id]["matches"].append({"reason": reason, "snippet": snippet})

        return list(jobs.values())

    def archive_job(self, job_id: str) -> bool:
        now = datetime.now().isoformat()
        cursor = self.db.conn.cursor()
        cursor.execute(
            "UPDATE regrade_jobs SET archived = 1, updated_at = ? WHERE id = ? AND archived = 0",
            (now, job_id),
        )
        self.db.conn.commit()
        return cursor.rowcount > 0

    def delete_job(self, job_id: str) -> bool:
        cursor = self.db.conn.cursor()
        cursor.execute("SELECT 1 FROM regrade_jobs WHERE id = ?", (job_id,))
        if not cursor.fetchone():
            return False

        cursor.execute("DELETE FROM regrade_essays WHERE job_id = ?", (job_id,))
        cursor.execute("DELETE FROM regrade_jobs WHERE id = ?", (job_id,))
        self.db.conn.commit()
        return True

    # ========================================================================
    # Essay CRUD
    # ========================================================================

    def add_essay(self, job_id: str, student_identifier: str, essay_text: str) -> int:
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            INSERT INTO regrade_essays (job_id, student_identifier, essay_text)
            VALUES (?, ?, ?)
            """,
            (job_id, student_identifier, essay_text),
        )
        self.db.conn.commit()
        essay_id = cursor.lastrowid
        assert essay_id is not None
        self._update_essay_count(job_id)
        return essay_id

    def get_essay(self, essay_id: int) -> Optional[Dict[str, Any]]:
        cursor = self.db.conn.cursor()
        cursor.execute("SELECT * FROM regrade_essays WHERE id = ?", (essay_id,))
        row = cursor.fetchone()
        if not row:
            return None

        essay = dict(row)
        if essay["evaluation"]:
            essay["evaluation"] = json.loads(essay["evaluation"])
        if essay["teacher_annotations"]:
            essay["teacher_annotations"] = json.loads(essay["teacher_annotations"])
        return essay

    def get_job_essays(
        self,
        job_id: str,
        status: Optional[str] = None,
        include_text: bool = True,
    ) -> List[Dict[str, Any]]:
        cursor = self.db.conn.cursor()

        if include_text:
            columns = "*"
        else:
            columns = "id, job_id, student_identifier, grade, status, teacher_grade"

        sql = f"SELECT {columns} FROM regrade_essays WHERE job_id = ?"
        params: List[Any] = [job_id]

        if status:
            sql += " AND status = ?"
            params.append(status)

        sql += " ORDER BY id"

        cursor.execute(sql, params)
        essays = []
        for row in cursor.fetchall():
            essay = dict(row)
            if include_text:
                if essay.get("evaluation"):
                    essay["evaluation"] = json.loads(essay["evaluation"])
                if essay.get("teacher_annotations"):
                    essay["teacher_annotations"] = json.loads(essay["teacher_annotations"])
            essays.append(essay)
        return essays

    def update_essay_evaluation(
        self, essay_id: int, evaluation_json: str, grade: Optional[str] = None
    ):
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            UPDATE regrade_essays
            SET evaluation = ?, grade = ?, status = 'GRADED'
            WHERE id = ?
            """,
            (evaluation_json, grade, essay_id),
        )
        self.db.conn.commit()

        # Update graded_count on the parent job
        cursor.execute("SELECT job_id FROM regrade_essays WHERE id = ?", (essay_id,))
        row = cursor.fetchone()
        if row:
            self._update_graded_count(row["job_id"])

    def get_job_statistics(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Compute grade distribution, averages, and per-criteria breakdown."""
        job = self.get_job(job_id)
        if not job:
            return None

        essays = self.get_job_essays(job_id, include_text=True)

        total = len(essays)
        graded = [e for e in essays if e["status"] == "GRADED"]
        pending = total - len(graded)

        # Grade distribution
        grade_distribution: Dict[str, int] = {}
        numeric_grades: List[float] = []

        for essay in graded:
            grade = essay.get("grade", "")
            grade_distribution[grade] = grade_distribution.get(grade, 0) + 1

            # Try to parse numeric grade
            try:
                # Handle "85/100" format
                if "/" in str(grade):
                    num = float(str(grade).split("/")[0])
                else:
                    num = float(grade)
                numeric_grades.append(num)
            except (ValueError, TypeError):
                pass

        # Numeric stats
        average_grade = None
        min_grade = None
        max_grade = None
        if numeric_grades:
            average_grade = round(sum(numeric_grades) / len(numeric_grades), 2)
            min_grade = min(numeric_grades)
            max_grade = max(numeric_grades)

        # Per-criteria breakdown
        criteria_stats: Dict[str, Dict[str, Any]] = {}
        for essay in graded:
            eval_data = essay.get("evaluation")
            if not eval_data or not isinstance(eval_data, dict):
                continue
            for criterion in eval_data.get("criteria", []):
                cname = criterion.get("name", "Unknown")
                if cname not in criteria_stats:
                    criteria_stats[cname] = {"scores": [], "count": 0}
                criteria_stats[cname]["count"] += 1
                try:
                    score_str = str(criterion.get("score", ""))
                    if "/" in score_str:
                        score_val = float(score_str.split("/")[0])
                    else:
                        score_val = float(score_str)
                    criteria_stats[cname]["scores"].append(score_val)
                except (ValueError, TypeError):
                    pass

        # Compute averages per criterion
        criteria_summary = []
        for cname, stats in criteria_stats.items():
            entry: Dict[str, Any] = {"name": cname, "count": stats["count"]}
            if stats["scores"]:
                entry["average_score"] = round(sum(stats["scores"]) / len(stats["scores"]), 2)
                entry["min_score"] = min(stats["scores"])
                entry["max_score"] = max(stats["scores"])
            criteria_summary.append(entry)

        return {
            "job_id": job_id,
            "name": job["name"],
            "status": job["status"],
            "total_essays": total,
            "graded_essays": len(graded),
            "pending_essays": pending,
            "grade_distribution": grade_distribution,
            "average_grade": average_grade,
            "min_grade": min_grade,
            "max_grade": max_grade,
            "criteria_breakdown": criteria_summary,
        }

    def update_job(
        self,
        job_id: str,
        name: Optional[str] = None,
        rubric: Optional[str] = None,
        class_name: Optional[str] = None,
        assignment_title: Optional[str] = None,
        due_date: Optional[str] = None,
        question_text: Optional[str] = None,
        status: Optional[str] = None,
    ) -> bool:
        """Update job fields. Only provided (non-None) values are changed."""
        updates = []
        params: List[Any] = []

        if name is not None:
            updates.append("name = ?")
            params.append(name)
        if rubric is not None:
            updates.append("rubric = ?")
            params.append(rubric)
        if class_name is not None:
            updates.append("class_name = ?")
            params.append(class_name)
        if assignment_title is not None:
            updates.append("assignment_title = ?")
            params.append(assignment_title)
        if due_date is not None:
            updates.append("due_date = ?")
            params.append(due_date)
        if question_text is not None:
            updates.append("question_text = ?")
            params.append(question_text)
        if status is not None:
            updates.append("status = ?")
            params.append(status)

        if not updates:
            return False

        now = datetime.now().isoformat()
        updates.append("updated_at = ?")
        params.append(now)
        params.append(job_id)

        sql = f"UPDATE regrade_jobs SET {', '.join(updates)} WHERE id = ?"

        cursor = self.db.conn.cursor()
        cursor.execute(sql, params)
        self.db.conn.commit()
        return cursor.rowcount > 0

    def update_essay_review(
        self,
        essay_id: int,
        teacher_grade: Optional[str] = None,
        teacher_comments: Optional[str] = None,
        teacher_annotations: Optional[str] = None,
        status: Optional[str] = None,
    ) -> bool:
        """Update teacher review fields on an essay."""
        updates = []
        params: List[Any] = []

        if teacher_grade is not None:
            updates.append("teacher_grade = ?")
            params.append(teacher_grade)
        if teacher_comments is not None:
            updates.append("teacher_comments = ?")
            params.append(teacher_comments)
        if teacher_annotations is not None:
            updates.append("teacher_annotations = ?")
            params.append(teacher_annotations)
        if status is not None:
            updates.append("status = ?")
            params.append(status)

        if not updates:
            return False

        params.append(essay_id)
        sql = f"UPDATE regrade_essays SET {', '.join(updates)} WHERE id = ?"

        cursor = self.db.conn.cursor()
        cursor.execute(sql, params)
        self.db.conn.commit()
        return cursor.rowcount > 0

    def set_metadata(self, job_id: str, key: str, value: Any) -> bool:
        """Set a key in the job's metadata JSON. Merges with existing metadata."""
        job = self.get_job(job_id)
        if not job:
            return False

        metadata = job.get("metadata") or {}
        if not isinstance(metadata, dict):
            metadata = {}
        metadata[key] = value

        now = datetime.now().isoformat()
        cursor = self.db.conn.cursor()
        cursor.execute(
            "UPDATE regrade_jobs SET metadata = ?, updated_at = ? WHERE id = ?",
            (json.dumps(metadata), now, job_id),
        )
        self.db.conn.commit()
        return cursor.rowcount > 0

    def get_metadata(self, job_id: str, key: str = "") -> Any:
        """Get job metadata. If key is provided, return that key's value. Otherwise return all metadata."""
        job = self.get_job(job_id)
        if not job:
            return None

        metadata = job.get("metadata") or {}
        if not isinstance(metadata, dict):
            return None

        if key:
            return metadata.get(key)
        return metadata

    def get_reviewed_count(self, job_id: str) -> int:
        """Count essays with teacher review (REVIEWED or APPROVED status)."""
        cursor = self.db.conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) as cnt FROM regrade_essays WHERE job_id = ? AND status IN ('REVIEWED', 'APPROVED')",
            (job_id,),
        )
        return cursor.fetchone()["cnt"]

    def close(self):
        self.db.close()
