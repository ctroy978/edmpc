from pathlib import Path
from typing import Union, Optional
from edmcp_core import DatabaseManager


class JobManager:
    """
    Manages jobs for the OCR-MCP pipeline, handling both DB state and filesystem.
    """

    def __init__(self, base_path: Union[str, Path], db: DatabaseManager):
        self.base_path = Path(base_path)
        self.db = db

    def create_job(
        self,
        job_name: Optional[str] = None,
        rubric: Optional[str] = None,
        question_text: Optional[str] = None,
        essay_format: Optional[str] = None,
        student_count: Optional[int] = None,
        knowledge_base_topic: Optional[str] = None,
    ) -> str:
        """
        Creates a new job:
        1. Creates a record in the database with materials.
        2. Creates the job directory on disk.

        Returns:
            str: The Job ID.
        """
        job_id = self.db.create_job(
            job_name=job_name,
            rubric=rubric,
            question_text=question_text,
            essay_format=essay_format,
            student_count=student_count,
            knowledge_base_topic=knowledge_base_topic,
        )
        job_dir = self.base_path / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        return job_id

    def get_job_directory(self, job_id: str) -> Path:
        """
        Returns the path to the job directory.
        """
        return self.base_path / job_id
