"""
Core modules for the essay grading workflow.
"""

from edmcp_essay.core.job_manager import JobManager
from edmcp_essay.core.name_loader import NameLoader
from edmcp_essay.core.student_roster import StudentRoster, StudentInfo
from edmcp_essay.core.email_sender import EmailSender
from edmcp_essay.core.report_generator import ReportGenerator
from edmcp_essay.core.prompts import get_evaluation_prompt

__all__ = [
    "JobManager",
    "NameLoader",
    "StudentRoster",
    "StudentInfo",
    "EmailSender",
    "ReportGenerator",
    "get_evaluation_prompt",
]
