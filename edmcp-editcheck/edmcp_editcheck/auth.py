"""
Google OAuth2 credential management for edmcp-editcheck.

Handles the one-time teacher consent flow and persistent token refresh.
Credentials are stored locally in TOKEN_PATH (default: ~/.edmcp/editcheck_token.json).
"""

import json
import os
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

# Read-only access to Classroom and Drive is all we need.
SCOPES = [
    "https://www.googleapis.com/auth/classroom.courses.readonly",
    "https://www.googleapis.com/auth/classroom.coursework.students.readonly",
    "https://www.googleapis.com/auth/classroom.student-submissions.students.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]

DEFAULT_TOKEN_PATH = Path.home() / ".edmcp" / "editcheck_token.json"


def get_credentials(
    client_secrets_path: str | Path | None = None,
    token_path: str | Path | None = None,
) -> Credentials:
    """
    Return valid Google credentials, refreshing or re-authorizing as needed.

    Args:
        client_secrets_path: Path to Google OAuth client_secrets JSON file.
                             Falls back to GOOGLE_CLIENT_SECRETS env var.
        token_path: Where to persist the token. Falls back to EDITCHECK_TOKEN_PATH
                    env var, then ~/.edmcp/editcheck_token.json.

    Returns:
        Valid google.oauth2.credentials.Credentials object.

    Raises:
        FileNotFoundError: If no client_secrets file can be found.
        RuntimeError: If the OAuth flow cannot be completed.
    """
    secrets = Path(
        client_secrets_path
        or os.environ.get("GOOGLE_CLIENT_SECRETS", "")
        or _find_default_secrets()
    )
    token = Path(
        token_path
        or os.environ.get("EDITCHECK_TOKEN_PATH", "")
        or DEFAULT_TOKEN_PATH
    )

    creds: Credentials | None = None

    if token.exists():
        creds = Credentials.from_authorized_user_file(str(token), SCOPES)

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        _save_token(creds, token)
        return creds

    # Full OAuth flow — opens a browser tab on the server host.
    if not secrets.exists():
        raise FileNotFoundError(
            f"Google client secrets file not found: {secrets}\n"
            "Set GOOGLE_CLIENT_SECRETS env var or pass client_secrets_path."
        )

    flow = InstalledAppFlow.from_client_secrets_file(str(secrets), SCOPES)
    creds = flow.run_local_server(port=0)
    _save_token(creds, token)
    return creds


def credentials_are_valid(token_path: str | Path | None = None) -> bool:
    """Return True if stored credentials exist and can be used (valid or refreshable)."""
    token = Path(token_path or os.environ.get("EDITCHECK_TOKEN_PATH", "") or DEFAULT_TOKEN_PATH)
    if not token.exists():
        return False
    try:
        creds = Credentials.from_authorized_user_file(str(token), SCOPES)
        if creds.valid:
            return True
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            _save_token(creds, token)
            return True
    except Exception:
        pass
    return False


def revoke_credentials(token_path: str | Path | None = None) -> None:
    """Delete stored token, requiring a fresh OAuth flow next time."""
    token = Path(token_path or os.environ.get("EDITCHECK_TOKEN_PATH", "") or DEFAULT_TOKEN_PATH)
    if token.exists():
        token.unlink()


def _save_token(creds: Credentials, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(creds.to_json())


def _find_default_secrets() -> str:
    """Look for client_secrets.json in common locations."""
    candidates = [
        Path.cwd() / "client_secrets.json",
        Path(__file__).parent.parent / "client_secrets.json",
        Path.home() / ".edmcp" / "client_secrets.json",
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    return "client_secrets.json"  # will trigger FileNotFoundError upstream
