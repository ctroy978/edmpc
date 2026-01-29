"""
Bubble Test MCP Server - FastMCP server for bubble sheet test creation and management.

Tools for creating tests, generating bubble sheets, managing answer keys,
and downloading artifacts.
"""

import base64
import json
from typing import Optional

from fastmcp import FastMCP

from edmcp_bubble.core import BubbleTestManager, BubbleSheetGenerator, GradingJobManager


# Initialize MCP server
mcp = FastMCP("Bubble Test Server")

# Lazy initialization of manager, generator, and grading manager
_manager: Optional[BubbleTestManager] = None
_generator: Optional[BubbleSheetGenerator] = None
_grading_manager: Optional[GradingJobManager] = None


def get_manager() -> BubbleTestManager:
    """Get or create the test manager."""
    global _manager
    if _manager is None:
        _manager = BubbleTestManager()
    return _manager


def get_generator() -> BubbleSheetGenerator:
    """Get or create the bubble sheet generator."""
    global _generator
    if _generator is None:
        _generator = BubbleSheetGenerator()
    return _generator


def get_grading_manager() -> GradingJobManager:
    """Get or create the grading job manager."""
    global _grading_manager
    if _grading_manager is None:
        manager = get_manager()
        _grading_manager = GradingJobManager(manager.db)
    return _grading_manager


@mcp.tool()
def create_bubble_test(name: str, description: str = "") -> str:
    """
    Create a new bubble test record.

    Args:
        name: User-friendly test name (e.g., "Week 5 Quiz")
        description: Optional description of the test

    Returns:
        JSON with test_id and status
    """
    manager = get_manager()
    test_id = manager.create_test(name=name, description=description or None)

    return json.dumps({
        "status": "success",
        "test_id": test_id,
        "message": f"Created bubble test '{name}' with ID: {test_id}",
    })


@mcp.tool()
def generate_bubble_sheet(
    test_id: str,
    num_questions: int,
    paper_size: str = "A4",
    id_length: int = 6,
    id_orientation: str = "vertical",
    draw_border: bool = False,
) -> str:
    """
    Generate a bubble sheet PDF and layout for an existing test.

    Args:
        test_id: The test ID to generate sheet for
        num_questions: Number of questions (1-50)
        paper_size: Paper size - "A4" or "LETTER"
        id_length: Number of digits in student ID (4-10)
        id_orientation: Student ID layout - "vertical" or "horizontal"
        draw_border: Whether to draw outer border rectangle

    Returns:
        JSON with status and sheet info
    """
    manager = get_manager()
    generator = get_generator()

    # Verify test exists
    test = manager.get_test(test_id)
    if not test:
        return json.dumps({
            "status": "error",
            "message": f"Test not found: {test_id}",
        })

    try:
        # Generate sheet
        pdf_bytes, layout = generator.generate(
            num_questions=num_questions,
            paper_size=paper_size,
            id_length=id_length,
            id_orientation=id_orientation,
            draw_border=draw_border,
            title=test["name"],
        )

        # Store in database
        sheet_id = manager.store_sheet(
            test_id=test_id,
            pdf_bytes=pdf_bytes,
            layout=layout,
            num_questions=num_questions,
            paper_size=paper_size,
            id_length=id_length,
            id_orientation=id_orientation,
            draw_border=draw_border,
        )

        return json.dumps({
            "status": "success",
            "test_id": test_id,
            "sheet_id": sheet_id,
            "num_questions": num_questions,
            "paper_size": paper_size,
            "message": f"Generated bubble sheet with {num_questions} questions",
        })

    except ValueError as e:
        return json.dumps({
            "status": "error",
            "message": str(e),
        })


@mcp.tool()
def list_bubble_tests(
    limit: int = 50,
    offset: int = 0,
    status: Optional[str] = None,
    search: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    include_archived: bool = False,
    sort_by: str = "created_at",
    sort_order: str = "desc",
) -> str:
    """
    List bubble tests with filtering, sorting, and pagination.

    Args:
        limit: Maximum number of tests to return (default 50)
        offset: Number of tests to skip for pagination (default 0)
        status: Filter by status - CREATED, SHEET_GENERATED, or KEY_ADDED
        search: Search text in test name and description
        date_from: Filter tests created on or after this date (ISO format, e.g., "2026-01-01")
        date_to: Filter tests created on or before this date (ISO format, e.g., "2026-01-31")
        include_archived: Whether to include archived tests (default False)
        sort_by: Field to sort by - "created_at", "name", or "status" (default "created_at")
        sort_order: Sort direction - "asc" or "desc" (default "desc")

    Returns:
        JSON with list of tests and pagination info
    """
    manager = get_manager()
    result = manager.list_tests(
        limit=limit,
        offset=offset,
        status=status,
        search=search,
        date_from=date_from,
        date_to=date_to,
        include_archived=include_archived,
        sort_by=sort_by,
        sort_order=sort_order,
    )

    # Format for display
    formatted = []
    for test in result["tests"]:
        formatted.append({
            "id": test["id"],
            "name": test["name"],
            "description": test.get("description"),
            "created_at": test["created_at"],
            "status": test["status"],
            "has_sheet": test["has_sheet"],
            "has_answer_key": test["has_answer_key"],
            "archived": test.get("archived", False),
        })

    return json.dumps({
        "status": "success",
        "count": len(formatted),
        "total": result["total"],
        "limit": result["limit"],
        "offset": result["offset"],
        "tests": formatted,
    })


@mcp.tool()
def get_bubble_test(test_id: str) -> str:
    """
    Get detailed information about a bubble test.

    Args:
        test_id: The test ID to retrieve

    Returns:
        JSON with test details including sheet and answer key status
    """
    manager = get_manager()
    test = manager.get_test(test_id)

    if not test:
        return json.dumps({
            "status": "error",
            "message": f"Test not found: {test_id}",
        })

    # Get sheet info if exists
    sheet_info = None
    sheet = manager.get_sheet(test_id)
    if sheet:
        sheet_info = {
            "num_questions": sheet["num_questions"],
            "paper_size": sheet["paper_size"],
            "id_length": sheet["id_length"],
            "id_orientation": sheet["id_orientation"],
            "created_at": sheet["created_at"],
        }

    # Get answer key summary if exists
    key_info = None
    key = manager.get_answer_key(test_id)
    if key:
        key_info = {
            "total_questions": len(key["answers"]),
            "total_points": key["total_points"],
            "created_at": key["created_at"],
        }

    return json.dumps({
        "status": "success",
        "test": {
            "id": test["id"],
            "name": test["name"],
            "description": test["description"],
            "created_at": test["created_at"],
            "status": test["status"],
        },
        "sheet": sheet_info,
        "answer_key": key_info,
    })


@mcp.tool()
def download_bubble_sheet_pdf(test_id: str) -> str:
    """
    Download the bubble sheet PDF for a test.

    Args:
        test_id: The test ID

    Returns:
        JSON with base64-encoded PDF content
    """
    manager = get_manager()

    pdf_bytes = manager.get_sheet_pdf(test_id)
    if not pdf_bytes:
        return json.dumps({
            "status": "error",
            "message": f"No bubble sheet found for test: {test_id}",
        })

    # Encode as base64 for transport
    pdf_base64 = base64.b64encode(pdf_bytes).decode("utf-8")

    return json.dumps({
        "status": "success",
        "test_id": test_id,
        "content_type": "application/pdf",
        "encoding": "base64",
        "data": pdf_base64,
    })


@mcp.tool()
def download_bubble_sheet_layout(test_id: str) -> str:
    """
    Download the bubble sheet layout JSON for a test.

    The layout contains coordinates of all bubbles for OMR scanning.

    Args:
        test_id: The test ID

    Returns:
        JSON with layout data
    """
    manager = get_manager()

    layout = manager.get_sheet_layout(test_id)
    if not layout:
        return json.dumps({
            "status": "error",
            "message": f"No bubble sheet found for test: {test_id}",
        })

    return json.dumps({
        "status": "success",
        "test_id": test_id,
        "layout": layout,
    })


@mcp.tool()
def set_answer_key(test_id: str, answers: str) -> str:
    """
    Set or update the answer key for a test.

    Args:
        test_id: The test ID
        answers: JSON string with answer key array, e.g.:
                 '[{"question": "Q1", "answer": "a", "points": 1.0},
                   {"question": "Q2", "answer": "b,c", "points": 2.0}]'
                 - question: Question identifier (e.g., "Q1", "Q01")
                 - answer: Correct answer(s), comma-separated for multiple select
                 - points: Point value for the question (default 1.0)

    Returns:
        JSON with status
    """
    manager = get_manager()

    # Verify test exists
    test = manager.get_test(test_id)
    if not test:
        return json.dumps({
            "status": "error",
            "message": f"Test not found: {test_id}",
        })

    try:
        answers_list = json.loads(answers)
        if not isinstance(answers_list, list):
            raise ValueError("answers must be a JSON array")

        # Validate structure
        for i, ans in enumerate(answers_list):
            if "question" not in ans or "answer" not in ans:
                raise ValueError(f"Answer {i} missing 'question' or 'answer' field")
            # Set default points if not provided
            if "points" not in ans:
                ans["points"] = 1.0

        key_id = manager.set_answer_key(test_id=test_id, answers=answers_list)
        total_points = sum(a.get("points", 1.0) for a in answers_list)

        return json.dumps({
            "status": "success",
            "test_id": test_id,
            "key_id": key_id,
            "total_questions": len(answers_list),
            "total_points": total_points,
            "message": f"Answer key saved with {len(answers_list)} questions",
        })

    except json.JSONDecodeError as e:
        return json.dumps({
            "status": "error",
            "message": f"Invalid JSON: {str(e)}",
        })
    except ValueError as e:
        return json.dumps({
            "status": "error",
            "message": str(e),
        })


@mcp.tool()
def get_answer_key(test_id: str) -> str:
    """
    Retrieve the answer key for a test.

    Args:
        test_id: The test ID

    Returns:
        JSON with answer key data
    """
    manager = get_manager()

    key = manager.get_answer_key(test_id)
    if not key:
        return json.dumps({
            "status": "error",
            "message": f"No answer key found for test: {test_id}",
        })

    return json.dumps({
        "status": "success",
        "test_id": test_id,
        "created_at": key["created_at"],
        "total_points": key["total_points"],
        "answers": key["answers"],
    })


@mcp.tool()
def delete_bubble_test(test_id: str) -> str:
    """
    Delete a bubble test and all associated data.

    Args:
        test_id: The test ID to delete

    Returns:
        JSON with status
    """
    manager = get_manager()

    deleted = manager.delete_test(test_id)
    if not deleted:
        return json.dumps({
            "status": "error",
            "message": f"Test not found: {test_id}",
        })

    return json.dumps({
        "status": "success",
        "message": f"Deleted test {test_id} and all associated data",
    })


@mcp.tool()
def archive_bubble_test(test_id: str) -> str:
    """
    Archive a bubble test (soft delete).

    Archived tests are hidden from normal listings but can be restored.
    Use include_archived=True in list_bubble_tests to see archived tests.

    Args:
        test_id: The test ID to archive

    Returns:
        JSON with status
    """
    manager = get_manager()

    archived = manager.archive_test(test_id)
    if not archived:
        return json.dumps({
            "status": "error",
            "message": f"Test not found or already archived: {test_id}",
        })

    return json.dumps({
        "status": "success",
        "message": f"Archived test {test_id}",
    })


@mcp.tool()
def unarchive_bubble_test(test_id: str) -> str:
    """
    Unarchive a bubble test (restore from archive).

    Args:
        test_id: The test ID to unarchive

    Returns:
        JSON with status
    """
    manager = get_manager()

    unarchived = manager.unarchive_test(test_id)
    if not unarchived:
        return json.dumps({
            "status": "error",
            "message": f"Test not found or not archived: {test_id}",
        })

    return json.dumps({
        "status": "success",
        "message": f"Unarchived test {test_id}",
    })


# ============================================================================
# Grading Tools
# ============================================================================


@mcp.tool()
def create_grading_job(test_id: str) -> str:
    """
    Create a new grading job for a bubble test.

    The test must have status KEY_ADDED (i.e., has a bubble sheet and answer key).

    Args:
        test_id: The test ID to create a grading job for

    Returns:
        JSON with status and job_id
    """
    from edmcp_bubble.core.grading_manager import GradingJobError

    grading_mgr = get_grading_manager()

    try:
        job_id = grading_mgr.create_job(test_id)
        return json.dumps({
            "status": "success",
            "job_id": job_id,
            "message": f"Created grading job {job_id} for test {test_id}",
        })
    except GradingJobError as e:
        return json.dumps({
            "status": "error",
            "message": str(e),
        })


@mcp.tool()
def upload_scans(job_id: str, pdf_base64: str) -> str:
    """
    Upload scanned bubble sheets PDF to a grading job.

    Args:
        job_id: The grading job ID
        pdf_base64: Base64-encoded PDF content containing scanned bubble sheets

    Returns:
        JSON with status and num_pages
    """
    from edmcp_bubble.core.grading_manager import GradingJobError

    grading_mgr = get_grading_manager()

    try:
        pdf_bytes = base64.b64decode(pdf_base64)
    except Exception as e:
        return json.dumps({
            "status": "error",
            "message": f"Invalid base64 encoding: {e}",
        })

    try:
        result = grading_mgr.upload_scans(job_id, pdf_bytes)
        return json.dumps({
            "status": "success",
            "job_id": job_id,
            "num_pages": result["num_pages"],
            "message": f"Uploaded PDF with {result['num_pages']} pages",
        })
    except GradingJobError as e:
        return json.dumps({
            "status": "error",
            "message": str(e),
        })


@mcp.tool()
def process_scans(job_id: str) -> str:
    """
    Process uploaded scans using computer vision to extract student responses.

    This converts the PDF pages to images and uses the bubble sheet layout
    to detect filled bubbles for student IDs and answers.

    Args:
        job_id: The grading job ID

    Returns:
        JSON with status, num_students, and num_errors
    """
    from edmcp_bubble.core.grading_manager import GradingJobError

    grading_mgr = get_grading_manager()

    try:
        result = grading_mgr.process_scans(job_id)
        return json.dumps({
            "status": "success",
            "job_id": job_id,
            "num_students": result["num_students"],
            "num_errors": result["num_errors"],
            "message": f"Processed {result['num_students']} students, {result['num_errors']} errors",
        })
    except GradingJobError as e:
        return json.dumps({
            "status": "error",
            "message": str(e),
        })


@mcp.tool()
def grade_job(job_id: str) -> str:
    """
    Grade all scanned responses against the answer key.

    This applies the test's answer key to all successfully scanned responses,
    calculates scores, and generates a gradebook CSV.

    Args:
        job_id: The grading job ID

    Returns:
        JSON with status and grading statistics (mean_score, min, max, etc.)
    """
    from edmcp_bubble.core.grading_manager import GradingJobError

    grading_mgr = get_grading_manager()

    try:
        stats = grading_mgr.grade_job(job_id)
        return json.dumps({
            "status": "success",
            "job_id": job_id,
            "mean_score": stats["mean_score"],
            "min_score": stats["min_score"],
            "max_score": stats["max_score"],
            "mean_percent": stats["mean_percent"],
            "message": f"Grading complete. Mean: {stats['mean_percent']:.1f}%",
        })
    except GradingJobError as e:
        return json.dumps({
            "status": "error",
            "message": str(e),
        })


@mcp.tool()
def get_grading_job(job_id: str) -> str:
    """
    Get details about a grading job.

    Args:
        job_id: The grading job ID

    Returns:
        JSON with job details including status, counts, and errors
    """
    grading_mgr = get_grading_manager()

    job = grading_mgr.get_job(job_id)
    if not job:
        return json.dumps({
            "status": "error",
            "message": f"Grading job not found: {job_id}",
        })

    return json.dumps({
        "status": "success",
        "job": job,
    })


@mcp.tool()
def list_grading_jobs(test_id: str, limit: int = 20) -> str:
    """
    List grading jobs for a test.

    Args:
        test_id: The test ID
        limit: Maximum number of jobs to return (default 20)

    Returns:
        JSON with list of grading jobs
    """
    grading_mgr = get_grading_manager()

    jobs = grading_mgr.list_jobs(test_id, limit=limit)

    return json.dumps({
        "status": "success",
        "test_id": test_id,
        "count": len(jobs),
        "jobs": jobs,
    })


@mcp.tool()
def download_gradebook(job_id: str) -> str:
    """
    Download the gradebook CSV for a completed grading job.

    Args:
        job_id: The grading job ID

    Returns:
        JSON with base64-encoded CSV content
    """
    grading_mgr = get_grading_manager()

    csv_bytes = grading_mgr.get_gradebook(job_id)
    if not csv_bytes:
        return json.dumps({
            "status": "error",
            "message": f"No gradebook found for job: {job_id}. Ensure grading is complete.",
        })

    csv_base64 = base64.b64encode(csv_bytes).decode("utf-8")

    return json.dumps({
        "status": "success",
        "job_id": job_id,
        "content_type": "text/csv",
        "encoding": "base64",
        "data": csv_base64,
    })


if __name__ == "__main__":
    mcp.run()
