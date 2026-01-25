import json
import sys
import tempfile
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List, Optional, Set
from edmcp_core import DatabaseManager
from edmcp_essay.core.report_generator import ReportGenerator
from edmcp_essay.core.student_roster import StudentRoster
from edmcp_essay.core.email_sender import EmailSender


class EmailerTool:
    """
    Tool for batch emailing student feedback PDFs.
    Orchestrates database access, email lookup, template rendering, and SMTP sending.
    """

    def __init__(
        self,
        db_manager: DatabaseManager,
        report_generator: ReportGenerator,
        student_roster: StudentRoster,
        email_sender: EmailSender
    ):
        """
        Initialize EmailerTool with required dependencies.

        Args:
            db_manager: Database manager for accessing essay records
            report_generator: Report generator for locating PDF files
            student_roster: Student roster for email lookups
            email_sender: Email sender for SMTP operations
        """
        self.db_manager = db_manager
        self.report_generator = report_generator
        self.student_roster = student_roster
        self.email_sender = email_sender

    def _get_log_path(self, job_id: str) -> Path:
        """Get path to email log file for a job"""
        job_dir = self.report_generator._get_job_dir(job_id)
        return job_dir / "email_log.jsonl"

    def _get_skip_list_path(self, job_id: str) -> Path:
        """Get path to skip list file for a job"""
        job_dir = self.report_generator._get_job_dir(job_id)
        return job_dir / "email_skip_list.json"

    def _load_skip_list(self, job_id: str) -> Set[int]:
        """Load set of essay IDs marked to skip for manual delivery"""
        skip_path = self._get_skip_list_path(job_id)
        if not skip_path.exists():
            return set()

        try:
            with open(skip_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return set(data.get("skipped_essay_ids", []))
        except Exception:
            return set()

    def _load_email_log(self, job_id: str) -> Set[str]:
        """
        Load email log and return set of student names who were successfully sent emails.

        Args:
            job_id: Job ID to check

        Returns:
            Set of student names with "SENT" status
        """
        log_path = self._get_log_path(job_id)
        sent_students = set()

        if not log_path.exists():
            return sent_students

        try:
            with open(log_path, "r", encoding="utf-8") as f:
                for line in f:
                    record = json.loads(line.strip())
                    if record.get("status") == "SENT":
                        sent_students.add(record["student_name"])
        except Exception as e:
            print(f"[Emailer] Warning: Could not read email log: {e}", file=sys.stderr)

        return sent_students

    def _write_email_log(self, job_id: str, record: Dict[str, Any]):
        """
        Append a record to the email log file.

        Args:
            job_id: Job ID
            record: Log record to write (will add timestamp)
        """
        log_path = self._get_log_path(job_id)

        # Ensure directory exists
        log_path.parent.mkdir(parents=True, exist_ok=True)

        # Add timestamp
        record["timestamp"] = datetime.utcnow().isoformat() + "Z"

        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")
        except Exception as e:
            print(f"[Emailer] Warning: Could not write to email log: {e}", file=sys.stderr)

    def _get_student_pdf_path(self, job_id: str, student_name: str, essay_id: int) -> Path:
        """
        Locate the PDF file for a specific student.
        First tries to retrieve from database and write to temp file.
        Falls back to filesystem if not in database (for backwards compatibility).

        Args:
            job_id: Job ID
            student_name: Student's full name
            essay_id: Essay database ID

        Returns:
            Path to PDF file (either temp file from DB or filesystem path)
        """
        # Try to get PDF from database first
        pdf_content = self.db_manager.get_student_pdf(essay_id)

        if pdf_content:
            # Write to temp file for email attachment
            safe_name = student_name.replace(' ', '_')
            temp_pdf = tempfile.NamedTemporaryFile(
                mode='wb',
                suffix='.pdf',
                prefix=f'{safe_name}_{essay_id}_',
                delete=False  # We'll clean up manually after email is sent
            )
            temp_pdf.write(pdf_content)
            temp_pdf.close()
            return Path(temp_pdf.name)

        # Fallback to filesystem (for backwards compatibility)
        job_dir = self.report_generator._get_job_dir(job_id)
        pdf_dir = job_dir / "feedback_pdfs"

        # PDF filename format matches ReportGenerator: {student_name}_{essay_id}.pdf
        safe_name = student_name.replace(' ', '_')
        pdf_path = pdf_dir / f"{safe_name}_{essay_id}.pdf"

        return pdf_path

    async def send_feedback_emails(
        self,
        job_id: str,
        subject_template: Optional[str] = None,
        body_template: str = "default_feedback",
        dry_run: bool = False,
        filter_students: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        Main method to send PDF feedback reports to students via email.

        Args:
            job_id: The ID of the graded job to send feedback for
            subject_template: Custom email subject (default: "Your Assignment Feedback")
            body_template: Name of email template to use (default: "default_feedback")
            dry_run: If True, validates emails but doesn't send (default: False)
            filter_students: Optional list of student names to email (default: all)

        Returns:
            Dictionary with summary of sent/failed/skipped emails and log file path
        """
        results = {
            "sent": [],
            "failed": [],
            "skipped": []
        }

        # Load essays from database
        essays = self.db_manager.get_job_essays(job_id)

        if not essays:
            return {
                "status": "error",
                "message": f"No essays found for job {job_id}",
                "total_students": 0,
                "emails_sent": 0,
                "emails_failed": 0,
                "emails_skipped": 0
            }

        # Get job name for email context
        job_info = self.db_manager.get_job(job_id)
        assignment_name = job_info.get("name", job_id) if job_info else job_id

        # Load already-sent emails for idempotency
        already_sent = self._load_email_log(job_id) if not dry_run else set()

        # Load skip list (students marked for manual delivery)
        skip_list = self._load_skip_list(job_id)

        # Process each essay
        for essay in essays:
            student_name = essay.get("student_name", "Unknown Student")
            essay_id = essay.get("id")
            grade = essay.get("grade", "N/A")

            # Skip if marked for manual delivery
            if essay_id in skip_list:
                results["skipped"].append({
                    "student": student_name,
                    "reason": "Marked for manual delivery"
                })
                print(f"[Emailer] SKIP: {student_name} marked for manual delivery", file=sys.stderr)
                continue

            # Skip if filtering and student not in filter list
            if filter_students and student_name not in filter_students:
                continue

            # Skip if already sent (idempotency)
            if student_name in already_sent:
                results["skipped"].append({
                    "student": student_name,
                    "reason": "Email already sent previously"
                })
                print(f"[Emailer] SKIP: Already sent to {student_name}", file=sys.stderr)
                continue

            pdf_path = None
            is_temp_file = False

            try:
                # 1. Email lookup
                email = self.student_roster.get_email_for_student(student_name)
                if not email:
                    results["skipped"].append({
                        "student": student_name,
                        "reason": "No email address in roster"
                    })
                    print(f"[Emailer] SKIP: No email for {student_name}", file=sys.stderr)

                    # Log the skip
                    self._write_email_log(job_id, {
                        "student_name": student_name,
                        "email": None,
                        "status": "SKIPPED",
                        "reason": "No email in roster"
                    })
                    continue

                # 2. PDF lookup (may create temp file from database)
                pdf_path = self._get_student_pdf_path(job_id, student_name, essay_id)
                # Check if this is a temp file (will be in system temp directory)
                is_temp_file = str(pdf_path).startswith(tempfile.gettempdir())

                if not pdf_path.exists():
                    results["failed"].append({
                        "student": student_name,
                        "email": email,
                        "error": f"PDF not found at {pdf_path}"
                    })
                    print(f"[Emailer] ERROR: PDF missing for {student_name} at {pdf_path}", file=sys.stderr)

                    # Log the failure
                    self._write_email_log(job_id, {
                        "student_name": student_name,
                        "email": email,
                        "status": "FAILED",
                        "error": "PDF not found"
                    })
                    continue

                # 3. Prepare email content
                subject = subject_template or f"Your Assignment Feedback - {assignment_name}"

                template_context = {
                    "student_name": student_name,
                    "grade": grade,
                    "assignment_name": assignment_name
                }

                # Render template
                html_body, plain_body = self.email_sender.render_template(
                    body_template,
                    template_context
                )

                # 4. Send email (or dry run)
                if dry_run:
                    results["sent"].append({
                        "student": student_name,
                        "email": email,
                        "dry_run": True
                    })
                    print(f"[Emailer] DRY RUN: Would send to {student_name} ({email})", file=sys.stderr)
                else:
                    success = await self.email_sender.send_email(
                        to_email=email,
                        subject=subject,
                        body_html=html_body,
                        body_plain=plain_body,
                        attachments=[pdf_path]
                    )

                    if success:
                        results["sent"].append({
                            "student": student_name,
                            "email": email
                        })
                        print(f"[Emailer] SENT: {student_name} ({email})", file=sys.stderr)

                        # Log the success
                        self._write_email_log(job_id, {
                            "student_name": student_name,
                            "email": email,
                            "status": "SENT"
                        })
                    else:
                        results["failed"].append({
                            "student": student_name,
                            "email": email,
                            "error": "SMTP send failed"
                        })
                        print(f"[Emailer] FAILED: {student_name} ({email})", file=sys.stderr)

                        # Log the failure
                        self._write_email_log(job_id, {
                            "student_name": student_name,
                            "email": email,
                            "status": "FAILED",
                            "error": "SMTP send failed"
                        })

            except Exception as e:
                results["failed"].append({
                    "student": student_name,
                    "email": email if 'email' in locals() else None,
                    "error": str(e)
                })
                print(f"[Emailer] ERROR: {student_name}: {e}", file=sys.stderr)

                # Log the error
                self._write_email_log(job_id, {
                    "student_name": student_name,
                    "email": email if 'email' in locals() else None,
                    "status": "FAILED",
                    "error": str(e)
                })
                # Continue to next student
            finally:
                # Clean up temp file if it was created from database
                if pdf_path and is_temp_file and pdf_path.exists():
                    try:
                        pdf_path.unlink()
                    except Exception as cleanup_error:
                        print(f"[Emailer] Warning: Could not delete temp file {pdf_path}: {cleanup_error}", file=sys.stderr)

        # Return summary
        return {
            "status": "success" if not results["failed"] else "warning",
            "job_id": job_id,
            "total_students": len(essays),
            "emails_sent": len(results["sent"]),
            "emails_failed": len(results["failed"]),
            "emails_skipped": len(results["skipped"]),
            "details": results,
            "log_file": str(self._get_log_path(job_id))
        }
