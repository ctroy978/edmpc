"""
Essay Regrading MCP Server - FastMCP server for regrading essays with RAG-powered evaluation.

Tools for creating regrade jobs, ingesting essays, indexing source materials,
and AI-powered grading with structured feedback.
"""

import json
from pathlib import Path
from typing import List, Optional

from fastmcp import FastMCP

from edmcp_core import KnowledgeBaseManager, load_edmcp_config

from edmcp_regrade.core import RegradeJobManager, Grader, ReportGenerator


# Load environment variables from central .env file
load_edmcp_config()

# Initialize MCP server
mcp = FastMCP("Essay Regrade Server")

# Server directory for paths
SERVER_DIR = Path(__file__).parent
DATA_DIR = SERVER_DIR.parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Lazy initialization of managers
_job_manager: Optional[RegradeJobManager] = None
_kb_manager: Optional[KnowledgeBaseManager] = None
_grader: Optional[Grader] = None
_report_generator: Optional[ReportGenerator] = None


def get_job_manager() -> RegradeJobManager:
    """Get or create the regrade job manager."""
    global _job_manager
    if _job_manager is None:
        db_path = DATA_DIR / "edmcp.db"
        _job_manager = RegradeJobManager(str(db_path))
    return _job_manager


def get_kb_manager() -> KnowledgeBaseManager:
    """Get or create the knowledge base manager."""
    global _kb_manager
    if _kb_manager is None:
        _kb_manager = KnowledgeBaseManager(str(DATA_DIR / "vector_store"))
    return _kb_manager


def get_grader() -> Grader:
    """Get or create the grader."""
    global _grader
    if _grader is None:
        _grader = Grader(get_job_manager(), get_kb_manager())
    return _grader


def get_report_generator() -> ReportGenerator:
    """Get or create the report generator."""
    global _report_generator
    if _report_generator is None:
        _report_generator = ReportGenerator(get_job_manager())
    return _report_generator


# ============================================================================
# Job Management Tools
# ============================================================================


@mcp.tool()
def create_regrade_job(
    name: str,
    rubric: str = "",
    class_name: str = "",
    assignment_title: str = "",
    due_date: str = "",
    question_text: str = "",
) -> str:
    """
    Create a new essay regrading job.

    Args:
        name: Human-readable job name (e.g., "Period 3 - Romeo & Juliet Essay")
        rubric: Full rubric text for grading
        class_name: Class name for filtering (e.g., "English 10 - Period 3")
        assignment_title: Assignment title (e.g., "Romeo & Juliet Thematic Essay")
        due_date: Due date string (e.g., "2026-02-15")
        question_text: The essay question/prompt students were given

    Returns:
        JSON with job_id and confirmation
    """
    manager = get_job_manager()

    job_id = manager.create_job(
        name=name,
        rubric=rubric or None,
        class_name=class_name or None,
        assignment_title=assignment_title or None,
        due_date=due_date or None,
        question_text=question_text or None,
    )

    return json.dumps({
        "status": "success",
        "job_id": job_id,
        "message": f"Created regrade job '{name}' with ID: {job_id}. Next: add essays with add_essays_from_directory or add_essay.",
    })


@mcp.tool()
def get_job(job_id: str) -> str:
    """
    Get detailed information about a regrade job.

    Args:
        job_id: The regrade job ID

    Returns:
        JSON with full job details
    """
    manager = get_job_manager()
    job = manager.get_job(job_id)

    if not job:
        return json.dumps({"status": "error", "message": f"Job not found: {job_id}"})

    return json.dumps({
        "status": "success",
        "job": job,
    })


@mcp.tool()
def list_jobs(
    limit: int = 50,
    offset: int = 0,
    status: str = "",
    class_name: str = "",
    search: str = "",
    include_archived: bool = False,
) -> str:
    """
    List regrade jobs with filtering and pagination.

    Args:
        limit: Maximum number of jobs to return (default: 50)
        offset: Number of jobs to skip for pagination
        status: Filter by status (PENDING, INDEXING, GRADING, READY_FOR_REVIEW)
        class_name: Filter by class name
        search: Search in name, class_name, assignment_title
        include_archived: Include archived jobs (default: False)

    Returns:
        JSON with list of jobs and pagination info
    """
    manager = get_job_manager()
    result = manager.list_jobs(
        limit=limit,
        offset=offset,
        status=status or None,
        class_name=class_name or None,
        search=search or None,
        include_archived=include_archived,
    )

    return json.dumps({
        "status": "success",
        "count": len(result["jobs"]),
        "total": result["total"],
        "limit": result["limit"],
        "offset": result["offset"],
        "jobs": result["jobs"],
    })


@mcp.tool()
def search_jobs(
    query: str,
    start_date: str = "",
    end_date: str = "",
) -> str:
    """
    Search regrade jobs by keyword across job names, class names, student identifiers, and essay content.

    Args:
        query: Search keyword
        start_date: Optional start date filter (ISO format)
        end_date: Optional end date filter (ISO format)

    Returns:
        JSON with matching jobs and snippets
    """
    manager = get_job_manager()
    results = manager.search_jobs(
        query=query,
        start_date=start_date or None,
        end_date=end_date or None,
    )

    return json.dumps({
        "status": "success",
        "count": len(results),
        "jobs": results,
    })


@mcp.tool()
def archive_job(job_id: str) -> str:
    """
    Archive a regrade job (soft delete).

    Args:
        job_id: The regrade job ID to archive

    Returns:
        JSON with status
    """
    manager = get_job_manager()
    archived = manager.archive_job(job_id)

    if not archived:
        return json.dumps({"status": "error", "message": f"Job not found or already archived: {job_id}"})

    return json.dumps({
        "status": "success",
        "message": f"Archived job {job_id}",
    })


@mcp.tool()
def delete_job(job_id: str) -> str:
    """
    Permanently delete a regrade job and all associated essays and knowledge base data.

    Args:
        job_id: The regrade job ID to delete

    Returns:
        JSON with status
    """
    manager = get_job_manager()

    # Delete knowledge base topic if exists
    job = manager.get_job(job_id)
    if job and job.get("knowledge_base_topic"):
        try:
            kb = get_kb_manager()
            kb.delete_topic(job["knowledge_base_topic"])
        except Exception:
            pass

    deleted = manager.delete_job(job_id)

    if not deleted:
        return json.dumps({"status": "error", "message": f"Job not found: {job_id}"})

    return json.dumps({
        "status": "success",
        "message": f"Deleted job {job_id} and all associated data",
    })


# ============================================================================
# Essay Ingestion Tools
# ============================================================================


@mcp.tool()
def add_essays_from_directory(
    job_id: str,
    directory_path: str,
    file_extension: str = ".txt",
) -> str:
    """
    Add essays from a directory to a regrade job. Each file becomes one essay.
    The filename stem (without extension) is used as the student identifier.

    Args:
        job_id: The regrade job ID
        directory_path: Path to directory containing essay files
        file_extension: File extension to look for (default: ".txt")

    Returns:
        JSON with count and student identifiers
    """
    manager = get_job_manager()

    job = manager.get_job(job_id)
    if not job:
        return json.dumps({"status": "error", "message": f"Job not found: {job_id}"})

    dir_path = Path(directory_path)
    if not dir_path.is_dir():
        return json.dumps({"status": "error", "message": f"Directory not found: {directory_path}"})

    # Normalize extension
    if not file_extension.startswith("."):
        file_extension = f".{file_extension}"

    files = sorted(dir_path.glob(f"*{file_extension}"))
    if not files:
        return json.dumps({"status": "warning", "message": f"No {file_extension} files found in {directory_path}"})

    identifiers = []
    errors = []

    for filepath in files:
        try:
            text = filepath.read_text(encoding="utf-8")
            if not text.strip():
                errors.append(f"{filepath.name}: empty file")
                continue

            student_id = filepath.stem
            manager.add_essay(job_id, student_id, text)
            identifiers.append(student_id)
        except Exception as e:
            errors.append(f"{filepath.name}: {str(e)}")

    result = {
        "status": "success",
        "job_id": job_id,
        "essays_added": len(identifiers),
        "student_identifiers": identifiers,
        "message": f"Added {len(identifiers)} essay(s) from {directory_path}",
    }

    if errors:
        result["errors"] = errors

    return json.dumps(result)


@mcp.tool()
def add_essay(
    job_id: str,
    student_identifier: str,
    essay_text: str,
) -> str:
    """
    Add a single essay to a regrade job.

    Args:
        job_id: The regrade job ID
        student_identifier: Student name or identifier
        essay_text: Pre-scrubbed plain text of the essay

    Returns:
        JSON with essay_id
    """
    manager = get_job_manager()

    job = manager.get_job(job_id)
    if not job:
        return json.dumps({"status": "error", "message": f"Job not found: {job_id}"})

    if not essay_text.strip():
        return json.dumps({"status": "error", "message": "Essay text cannot be empty"})

    essay_id = manager.add_essay(job_id, student_identifier, essay_text)

    return json.dumps({
        "status": "success",
        "job_id": job_id,
        "essay_id": essay_id,
        "student_identifier": student_identifier,
        "message": f"Added essay for '{student_identifier}'",
    })


# ============================================================================
# Source Material Tools
# ============================================================================


@mcp.tool()
def add_source_material(
    job_id: str,
    file_paths: List[str],
) -> str:
    """
    Add source/reference materials to a regrade job for RAG-powered grading.
    Materials are indexed and used to provide context during essay evaluation.

    Supports: PDF, TXT, DOCX, MD files

    Args:
        job_id: The regrade job ID
        file_paths: List of file paths to ingest

    Returns:
        JSON with ingestion summary
    """
    manager = get_job_manager()
    kb = get_kb_manager()

    job = manager.get_job(job_id)
    if not job:
        return json.dumps({"status": "error", "message": f"Job not found: {job_id}"})

    # Create or use existing topic
    topic = job.get("knowledge_base_topic") or f"regrade_{job_id}"

    # Update status
    manager.update_status(job_id, "INDEXING")

    try:
        count = kb.ingest_documents(file_paths, topic)
    except Exception as e:
        manager.update_status(job_id, "PENDING")
        return json.dumps({"status": "error", "message": f"Ingestion failed: {e}"})

    if count == 0:
        manager.update_status(job_id, "PENDING")
        return json.dumps({
            "status": "warning",
            "message": "No documents were ingested. Check file paths and formats.",
        })

    # Update job with topic
    manager.set_knowledge_topic(job_id, topic)
    manager.update_status(job_id, "PENDING")

    return json.dumps({
        "status": "success",
        "job_id": job_id,
        "documents_ingested": count,
        "knowledge_base_topic": topic,
        "message": f"Ingested {count} document(s) into knowledge base. Ready for grading with grade_job.",
    })


# ============================================================================
# Grading Tools
# ============================================================================


@mcp.tool()
def grade_job(
    job_id: str,
    model: str = "",
    system_instructions: str = "",
) -> str:
    """
    Grade all pending essays in a regrade job using AI evaluation.
    If source materials were added, RAG-retrieved context is included in the evaluation.

    Args:
        job_id: The regrade job ID
        model: Optional AI model override (default: uses EVALUATION_API_MODEL or XAI_API_MODEL)
        system_instructions: Optional additional instructions for the evaluator

    Returns:
        JSON with grading summary
    """
    grader = get_grader()
    result = grader.grade_job(
        job_id=job_id,
        model=model or None,
        system_instructions=system_instructions or None,
    )

    return json.dumps(result)


# ============================================================================
# Essay Retrieval Tools
# ============================================================================


@mcp.tool()
def get_job_essays(
    job_id: str,
    status: str = "",
    include_text: bool = False,
) -> str:
    """
    Get essays for a regrade job with optional filtering.

    Args:
        job_id: The regrade job ID
        status: Filter by status (PENDING, GRADED)
        include_text: Include full essay text in response (default: False for lighter payload)

    Returns:
        JSON with essay list
    """
    manager = get_job_manager()

    job = manager.get_job(job_id)
    if not job:
        return json.dumps({"status": "error", "message": f"Job not found: {job_id}"})

    essays = manager.get_job_essays(
        job_id,
        status=status or None,
        include_text=include_text,
    )

    return json.dumps({
        "status": "success",
        "job_id": job_id,
        "count": len(essays),
        "essays": essays,
    })


@mcp.tool()
def get_essay_detail(
    job_id: str,
    essay_id: int,
) -> str:
    """
    Get full details for a specific essay including text and evaluation JSON.

    Args:
        job_id: The regrade job ID
        essay_id: The essay ID

    Returns:
        JSON with full essay details and evaluation
    """
    manager = get_job_manager()

    essay = manager.get_essay(essay_id)
    if not essay:
        return json.dumps({"status": "error", "message": f"Essay not found: {essay_id}"})

    if essay["job_id"] != job_id:
        return json.dumps({"status": "error", "message": "Essay does not belong to this job"})

    return json.dumps({
        "status": "success",
        "essay": essay,
    })


# ============================================================================
# Statistics Tools
# ============================================================================


@mcp.tool()
def get_job_statistics(job_id: str) -> str:
    """
    Get grading statistics for a regrade job including grade distribution,
    averages, and per-criteria breakdown.

    Args:
        job_id: The regrade job ID

    Returns:
        JSON with statistics
    """
    manager = get_job_manager()
    stats = manager.get_job_statistics(job_id)

    if not stats:
        return json.dumps({"status": "error", "message": f"Job not found: {job_id}"})

    return json.dumps({
        "status": "success",
        **stats,
    })


# ============================================================================
# Teacher Review Tools (Phase 2)
# ============================================================================


@mcp.tool()
def update_job(
    job_id: str,
    name: str = "",
    rubric: str = "",
    class_name: str = "",
    assignment_title: str = "",
    due_date: str = "",
    question_text: str = "",
    status: str = "",
) -> str:
    """
    Update a regrade job's metadata. Only provided (non-empty) fields are changed.

    Args:
        job_id: The regrade job ID
        name: New job name
        rubric: New rubric text
        class_name: New class name
        assignment_title: New assignment title
        due_date: New due date
        question_text: New essay question/prompt
        status: New status (PENDING, READY_FOR_REVIEW, IN_PROGRESS, FINALIZED)

    Returns:
        JSON with status
    """
    manager = get_job_manager()

    updated = manager.update_job(
        job_id=job_id,
        name=name or None,
        rubric=rubric or None,
        class_name=class_name or None,
        assignment_title=assignment_title or None,
        due_date=due_date or None,
        question_text=question_text or None,
        status=status or None,
    )

    if not updated:
        return json.dumps({"status": "error", "message": f"Job not found or no changes: {job_id}"})

    return json.dumps({
        "status": "success",
        "job_id": job_id,
        "message": "Job updated",
    })


@mcp.tool()
def update_essay_review(
    job_id: str,
    essay_id: int,
    teacher_grade: str = "",
    teacher_comments: str = "",
    teacher_annotations: str = "",
    status: str = "",
) -> str:
    """
    Save teacher review data for an essay. Supports partial updates — only
    provided (non-empty) fields are changed. Call this for auto-save or
    explicit save during teacher review.

    Args:
        job_id: The regrade job ID
        essay_id: The essay ID to update
        teacher_grade: Teacher's grade override (e.g., "B+", "85/100")
        teacher_comments: Teacher's overall comments
        teacher_annotations: JSON string of inline annotations (list of {"selected_text": "...", "comment": "..."})
        status: New essay status (REVIEWED, APPROVED)

    Returns:
        JSON with status
    """
    manager = get_job_manager()

    essay = manager.get_essay(essay_id)
    if not essay:
        return json.dumps({"status": "error", "message": f"Essay not found: {essay_id}"})
    if essay["job_id"] != job_id:
        return json.dumps({"status": "error", "message": "Essay does not belong to this job"})

    # Validate annotations JSON if provided
    if teacher_annotations:
        try:
            json.loads(teacher_annotations)
        except json.JSONDecodeError as e:
            return json.dumps({"status": "error", "message": f"Invalid annotations JSON: {e}"})

    updated = manager.update_essay_review(
        essay_id=essay_id,
        teacher_grade=teacher_grade or None,
        teacher_comments=teacher_comments or None,
        teacher_annotations=teacher_annotations or None,
        status=status or None,
    )

    if not updated:
        return json.dumps({"status": "warning", "message": "No changes made"})

    return json.dumps({
        "status": "success",
        "essay_id": essay_id,
        "message": f"Review saved for essay {essay_id}",
    })


@mcp.tool()
def finalize_job(
    job_id: str,
    refine_comments: bool = True,
    model: str = "",
) -> str:
    """
    Finalize a regrade job. Optionally refines all teacher comments with AI
    to make them more professional and encouraging, then sets status to FINALIZED.

    Args:
        job_id: The regrade job ID
        refine_comments: Whether to AI-refine teacher comments before finalizing (default: True)
        model: Optional AI model override for comment refinement

    Returns:
        JSON with finalization summary
    """
    manager = get_job_manager()

    job = manager.get_job(job_id)
    if not job:
        return json.dumps({"status": "error", "message": f"Job not found: {job_id}"})

    refinement_result = None
    if refine_comments:
        grader = get_grader()
        refinement_result = grader.refine_comments(
            job_id=job_id,
            model=model or None,
        )

    manager.update_status(job_id, "FINALIZED")

    result = {
        "status": "success",
        "job_id": job_id,
        "message": f"Job {job_id} finalized",
    }
    if refinement_result:
        result["refinement"] = refinement_result

    return json.dumps(result)


@mcp.tool()
def refine_essay_comments(
    job_id: str,
    essay_ids: List[int] = [],
    model: str = "",
) -> str:
    """
    Use AI to polish teacher draft comments into professional, encouraging feedback.
    Preserves teacher intent while improving clarity and tone.

    Args:
        job_id: The regrade job ID
        essay_ids: Specific essay IDs to refine (empty = all essays with teacher comments)
        model: Optional AI model override

    Returns:
        JSON with refinement summary
    """
    grader = get_grader()
    result = grader.refine_comments(
        job_id=job_id,
        essay_ids=essay_ids if essay_ids else None,
        model=model or None,
    )

    return json.dumps(result)


@mcp.tool()
def generate_student_report(
    job_id: str,
    essay_id: int,
) -> str:
    """
    Generate a standalone HTML feedback report for a student essay.
    Includes rubric breakdown, teacher comments, annotated essay with
    highlighted passages, and final grade.

    Args:
        job_id: The regrade job ID
        essay_id: The essay ID

    Returns:
        JSON with HTML report content
    """
    generator = get_report_generator()
    result = generator.generate_student_report(job_id, essay_id)

    return json.dumps(result)


if __name__ == "__main__":
    mcp.run()
