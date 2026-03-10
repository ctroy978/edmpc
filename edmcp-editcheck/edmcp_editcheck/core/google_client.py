"""
Build authenticated Google API service objects.
"""

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build


def build_classroom_service(creds: Credentials):
    """Return a Google Classroom API v1 resource."""
    return build("classroom", "v1", credentials=creds)


def build_drive_service(creds: Credentials):
    """Return a Google Drive API v3 resource."""
    return build("drive", "v3", credentials=creds)
