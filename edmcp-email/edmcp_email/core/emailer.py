import tempfile
from pathlib import Path
from typing import Dict, List, Optional

from edmcp_core.db import DatabaseManager
from edmcp_email.core.report_fetcher import ReportFetcher
from edmcp_email.core.student_roster import StudentRoster
from edmcp_email.core.email_sender import EmailSender


class Emailer:
    """
    Universal orchestrator for sending student reports via email.
    Supports idempotency, dry runs, filtering, and DB logging.
    """

    def __init__(
        self,
        db_manager: DatabaseManager,
        report_fetcher: ReportFetcher,
        student_roster: StudentRoster,
        email_sender: EmailSender,
    ):
        self.db = db_manager
        self.fetcher = report_fetcher
        self.roster = student_roster
        self.sender = email_sender

    async def send_reports(
        self,
        job_id: str,
        report_type: str,
        subject: Optional[str] = None,
        body_template: str = "default_feedback",
        dry_run: bool = False,
        filter_students: Optional[List[str]] = None,
        skip_students: Optional[List[str]] = None,
    ) -> Dict:
        """
        Sends reports for all essays in a job.

        For each essay:
          1. Skip if already in get_sent_students() (idempotency)
          2. Skip if in skip_students or not in filter_students
          3. Look up email via StudentRoster
          4. Fetch report bytes via ReportFetcher
          5. Write to temp file, attach, send, delete temp file
          6. Log result to DB

        Returns:
            Dict with sent, failed, skipped, dry_run counts and details list
        """
        results = {"sent": 0, "failed": 0, "skipped": 0, "dry_run": 0, "details": []}

        # Get job context for subject and template variables
        job_context = self._get_job_context(job_id)
        assignment_name = job_context.get("assignment_title") or job_context.get("name") or ""
        resolved_subject = self._resolve_subject(subject, assignment_name, report_type)

        # Already-sent students (idempotency)
        already_sent = self.db.get_sent_students(job_id, report_type)

        # Normalize filter/skip lists for case-insensitive comparison
        filter_set = {s.lower() for s in filter_students} if filter_students else None
        skip_set = {s.lower() for s in skip_students} if skip_students else set()

        # Get all essays for this job
        essays = self.db.get_job_essays(job_id)

        for essay in essays:
            student_name = essay.get("student_name") or ""
            essay_id = essay.get("id")

            if not student_name:
                continue

            student_name_lower = student_name.lower()

            # Idempotency check
            if student_name in already_sent:
                results["skipped"] += 1
                results["details"].append({
                    "student": student_name,
                    "status": "SKIPPED",
                    "reason": "Already sent",
                })
                continue

            # Filter checks
            if filter_set and student_name_lower not in filter_set:
                results["skipped"] += 1
                results["details"].append({
                    "student": student_name,
                    "status": "SKIPPED",
                    "reason": "Not in filter_students",
                })
                continue

            if student_name_lower in skip_set:
                results["skipped"] += 1
                results["details"].append({
                    "student": student_name,
                    "status": "SKIPPED",
                    "reason": "In skip_students",
                })
                self.db.log_email(
                    job_id=job_id,
                    report_type=report_type,
                    student_name=student_name,
                    status="SKIPPED",
                    reason="In skip_students",
                    subject=resolved_subject,
                    template_used=body_template,
                )
                continue

            # Email lookup
            email_address = self.roster.get_email_for_student(student_name)
            if not email_address:
                results["failed"] += 1
                results["details"].append({
                    "student": student_name,
                    "status": "FAILED",
                    "reason": "No email address found in roster",
                })
                self.db.log_email(
                    job_id=job_id,
                    report_type=report_type,
                    student_name=student_name,
                    status="FAILED",
                    reason="No email address found in roster",
                    subject=resolved_subject,
                    template_used=body_template,
                )
                continue

            # Fetch report
            report_result = self.fetcher.fetch_for_student(job_id, report_type, essay_id, student_name)
            if not report_result:
                results["failed"] += 1
                results["details"].append({
                    "student": student_name,
                    "status": "FAILED",
                    "reason": f"No report found for type '{report_type}'",
                })
                self.db.log_email(
                    job_id=job_id,
                    report_type=report_type,
                    student_name=student_name,
                    status="FAILED",
                    email_address=email_address,
                    reason=f"No report found for type '{report_type}'",
                    subject=resolved_subject,
                    template_used=body_template,
                )
                continue

            content_bytes, filename = report_result

            # Build template context
            student_info = self.roster.get_student_info(student_name)
            grade = essay.get("grade") or ""
            template_context = {
                "student_name": student_name,
                "grade": grade,
                "assignment_name": assignment_name,
                "report_type": report_type,
            }

            if dry_run:
                results["dry_run"] += 1
                results["details"].append({
                    "student": student_name,
                    "status": "DRY_RUN",
                    "email": email_address,
                    "filename": filename,
                })
                self.db.log_email(
                    job_id=job_id,
                    report_type=report_type,
                    student_name=student_name,
                    status="DRY_RUN",
                    email_address=email_address,
                    subject=resolved_subject,
                    template_used=body_template,
                )
                continue

            # Render template
            try:
                html_body, plain_body = self.sender.render_template(body_template, template_context)
            except Exception as e:
                results["failed"] += 1
                results["details"].append({
                    "student": student_name,
                    "status": "FAILED",
                    "reason": f"Template render error: {e}",
                })
                self.db.log_email(
                    job_id=job_id,
                    report_type=report_type,
                    student_name=student_name,
                    status="FAILED",
                    email_address=email_address,
                    reason=f"Template render error: {e}",
                    subject=resolved_subject,
                    template_used=body_template,
                )
                continue

            # Write to temp file, send, clean up
            suffix = Path(filename).suffix or ".bin"
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp.write(content_bytes)
                tmp_path = Path(tmp.name)
            # Rename so the attachment has the correct filename
            named_path = tmp_path.parent / filename
            tmp_path.rename(named_path)

            try:
                success = await self.sender.send_email(
                    to_email=email_address,
                    subject=resolved_subject,
                    body_html=html_body,
                    body_plain=plain_body,
                    attachments=[named_path],
                )
            finally:
                if named_path.exists():
                    named_path.unlink()

            if success:
                results["sent"] += 1
                results["details"].append({
                    "student": student_name,
                    "status": "SENT",
                    "email": email_address,
                    "filename": filename,
                })
                self.db.log_email(
                    job_id=job_id,
                    report_type=report_type,
                    student_name=student_name,
                    status="SENT",
                    email_address=email_address,
                    subject=resolved_subject,
                    template_used=body_template,
                )
            else:
                results["failed"] += 1
                results["details"].append({
                    "student": student_name,
                    "status": "FAILED",
                    "reason": "SMTP send failed",
                    "email": email_address,
                })
                self.db.log_email(
                    job_id=job_id,
                    report_type=report_type,
                    student_name=student_name,
                    status="FAILED",
                    email_address=email_address,
                    reason="SMTP send failed",
                    subject=resolved_subject,
                    template_used=body_template,
                )

        return results

    def _get_job_context(self, job_id: str) -> Dict:
        """
        Tries regrade_jobs first (has assignment_title/class_name),
        falls back to jobs table.
        """
        cursor = self.db.conn.cursor()

        # Try regrade_jobs
        try:
            cursor.execute("SELECT * FROM regrade_jobs WHERE id = ?", (job_id,))
            row = cursor.fetchone()
            if row:
                return dict(row)
        except Exception:
            pass

        # Fall back to jobs table
        job = self.db.get_job(job_id)
        return job or {}

    def _resolve_subject(self, subject: Optional[str], assignment_name: str, report_type: str) -> str:
        """Auto-generates subject from report_type if not provided."""
        if subject:
            return subject

        type_labels = {
            "student_html": "Feedback Report",
            "student_pdf": "Feedback Report (PDF)",
        }
        label = type_labels.get(report_type, "Report")

        if assignment_name:
            return f"Your {label}: {assignment_name}"
        return f"Your {label}"
