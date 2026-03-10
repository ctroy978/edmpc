"""
Google Drive API helpers for fetching per-revision text content.

Author information is stripped from all returned data.
"""

import requests
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2.credentials import Credentials


def get_revisions(drive_service, file_id: str) -> list[dict]:
    """
    Return revision metadata for a Drive file, with author stripped.

    Returns:
        [{revision_id, modified_time}]
    """
    revisions = []
    try:
        resp = drive_service.revisions().list(
            fileId=file_id,
            fields="revisions(id,modifiedTime)",
        ).execute()
        for rev in resp.get("revisions", []):
            revisions.append({
                "revision_id": rev["id"],
                "modified_time": rev.get("modifiedTime", ""),
            })
    except Exception:
        # File may not support revision history (non-native Docs)
        pass
    return revisions


def export_revision_text(
    drive_service,
    creds: Credentials,
    file_id: str,
    revision_id: str,
) -> str:
    """
    Export the plain-text content of a specific Drive revision.

    Uses the per-revision exportLinks approach; falls back to current-file
    export if the revision doesn't have an export link.

    Returns:
        Plain text content of the revision (may be empty string on failure).
    """
    # Refresh credentials if needed before making raw HTTP requests
    if creds.expired and creds.refresh_token:
        creds.refresh(GoogleRequest())

    # Try to get per-revision export link
    try:
        rev_meta = drive_service.revisions().get(
            fileId=file_id,
            revisionId=revision_id,
            fields="exportLinks",
        ).execute()
        export_links: dict = rev_meta.get("exportLinks", {})
        text_url = export_links.get("text/plain")
        if text_url:
            headers = {"Authorization": f"Bearer {creds.token}"}
            r = requests.get(text_url, headers=headers, timeout=30)
            if r.status_code == 200:
                return r.text
    except Exception:
        pass

    # Fallback: export current file version as plain text
    try:
        content = drive_service.files().export(
            fileId=file_id,
            mimeType="text/plain",
        ).execute()
        if isinstance(content, bytes):
            return content.decode("utf-8", errors="replace")
        return str(content)
    except Exception:
        return ""
