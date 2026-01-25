import sys
import shutil
from datetime import datetime, timedelta
from typing import Optional, List
from pathlib import Path

from edmcp_core import DatabaseManager, KnowledgeBaseManager
from edmcp_essay.core.job_manager import JobManager


class CleanupTool:
    """
    Manages data lifecycle and cleanup operations.
    """
    def __init__(
        self,
        db_manager: DatabaseManager,
        kb_manager: KnowledgeBaseManager,
        job_manager: JobManager
    ):
        self.db_manager = db_manager
        self.kb_manager = kb_manager
        self.job_manager = job_manager

    def cleanup_old_jobs(self, retention_days: int = 210, dry_run: bool = False) -> dict:
        """
        Deletes jobs older than the retention period.

        Args:
            retention_days: Number of days to keep jobs (default: 210 / ~7 months).
            dry_run: If True, lists what would be deleted without acting.

        Returns:
            Summary of deleted (or targeted) jobs.
        """
        cutoff_date = datetime.now() - timedelta(days=retention_days)
        old_jobs = self.db_manager.get_old_jobs(cutoff_date)

        if not old_jobs:
            return {
                "status": "success",
                "message": f"No jobs found older than {retention_days} days.",
                "jobs_count": 0,
                "deleted_jobs": []
            }

        deleted_jobs = []
        errors = []

        print(f"[Cleanup] Found {len(old_jobs)} jobs older than {cutoff_date.date()}", file=sys.stderr)

        for job_id in old_jobs:
            if dry_run:
                deleted_jobs.append(job_id)
                continue

            try:
                # 1. Delete Directory
                job_dir = self.job_manager.get_job_directory(job_id)
                if job_dir.exists():
                    shutil.rmtree(job_dir)

                # 2. Delete DB Record
                self.db_manager.delete_job(job_id)

                deleted_jobs.append(job_id)
                print(f"[Cleanup] Deleted job {job_id}", file=sys.stderr)

            except Exception as e:
                errors.append(f"{job_id}: {str(e)}")
                print(f"[Cleanup] Error deleting {job_id}: {e}", file=sys.stderr)

        return {
            "status": "success" if not errors else "warning",
            "message": f"{'Would delete' if dry_run else 'Deleted'} {len(deleted_jobs)} jobs.",
            "dry_run": dry_run,
            "jobs_count": len(deleted_jobs),
            "deleted_jobs": deleted_jobs,
            "errors": errors if errors else None
        }

    def delete_knowledge_topic(self, topic: str) -> dict:
        """
        Manually deletes a Knowledge Base topic (collection).

        Args:
            topic: The name of the topic to delete.

        Returns:
            Status of the operation.
        """
        success = self.kb_manager.delete_topic(topic)

        if success:
            return {
                "status": "success",
                "message": f"Topic '{topic}' successfully deleted from Knowledge Base."
            }
        else:
            return {
                "status": "warning",
                "message": f"Topic '{topic}' not found."
            }
