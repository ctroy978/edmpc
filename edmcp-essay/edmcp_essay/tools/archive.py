import shutil
import json
import os
from pathlib import Path
from typing import Optional, List
from datetime import datetime

from edmcp_core import DatabaseManager, write_jsonl
from edmcp_essay.core.job_manager import JobManager
from edmcp_essay.core.report_generator import ReportGenerator


class ArchiveTool:
    """
    Tools for discovering and archiving past jobs.
    """
    def __init__(self, db_manager: DatabaseManager, job_manager: JobManager, report_generator: ReportGenerator):
        self.db = db_manager
        self.job_manager = job_manager
        self.reports = report_generator
        self.export_root = Path("data/exports")
        self.export_root.mkdir(parents=True, exist_ok=True)

    def search_past_jobs(self, query: str, start_date: Optional[str] = None, end_date: Optional[str] = None) -> dict:
        """
        Searches for jobs based on content, name, or student.
        """
        results = self.db.search_jobs(query, start_date, end_date)

        if not results:
            return {"status": "success", "message": "No matching jobs found.", "results": []}

        return {
            "status": "success",
            "count": len(results),
            "results": results
        }

    def export_job_archive(self, job_id: str) -> dict:
        """
        Bundles all job data into a comprehensive ZIP archive.
        """
        # 1. Verify Job
        essays = self.db.get_job_essays(job_id)
        if not essays:
            return {"status": "error", "message": f"Job {job_id} not found or empty."}

        # 2. Setup Staging Area
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        staging_dir = self.export_root / f"{job_id}_archive_{timestamp}"
        staging_dir.mkdir(parents=True, exist_ok=True)

        try:
            # 3. Export Evidence (JSONL)
            evidence_dir = staging_dir / "evidence"
            evidence_dir.mkdir()
            # Reconstruct JSONL from DB
            jsonl_path = evidence_dir / "raw_data.jsonl"
            write_jsonl(jsonl_path, essays)

            # 4. Generate Reports
            reports_dir = staging_dir / "reports"
            reports_dir.mkdir()
            csv_path = self.reports.generate_csv_gradebook(job_id, essays)
            # Copy CSV to staging
            shutil.copy2(csv_path, reports_dir / Path(csv_path).name)

            # 5. Generate Feedback PDFs
            feedback_dir = staging_dir / "feedback"
            feedback_dir.mkdir()
            # This generates PDFs in a temp dir, we need to copy them or generate them into our dir
            # ReportGenerator generates into self.reports_dir/{job_id}/
            # We will just copy the directory content if it exists, or generate new ones.
            # Ideally ReportGenerator should accept an output path, but let's re-generate for safety.
            # Actually, ReportGenerator returns the directory path.
            pdf_source_dir = self.reports.generate_student_feedback_pdfs(job_id, essays)
            # Copy all PDFs
            for pdf in Path(pdf_source_dir).glob("*.pdf"):
                shutil.copy2(pdf, feedback_dir / pdf.name)

            # 6. Create Manifest
            manifest_path = staging_dir / "manifest.txt"
            with open(manifest_path, "w") as f:
                f.write(f"Archive Manifest for Job: {job_id}\n")
                f.write(f"Export Date: {datetime.now().isoformat()}\n")
                f.write(f"Total Essays: {len(essays)}\n")
                f.write("-" * 40 + "\n")
                f.write("Chain of Custody:\n")
                # Get job creation date
                # We need to query the job table again or pass it
                # Optimization: just use the first essay's metadata or DB call if critical
                # For now, simplistic view:
                f.write(f"Status check: {[e['status'] for e in essays]}\n")

            # 7. Zip It
            zip_base_name = self.export_root / f"{job_id}_archive"
            archive_path = shutil.make_archive(str(zip_base_name), 'zip', staging_dir)

            # Cleanup staging
            shutil.rmtree(staging_dir)

            return {
                "status": "success",
                "job_id": job_id,
                "archive_path": archive_path,
                "message": f"Archive created at {archive_path}"
            }

        except Exception as e:
            # Cleanup on failure
            if staging_dir.exists():
                shutil.rmtree(staging_dir)
            return {"status": "error", "message": str(e)}
