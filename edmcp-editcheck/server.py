"""
edmcp-editcheck: FastMCP server for auditing Google Doc edit histories.

Lets a teacher audit Google Classroom assignment submissions for AI/copy-paste
cheating by analyzing Drive revision histories server-side. All student PII is
stripped before any data leaves the core modules.
"""

import os
import threading
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

import fastmcp

from edmcp_editcheck.auth import (
    SCOPES,
    credentials_are_valid,
    get_credentials,
    revoke_credentials,
    DEFAULT_TOKEN_PATH,
    _find_default_secrets,
    _save_token,
)
from edmcp_editcheck.core.google_client import build_classroom_service, build_drive_service
from edmcp_editcheck.core.classroom import (
    list_courses,
    list_coursework,
    get_submission_doc_ids,
)
from edmcp_editcheck.core.drive_revisions import get_revisions, export_revision_text
from edmcp_editcheck.core.diff_analyzer import analyze_submission
from edmcp_editcheck.core.report_builder import build_report

mcp = fastmcp.FastMCP("edmcp-editcheck")

# Module-level auth state shared between start_auth() and check_auth_status()
_auth_state: dict = {"thread": None, "done": False, "error": None}


# ─── Auth Tools ───────────────────────────────────────────────────────────────


@mcp.tool()
def start_auth() -> dict:
    """
    Launch the Google OAuth consent flow in a background thread.

    Opens a browser window on the teacher's desktop. Returns the authorization
    URL for display. Call check_auth_status() to poll for completion.

    Returns:
        {status, message, auth_initiated}
    """
    global _auth_state

    # Already authenticated — nothing to do
    if credentials_are_valid():
        return {
            "status": "success",
            "message": "Already authenticated. No action needed.",
            "auth_initiated": False,
        }

    # A flow is already running
    if _auth_state.get("thread") and _auth_state["thread"].is_alive():
        return {
            "status": "success",
            "message": (
                "OAuth flow already in progress. "
                "Complete the browser consent, then call check_auth_status()."
            ),
            "auth_initiated": False,
        }

    secrets_path = os.environ.get("GOOGLE_CLIENT_SECRETS", "") or _find_default_secrets()

    def _run_flow():
        try:
            from google_auth_oauthlib.flow import InstalledAppFlow
            flow = InstalledAppFlow.from_client_secrets_file(secrets_path, SCOPES)
            creds = flow.run_local_server(port=0)
            token_path = Path(
                os.environ.get("EDITCHECK_TOKEN_PATH", "") or DEFAULT_TOKEN_PATH
            )
            _save_token(creds, token_path)
            _auth_state["done"] = True
            _auth_state["error"] = None
        except Exception as exc:
            _auth_state["done"] = False
            _auth_state["error"] = str(exc)

    _auth_state = {"thread": None, "done": False, "error": None}
    t = threading.Thread(target=_run_flow, daemon=True)
    _auth_state["thread"] = t
    t.start()

    return {
        "status": "success",
        "message": (
            "OAuth flow started. A browser window should open on the teacher's "
            "desktop. Complete the Google consent screen, then call "
            "check_auth_status() to confirm authentication."
        ),
        "auth_initiated": True,
    }


@mcp.tool()
def check_auth_status() -> dict:
    """
    Check whether the teacher has completed the OAuth consent flow.

    Poll this after calling start_auth(). Returns authenticated=true once
    the browser flow is complete and credentials are stored.

    Returns:
        {status, authenticated, message}
    """
    if credentials_are_valid():
        return {
            "status": "success",
            "authenticated": True,
            "message": "Authenticated and credentials are valid.",
        }

    if _auth_state.get("error"):
        return {
            "status": "error",
            "authenticated": False,
            "message": f"OAuth flow failed: {_auth_state['error']}",
        }

    thread = _auth_state.get("thread")
    if thread and thread.is_alive():
        return {
            "status": "success",
            "authenticated": False,
            "message": (
                "OAuth flow is in progress. "
                "Complete the consent in the browser, then poll again."
            ),
        }

    return {
        "status": "success",
        "authenticated": False,
        "message": "Not authenticated. Call start_auth() to begin the OAuth flow.",
    }


@mcp.tool()
def revoke_auth() -> dict:
    """
    Delete stored OAuth token, requiring a fresh login next time.

    Returns:
        {status, message}
    """
    revoke_credentials()
    _auth_state["done"] = False
    return {
        "status": "success",
        "message": "Credentials revoked. Call start_auth() to re-authenticate.",
    }


# ─── Course / Assignment Discovery ────────────────────────────────────────────


@mcp.tool()
def list_courses_and_assignments() -> dict:
    """
    List all active courses and their assignments for the authenticated teacher.

    No student data is included in the response.

    Returns:
        {status, courses: [{course_id, name, assignments: [{coursework_id, title, due_date}]}]}
    """
    if not credentials_are_valid():
        return {
            "status": "error",
            "message": "Not authenticated. Call start_auth() first.",
        }

    try:
        creds = get_credentials()
        classroom = build_classroom_service(creds)

        courses_raw = list_courses(classroom)
        courses_out = []
        for course in courses_raw:
            assignments = list_coursework(classroom, course["id"])
            courses_out.append({
                "course_id": course["id"],
                "name": course["name"],
                "assignments": [
                    {
                        "coursework_id": a["id"],
                        "title": a["title"],
                        "due_date": a["due_date"],
                    }
                    for a in assignments
                ],
            })

        return {"status": "success", "courses": courses_out}

    except Exception as exc:
        return {"status": "error", "message": str(exc)}


# ─── Core Audit Tool ──────────────────────────────────────────────────────────


@mcp.tool()
def audit_assignment(course_id: str, coursework_id: str) -> dict:
    """
    Audit all student submissions for an assignment and return an anonymized report.

    Fetches Drive revision histories, analyzes edit patterns algorithmically,
    and returns flags such as bulk insertion, few revisions, burst editing,
    cold start, and timing anomalies. No student names or emails appear in output.

    Args:
        course_id:      Google Classroom course ID (from list_courses_and_assignments)
        coursework_id:  Coursework item ID (from list_courses_and_assignments)

    Returns:
        Anonymized report dict with summary and per-submission flag lists.
    """
    if not credentials_are_valid():
        return {
            "status": "error",
            "message": "Not authenticated. Call start_auth() first.",
        }

    try:
        creds = get_credentials()
        classroom = build_classroom_service(creds)
        drive = build_drive_service(creds)

        # Get due date for timing checks (no student PII involved)
        due_date_iso: str | None = None
        try:
            cw = classroom.courses().courseWork().get(
                courseId=course_id, id=coursework_id
            ).execute()
            due = cw.get("dueDate")
            due_time = cw.get("dueTime", {})
            if due:
                y = due.get("year", 2000)
                m = due.get("month", 1)
                d = due.get("day", 1)
                h = due_time.get("hours", 23)
                mi = due_time.get("minutes", 59)
                due_date_iso = f"{y:04d}-{m:02d}-{d:02d}T{h:02d}:{mi:02d}:00Z"
        except Exception:
            pass

        # Fetch all submission doc IDs (PII stripped in classroom.py)
        file_ids = get_submission_doc_ids(classroom, course_id, coursework_id)

        if not file_ids:
            return {
                "status": "success",
                "message": "No Google Doc submissions found for this assignment.",
                "summary": {"total_submissions": 0, "total_flags": 0},
                "submissions": [],
            }

        # Analyze each document
        flags_per_doc = []
        for file_id in file_ids:
            revisions = get_revisions(drive, file_id)

            if not revisions:
                # Document with no revision history — treat as single snapshot
                text = export_revision_text(drive, creds, file_id, "head")
                revision_texts = [("", text)] if text else []
            else:
                revision_texts = []
                for rev in revisions:
                    text = export_revision_text(
                        drive, creds, file_id, rev["revision_id"]
                    )
                    revision_texts.append((rev["modified_time"], text))

            flags = analyze_submission(revision_texts, deadline_iso=due_date_iso)
            flags_per_doc.append(flags)

        report = build_report(flags_per_doc, file_ids=file_ids)
        report["course_id"] = course_id
        report["coursework_id"] = coursework_id
        return report

    except Exception as exc:
        return {"status": "error", "message": str(exc)}


if __name__ == "__main__":
    mcp.run()
