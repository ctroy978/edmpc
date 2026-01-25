import json
from pathlib import Path
from typing import Dict, Any, List, Optional
from edmcp_core import DatabaseManager
from edmcp_essay.core.student_roster import StudentRoster
from edmcp_essay.core.report_generator import ReportGenerator


class NameFixerTool:
    """
    Tool for identifying and correcting student name mismatches before emailing.
    Designed for multi-turn conversations with LangGraph agents.
    """

    def __init__(
        self,
        db_manager: DatabaseManager,
        student_roster: StudentRoster,
        report_generator: ReportGenerator
    ):
        """
        Initialize NameFixerTool with required dependencies.

        Args:
            db_manager: Database manager for accessing and updating essay records
            student_roster: Student roster for name/email lookups
            report_generator: Report generator for locating PDF files
        """
        self.db_manager = db_manager
        self.student_roster = student_roster
        self.report_generator = report_generator

    def _get_skip_list_path(self, job_id: str) -> Path:
        """Get path to skip list file for a job"""
        job_dir = self.report_generator._get_job_dir(job_id)
        return job_dir / "email_skip_list.json"

    def _load_skip_list(self, job_id: str) -> List[int]:
        """Load list of essay IDs marked to skip"""
        skip_path = self._get_skip_list_path(job_id)
        if not skip_path.exists():
            return []

        try:
            with open(skip_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("skipped_essay_ids", [])
        except Exception:
            return []

    def _save_skip_list(self, job_id: str, essay_ids: List[int]):
        """Save list of essay IDs to skip"""
        skip_path = self._get_skip_list_path(job_id)
        skip_path.parent.mkdir(parents=True, exist_ok=True)

        with open(skip_path, "w", encoding="utf-8") as f:
            json.dump({"skipped_essay_ids": essay_ids}, f, indent=2)

    def identify_email_problems(self, job_id: str) -> Dict[str, Any]:
        """
        Identifies students who cannot be emailed and why.

        This is the first step in the correction workflow. Returns a list of
        students who need teacher intervention before emails can be sent.

        Args:
            job_id: The ID of the graded job to check

        Returns:
            Dictionary with:
            - status: "needs_corrections" if problems found, "ready" if all good
            - students_needing_help: List of problem students with details
            - ready_to_send: Count of students ready to email
            - total_students: Total number of students in job
        """
        essays = self.db_manager.get_job_essays(job_id)

        if not essays:
            return {
                "status": "error",
                "message": f"No essays found for job {job_id}"
            }

        # Load skip list
        skip_list = self._load_skip_list(job_id)

        problems = []
        ready_count = 0

        for essay in essays:
            essay_id = essay.get("id")
            student_name = essay.get("student_name", "Unknown")
            grade = essay.get("grade", "N/A")

            # Skip if already marked for manual delivery
            if essay_id in skip_list:
                continue

            # Check if we can find email for this student
            email = self.student_roster.get_email_for_student(student_name)

            if not email:
                # Determine the specific problem
                student_info = self.student_roster.get_student_info(student_name)

                if student_info and not student_info.email:
                    reason = "Student found in roster but has no email address"
                else:
                    reason = "Student name not found in roster (possible OCR error or typo)"

                problems.append({
                    "essay_id": essay_id,
                    "current_name": student_name,
                    "grade": grade,
                    "reason": reason,
                    "pdf_exists": self._check_pdf_exists(job_id, student_name, essay_id)
                })
            else:
                # Check if PDF exists
                if self._check_pdf_exists(job_id, student_name, essay_id):
                    ready_count += 1
                else:
                    problems.append({
                        "essay_id": essay_id,
                        "current_name": student_name,
                        "grade": grade,
                        "reason": "PDF not generated for this student",
                        "pdf_exists": False
                    })

        return {
            "status": "needs_corrections" if problems else "ready",
            "students_needing_help": problems,
            "ready_to_send": ready_count,
            "total_students": len(essays),
            "skipped_for_manual_delivery": len(skip_list)
        }

    def _check_pdf_exists(self, job_id: str, student_name: str, essay_id: int) -> bool:
        """Check if PDF exists for a student"""
        job_dir = self.report_generator._get_job_dir(job_id)
        pdf_dir = job_dir / "feedback_pdfs"
        safe_name = student_name.replace(' ', '_')
        pdf_path = pdf_dir / f"{safe_name}_{essay_id}.pdf"
        return pdf_path.exists()

    def verify_student_name_correction(
        self,
        job_id: str,
        essay_id: int,
        suggested_name: str
    ) -> Dict[str, Any]:
        """
        Verifies that a suggested name exists in the roster and has an email.

        This is the second step - teacher provides a corrected name, and we
        check if it's valid before applying the correction.

        Args:
            job_id: The ID of the graded job
            essay_id: The essay database ID to correct
            suggested_name: The corrected student name provided by teacher

        Returns:
            Dictionary with:
            - status: "match_found", "no_match", or "no_email"
            - match details if found (name, email, grade, etc.)
            - current_name: The name currently in database
        """
        # Get current essay info
        essays = self.db_manager.get_job_essays(job_id)
        essay = next((e for e in essays if e.get("id") == essay_id), None)

        if not essay:
            return {
                "status": "error",
                "message": f"Essay {essay_id} not found in job {job_id}"
            }

        current_name = essay.get("student_name", "Unknown")
        current_grade = essay.get("grade", "N/A")

        # Look up suggested name in roster
        student_info = self.student_roster.get_student_info(suggested_name)

        if not student_info:
            # Try case-insensitive partial matches
            all_students = self.student_roster.get_all_students()
            suggested_lower = suggested_name.lower()

            possible_matches = [
                (name, info) for name, info in all_students.items()
                if suggested_lower in name or name in suggested_lower
            ]

            if possible_matches:
                return {
                    "status": "no_exact_match",
                    "essay_id": essay_id,
                    "current_name": current_name,
                    "suggested_name": suggested_name,
                    "possible_matches": [
                        {
                            "name": info.full_name,
                            "email": info.email,
                            "grade": info.grade
                        }
                        for name, info in possible_matches[:5]  # Limit to 5
                    ],
                    "message": f"No exact match for '{suggested_name}'. Did you mean one of these?"
                }
            else:
                return {
                    "status": "no_match",
                    "essay_id": essay_id,
                    "current_name": current_name,
                    "suggested_name": suggested_name,
                    "message": f"Student '{suggested_name}' not found in roster"
                }

        # Check if student has email
        if not student_info.email:
            return {
                "status": "no_email",
                "essay_id": essay_id,
                "current_name": current_name,
                "suggested_name": student_info.full_name,
                "student_grade": student_info.grade,
                "message": f"Student '{student_info.full_name}' found but has no email address in roster"
            }

        # Match found!
        return {
            "status": "match_found",
            "essay_id": essay_id,
            "current_name": current_name,
            "current_grade": current_grade,
            "suggested_name": student_info.full_name,
            "email": student_info.email,
            "student_grade": student_info.grade,
            "needs_confirmation": True,
            "message": f"Found: {student_info.full_name} (Grade {student_info.grade}) <{student_info.email}>"
        }

    def apply_student_name_correction(
        self,
        job_id: str,
        essay_id: int,
        confirmed_name: str
    ) -> Dict[str, Any]:
        """
        Applies a confirmed name correction to the database.

        This is the third step - after teacher confirms the match, we update
        the database record. Does NOT re-grade the essay.

        Args:
            job_id: The ID of the graded job
            essay_id: The essay database ID to update
            confirmed_name: The confirmed correct student name

        Returns:
            Dictionary with:
            - status: "success" or "error"
            - essay_id: The updated essay ID
            - old_name: Previous name in database
            - new_name: Updated name
        """
        # Verify the name one more time
        student_info = self.student_roster.get_student_info(confirmed_name)

        if not student_info:
            return {
                "status": "error",
                "message": f"Cannot apply correction: '{confirmed_name}' not found in roster"
            }

        if not student_info.email:
            return {
                "status": "error",
                "message": f"Cannot apply correction: '{confirmed_name}' has no email in roster"
            }

        # Get current essay info
        essays = self.db_manager.get_job_essays(job_id)
        essay = next((e for e in essays if e.get("id") == essay_id), None)

        if not essay:
            return {
                "status": "error",
                "message": f"Essay {essay_id} not found in job {job_id}"
            }

        old_name = essay.get("student_name", "Unknown")

        # Update the database - only change student_name, preserve everything else
        try:
            cursor = self.db_manager.conn.cursor()
            cursor.execute(
                "UPDATE essays SET student_name = ? WHERE id = ?",
                (student_info.full_name, essay_id)
            )
            self.db_manager.conn.commit()

            return {
                "status": "success",
                "essay_id": essay_id,
                "old_name": old_name,
                "new_name": student_info.full_name,
                "email": student_info.email,
                "message": f"Successfully updated essay {essay_id} from '{old_name}' to '{student_info.full_name}'"
            }
        except Exception as e:
            return {
                "status": "error",
                "message": f"Database update failed: {str(e)}"
            }

    def skip_student_email(
        self,
        job_id: str,
        essay_id: int,
        reason: str = "Manual delivery"
    ) -> Dict[str, Any]:
        """
        Marks a student to skip for email delivery (manual delivery instead).

        Use this when teacher can't identify the student or wants to deliver
        the feedback manually.

        Args:
            job_id: The ID of the graded job
            essay_id: The essay database ID to skip
            reason: Reason for skipping (default: "Manual delivery")

        Returns:
            Dictionary with:
            - status: "success"
            - essay_id: The skipped essay ID
            - student_name: Current name in database
            - reason: Why it's being skipped
        """
        # Get current essay info
        essays = self.db_manager.get_job_essays(job_id)
        essay = next((e for e in essays if e.get("id") == essay_id), None)

        if not essay:
            return {
                "status": "error",
                "message": f"Essay {essay_id} not found in job {job_id}"
            }

        student_name = essay.get("student_name", "Unknown")

        # Load existing skip list
        skip_list = self._load_skip_list(job_id)

        # Add to skip list if not already there
        if essay_id not in skip_list:
            skip_list.append(essay_id)
            self._save_skip_list(job_id, skip_list)

        return {
            "status": "success",
            "essay_id": essay_id,
            "student_name": student_name,
            "reason": reason,
            "message": f"Essay {essay_id} ({student_name}) marked for manual delivery"
        }

    def get_skip_list(self, job_id: str) -> Dict[str, Any]:
        """
        Returns list of students marked for manual delivery.

        Args:
            job_id: The ID of the graded job

        Returns:
            Dictionary with list of skipped students
        """
        skip_list = self._load_skip_list(job_id)
        essays = self.db_manager.get_job_essays(job_id)

        skipped_students = []
        for essay_id in skip_list:
            essay = next((e for e in essays if e.get("id") == essay_id), None)
            if essay:
                skipped_students.append({
                    "essay_id": essay_id,
                    "student_name": essay.get("student_name", "Unknown"),
                    "grade": essay.get("grade", "N/A")
                })

        return {
            "status": "success",
            "job_id": job_id,
            "skipped_students": skipped_students,
            "total_skipped": len(skipped_students)
        }
