"""
edmcp-email: Universal email delivery server for student reports.

Sends any report type stored in the central edmcp database to students
via SMTP. Supports idempotency, dry runs, filtering, and DB logging.
"""

import os
import tempfile
from pathlib import Path
from typing import Dict, List, Optional

from dotenv import load_dotenv

# Load credentials from the project-level .env (walks up from this file's location)
load_dotenv(Path(__file__).parent.parent / ".env")

import fastmcp
from edmcp_core.db import DatabaseManager
from edmcp_email.core.email_sender import EmailSender
from edmcp_email.core.emailer import Emailer
from edmcp_email.core.report_fetcher import ReportFetcher
from edmcp_email.core.student_roster import StudentRoster

mcp = fastmcp.FastMCP("edmcp-email")

DB_PATH = os.environ.get("EDMCP_DB_PATH", "../data/edmcp.db")


def _get_db() -> DatabaseManager:
    return DatabaseManager(DB_PATH)


def _get_email_sender() -> EmailSender:
    return EmailSender(
        smtp_host=os.environ.get("SMTP_HOST", "smtp-relay.brevo.com"),
        smtp_port=int(os.environ.get("SMTP_PORT", "587")),
        smtp_user=os.environ.get("SMTP_USER", ""),
        smtp_pass=os.environ.get("SMTP_PASS", ""),
        from_email=os.environ.get("FROM_EMAIL", ""),
        from_name=os.environ.get("FROM_NAME", "Grade Reports"),
        use_tls=os.environ.get("SMTP_USE_TLS", "true").lower() != "false",
    )


def _build_emailer(roster_path: str) -> tuple[DatabaseManager, Emailer]:
    db = _get_db()
    sender = _get_email_sender()
    fetcher = ReportFetcher(db)
    roster = StudentRoster(Path(roster_path))
    emailer = Emailer(db, fetcher, roster, sender)
    return db, emailer


@mcp.tool()
async def send_reports(
    job_id: str,
    report_type: str,
    roster_path: str,
    subject: Optional[str] = None,
    body_template: str = "default_feedback",
    dry_run: bool = False,
    filter_students: Optional[List[str]] = None,
    skip_students: Optional[List[str]] = None,
) -> dict:
    """
    Send reports for all students in a job via email.

    Idempotent — students already marked SENT for this job+report_type are
    automatically skipped. Safe to run multiple times.

    Args:
        job_id: The job ID whose reports to send
        report_type: Report type stored in DB (e.g., 'student_html', 'student_pdf')
        roster_path: Path to directory containing school_names.csv with student emails
        subject: Email subject line (auto-generated from report_type if not provided)
        body_template: Jinja2 template base name (default: 'default_feedback')
        dry_run: If True, log DRY_RUN entries but do not send any emails
        filter_students: If provided, only send to these students (by name)
        skip_students: Students to skip even if they have reports and emails

    Returns:
        Dict with sent, failed, skipped, dry_run counts and per-student details
    """
    db, emailer = _build_emailer(roster_path)
    try:
        results = await emailer.send_reports(
            job_id=job_id,
            report_type=report_type,
            subject=subject,
            body_template=body_template,
            dry_run=dry_run,
            filter_students=filter_students,
            skip_students=skip_students,
        )
        return results
    finally:
        db.close()


@mcp.tool()
def preview_email_campaign(job_id: str, report_type: str, roster_path: str) -> dict:
    """
    Preview who would receive emails without sending anything.

    Returns lists of students categorized as: ready, already_sent,
    missing_email, missing_report.

    Args:
        job_id: The job ID to preview
        report_type: Report type to check (e.g., 'student_html', 'student_pdf')
        roster_path: Path to directory containing school_names.csv

    Returns:
        Dict with categorized student lists and summary counts
    """
    db = _get_db()
    try:
        fetcher = ReportFetcher(db)
        roster = StudentRoster(Path(roster_path))

        already_sent = db.get_sent_students(job_id, report_type)
        essays = db.get_job_essays(job_id)

        ready = []
        already_sent_list = []
        missing_email = []
        missing_report = []

        for essay in essays:
            student_name = essay.get("student_name") or ""
            essay_id = essay.get("id")

            if not student_name:
                continue

            if student_name in already_sent:
                already_sent_list.append(student_name)
                continue

            email_address = roster.get_email_for_student(student_name)
            if not email_address:
                missing_email.append(student_name)
                continue

            report_result = fetcher.fetch_for_student(job_id, report_type, essay_id, student_name)
            if not report_result:
                missing_report.append(student_name)
                continue

            _, filename = report_result
            ready.append({"student": student_name, "email": email_address, "filename": filename})

        return {
            "job_id": job_id,
            "report_type": report_type,
            "summary": {
                "ready": len(ready),
                "already_sent": len(already_sent_list),
                "missing_email": len(missing_email),
                "missing_report": len(missing_report),
            },
            "ready": ready,
            "already_sent": already_sent_list,
            "missing_email": missing_email,
            "missing_report": missing_report,
        }
    finally:
        db.close()


@mcp.tool()
def get_email_log(job_id: str, report_type: Optional[str] = None) -> dict:
    """
    Retrieve email send history for a job.

    Args:
        job_id: The job ID to query
        report_type: Optional filter by report type

    Returns:
        Dict with sent, failed, skipped, dry_run lists and total count
    """
    db = _get_db()
    try:
        entries = db.get_email_log(job_id, report_type)

        sent = [e for e in entries if e["status"] == "SENT"]
        failed = [e for e in entries if e["status"] == "FAILED"]
        skipped = [e for e in entries if e["status"] == "SKIPPED"]
        dry_run = [e for e in entries if e["status"] == "DRY_RUN"]

        return {
            "job_id": job_id,
            "report_type": report_type,
            "total": len(entries),
            "sent": sent,
            "failed": failed,
            "skipped": skipped,
            "dry_run": dry_run,
        }
    finally:
        db.close()


@mcp.tool()
async def send_report_from_file(
    file_path: str,
    student_name: str,
    to_email: str,
    subject: str,
    body_template: str = "default_feedback",
    assignment_name: str = "",
    grade: str = "",
    job_id: Optional[str] = None,
    report_type: str = "manual",
) -> dict:
    """
    Send a single report from a filesystem path, bypassing roster lookup.

    Useful for one-off sends or resending a manually modified file.
    Logs the send if job_id is provided.

    Args:
        file_path: Absolute path to the file to send as attachment
        student_name: Student's full name (used in template and log)
        to_email: Recipient email address
        subject: Email subject line
        body_template: Jinja2 template base name (default: 'default_feedback')
        assignment_name: Assignment name for template context
        grade: Student's grade for template context
        job_id: Optional job ID for logging
        report_type: Report type label for logging (default: 'manual')

    Returns:
        Dict with status and details
    """
    attachment_path = Path(file_path)
    if not attachment_path.exists():
        return {"success": False, "error": f"File not found: {file_path}"}

    sender = _get_email_sender()
    template_context = {
        "student_name": student_name,
        "grade": grade,
        "assignment_name": assignment_name,
        "report_type": report_type,
    }

    try:
        html_body, plain_body = sender.render_template(body_template, template_context)
    except Exception as e:
        return {"success": False, "error": f"Template render error: {e}"}

    success = await sender.send_email(
        to_email=to_email,
        subject=subject,
        body_html=html_body,
        body_plain=plain_body,
        attachments=[attachment_path],
    )

    if job_id:
        db = _get_db()
        try:
            db.log_email(
                job_id=job_id,
                report_type=report_type,
                student_name=student_name,
                status="SENT" if success else "FAILED",
                email_address=to_email,
                reason=None if success else "SMTP send failed",
                subject=subject,
                template_used=body_template,
            )
        finally:
            db.close()

    return {
        "success": success,
        "student": student_name,
        "email": to_email,
        "file": file_path,
    }


@mcp.tool()
async def resend_failed_emails(
    job_id: str,
    report_type: str,
    roster_path: str,
    subject: Optional[str] = None,
    body_template: str = "default_feedback",
    dry_run: bool = False,
) -> dict:
    """
    Retry sending emails that previously FAILED for this job and report type.

    Only retries students with status=FAILED in email_logs. Students with
    status=SENT are never retried.

    Args:
        job_id: The job ID to retry
        report_type: Report type to retry (e.g., 'student_html', 'student_pdf')
        roster_path: Path to directory containing school_names.csv
        subject: Email subject line (auto-generated if not provided)
        body_template: Jinja2 template base name (default: 'default_feedback')
        dry_run: If True, log DRY_RUN entries but do not send emails

    Returns:
        Dict with sent, failed, skipped, dry_run counts and details
    """
    db = _get_db()
    try:
        # Find students with FAILED status
        all_logs = db.get_email_log(job_id, report_type)
        failed_entries = [e for e in all_logs if e["status"] == "FAILED"]

        if not failed_entries:
            return {
                "sent": 0, "failed": 0, "skipped": 0, "dry_run": 0,
                "details": [],
                "message": "No FAILED entries found for this job and report type.",
            }

        # Build filter list from failed student names
        failed_students = [e["student_name"] for e in failed_entries]

        fetcher = ReportFetcher(db)
        sender = _get_email_sender()
        roster = StudentRoster(Path(roster_path))
        emailer = Emailer(db, fetcher, roster, sender)

        # Temporarily override get_sent_students to only skip truly SENT students
        # (not FAILED ones) — this is the default behavior, so just filter to failed
        results = await emailer.send_reports(
            job_id=job_id,
            report_type=report_type,
            subject=subject,
            body_template=body_template,
            dry_run=dry_run,
            filter_students=failed_students,
        )

        results["retried_count"] = len(failed_students)
        return results
    finally:
        db.close()


@mcp.tool()
async def test_smtp_connection() -> dict:
    """
    Test the SMTP connection configured via environment variables.

    Does not send any email. Returns connection status and config details
    (credentials are not included in the response).

    Returns:
        Dict with success status, host, port, and from_email
    """
    sender = _get_email_sender()
    success = await sender.test_connection()
    return {
        "success": success,
        "smtp_host": sender.smtp_host,
        "smtp_port": sender.smtp_port,
        "from_email": sender.from_email,
        "from_name": sender.from_name,
        "use_tls": sender.use_tls,
    }


@mcp.tool()
def list_available_reports(job_id: str) -> dict:
    """
    List all reports available to send for a job.

    Shows what report types and students have content stored in the DB,
    without fetching the actual file bytes. Use this before send_reports
    to confirm reports exist.

    Args:
        job_id: The job ID to inspect

    Returns:
        Dict with report count, types summary, and full report list
    """
    db = _get_db()
    try:
        fetcher = ReportFetcher(db)
        reports = fetcher.list_available_reports(job_id)

        # Summarize by type
        by_type: Dict[str, int] = {}
        for r in reports:
            rt = r["report_type"]
            by_type[rt] = by_type.get(rt, 0) + 1

        return {
            "job_id": job_id,
            "total": len(reports),
            "by_type": by_type,
            "reports": reports,
        }
    finally:
        db.close()


@mcp.tool()
def store_report(
    job_id: str,
    student_name: str,
    content: str,
    report_type: str = "student_html",
    filename: Optional[str] = None,
) -> dict:
    """
    Store a generated report in the central DB for later email delivery.

    Creates a stub essays entry (if needed) and a reports entry.
    Call this before send_reports for reports generated outside the DB pipeline.

    Args:
        job_id: The job this report belongs to
        student_name: Real student name (must match roster for email lookup)
        content: Report content as UTF-8 string (HTML)
        report_type: e.g. 'student_html', 'student_pdf'. Default: 'student_html'
        filename: Attachment filename. Auto-generated from student_name if omitted.

    Returns:
        Dict with essay_id and report_id
    """
    db = _get_db()
    try:
        if not filename:
            safe = student_name.replace(" ", "_")
            ext = ReportFetcher.EXTENSION_MAP.get(report_type, ".bin")
            filename = f"{safe}_feedback{ext}"

        # Get or create stub essays entry for this student
        essays = db.get_job_essays(job_id)
        essay_entry = next((e for e in essays if e["student_name"] == student_name), None)
        if essay_entry:
            essay_id = essay_entry["id"]
        else:
            essay_id = db.add_essay(job_id, student_name, raw_text="")

        content_bytes = content.encode("utf-8")
        report_id = db.store_report(job_id, report_type, filename, content_bytes, essay_id)
        return {
            "status": "success",
            "essay_id": essay_id,
            "report_id": report_id,
            "filename": filename,
        }
    finally:
        db.close()


if __name__ == "__main__":
    mcp.run()
