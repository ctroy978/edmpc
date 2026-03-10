"""
Google Classroom API helpers.

All functions strip student PII before returning data.
"""

from typing import Any


def list_courses(service) -> list[dict]:
    """
    Return all active courses the authenticated teacher owns/teaches.

    Returns:
        [{id, name}]
    """
    courses = []
    page_token = None
    while True:
        resp = service.courses().list(
            courseStates=["ACTIVE"],
            teacherId="me",
            pageToken=page_token,
        ).execute()
        for c in resp.get("courses", []):
            courses.append({"id": c["id"], "name": c.get("name", "")})
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return courses


def list_coursework(service, course_id: str) -> list[dict]:
    """
    Return coursework items for a course.

    Returns:
        [{id, title, due_date}]
    """
    items = []
    page_token = None
    while True:
        resp = service.courses().courseWork().list(
            courseId=course_id,
            pageToken=page_token,
        ).execute()
        for cw in resp.get("courseWork", []):
            due = cw.get("dueDate")
            if due:
                due_str = f"{due.get('year','?')}-{due.get('month','?'):02}-{due.get('day','?'):02}" if isinstance(due.get('month'), int) else str(due)
            else:
                due_str = None
            items.append({
                "id": cw["id"],
                "title": cw.get("title", ""),
                "due_date": due_str,
            })
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return items


def get_submission_doc_ids(
    classroom_service,
    course_id: str,
    coursework_id: str,
) -> list[str]:
    """
    Return Drive file IDs for all student submissions, with all PII stripped.

    Only Google Docs attachments are returned (other file types are ignored).

    Returns:
        [file_id, ...]
    """
    file_ids: list[str] = []
    page_token = None
    while True:
        resp = classroom_service.courses().courseWork().studentSubmissions().list(
            courseId=course_id,
            courseWorkId=coursework_id,
            pageToken=page_token,
        ).execute()
        for sub in resp.get("studentSubmissions", []):
            submission_history = sub.get("submissionHistory", [])
            # Prefer turned-in attachments
            work = sub.get("assignmentSubmission", {})
            attachments = work.get("attachments", [])
            for att in attachments:
                drive_file = att.get("driveFile")
                if drive_file and drive_file.get("id"):
                    file_ids.append(drive_file["id"])
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    # Deduplicate while preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for fid in file_ids:
        if fid not in seen:
            seen.add(fid)
            unique.append(fid)
    return unique
