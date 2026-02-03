"""
Test Generation MCP Server - FastMCP server for creating tests and answer sheets.

Tools for creating tests, generating questions from teaching materials,
and exporting tests and answer keys.
"""

import base64
import json
from pathlib import Path
from typing import List, Optional

from fastmcp import FastMCP

from edmcp_core import (
    KnowledgeBaseManager,
    load_edmcp_config,
)

from edmcp_testgen.core import TestJobManager, QuestionGenerator, Formatter
from edmcp_testgen.tools import Exporter


# Load environment variables from central .env file
load_edmcp_config()

# Initialize MCP server
mcp = FastMCP("Test Generation Server")

# Server directory for paths
SERVER_DIR = Path(__file__).parent
DATA_DIR = SERVER_DIR.parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Lazy initialization of managers
_job_manager: Optional[TestJobManager] = None
_kb_manager: Optional[KnowledgeBaseManager] = None
_question_generator: Optional[QuestionGenerator] = None
_formatter: Optional[Formatter] = None
_exporter: Optional[Exporter] = None


def get_job_manager() -> TestJobManager:
    """Get or create the test job manager."""
    global _job_manager
    if _job_manager is None:
        db_path = DATA_DIR / "edmcp.db"
        _job_manager = TestJobManager(str(db_path))
    return _job_manager


def get_kb_manager() -> KnowledgeBaseManager:
    """Get or create the knowledge base manager."""
    global _kb_manager
    if _kb_manager is None:
        _kb_manager = KnowledgeBaseManager(str(DATA_DIR / "vector_store"))
    return _kb_manager


def get_question_generator() -> QuestionGenerator:
    """Get or create the question generator."""
    global _question_generator
    if _question_generator is None:
        _question_generator = QuestionGenerator(get_job_manager(), get_kb_manager())
    return _question_generator


def get_formatter() -> Formatter:
    """Get or create the formatter."""
    global _formatter
    if _formatter is None:
        _formatter = Formatter(get_job_manager())
    return _formatter


def get_exporter() -> Exporter:
    """Get or create the exporter."""
    global _exporter
    if _exporter is None:
        _exporter = Exporter(get_job_manager(), get_formatter())
    return _exporter


# ============================================================================
# Job Management Tools
# ============================================================================


@mcp.tool()
def create_test_job(
    name: str,
    description: str = "",
    total_questions: int = 20,
    total_points: float = 100.0,
    difficulty: str = "medium",
    grade_level: str = "",
    mcq_count: int = 0,
    fib_count: int = 0,
    sa_count: int = 0,
    focus_topics: str = "",
    include_word_bank: bool = False,
    include_rubrics: bool = True,
) -> str:
    """
    Create a new test generation job.

    Args:
        name: User-friendly name for the test (e.g., "Chapter 5 Quiz")
        description: Optional description of the test
        total_questions: Total number of questions to generate (default: 20)
        total_points: Total points for the test (default: 100)
        difficulty: Overall difficulty - "easy", "medium", or "hard" (default: medium)
        grade_level: Target grade level (e.g., "8th grade", "high school")
        mcq_count: Number of multiple choice questions (0 = auto-calculate)
        fib_count: Number of fill-in-the-blank questions (0 = auto-calculate)
        sa_count: Number of short answer questions (0 = auto-calculate)
        focus_topics: Comma-separated list of topics to emphasize
        include_word_bank: Whether to include word bank for fill-in-the-blank
        include_rubrics: Whether to include rubrics for short answer questions

    Returns:
        JSON with job_id and confirmation
    """
    manager = get_job_manager()

    # Parse question distribution
    distribution = None
    if mcq_count > 0 or fib_count > 0 or sa_count > 0:
        distribution = {
            "mcq": mcq_count,
            "fib": fib_count,
            "sa": sa_count,
        }
        # Adjust total_questions to match
        total_questions = mcq_count + fib_count + sa_count

    # Parse focus topics
    topics = None
    if focus_topics.strip():
        topics = [t.strip() for t in focus_topics.split(",") if t.strip()]

    # Round total_points to nearest 0.5 for teacher-friendly values
    rounded_total_points = round(total_points * 2) / 2

    job_id = manager.create_job(
        name=name or None,
        description=description or None,
        total_questions=total_questions,
        total_points=rounded_total_points,
        difficulty=difficulty,
        grade_level=grade_level or None,
        question_distribution=distribution,
        focus_topics=topics,
        include_word_bank=include_word_bank,
        include_rubrics=include_rubrics,
    )

    return json.dumps({
        "status": "success",
        "job_id": job_id,
        "message": f"Created test job '{name}' with ID: {job_id}. Next: add materials with add_materials_to_job.",
    })


@mcp.tool()
def update_test_specs(
    job_id: str,
    name: str = "",
    description: str = "",
    total_questions: int = 0,
    total_points: float = 0,
    difficulty: str = "",
    grade_level: str = "",
    mcq_count: int = -1,
    fib_count: int = -1,
    sa_count: int = -1,
    include_word_bank: bool = False,
    include_rubrics: bool = True,
) -> str:
    """
    Update test specifications for a job. Only provided values are updated.

    Args:
        job_id: The job ID to update
        name: New test name
        description: New description
        total_questions: New total question count
        total_points: New total points
        difficulty: New difficulty level
        grade_level: New grade level
        mcq_count: New MCQ count (-1 = don't change)
        fib_count: New FIB count (-1 = don't change)
        sa_count: New SA count (-1 = don't change)
        include_word_bank: Include word bank for FIB
        include_rubrics: Include rubrics for SA

    Returns:
        JSON with status
    """
    manager = get_job_manager()

    distribution = None
    if mcq_count >= 0 or fib_count >= 0 or sa_count >= 0:
        job = manager.get_job(job_id)
        if not job:
            return json.dumps({"status": "error", "message": f"Job not found: {job_id}"})

        current = job.get("question_distribution", {"mcq": 0, "fib": 0, "sa": 0})
        distribution = {
            "mcq": mcq_count if mcq_count >= 0 else current.get("mcq", 0),
            "fib": fib_count if fib_count >= 0 else current.get("fib", 0),
            "sa": sa_count if sa_count >= 0 else current.get("sa", 0),
        }

    # Round total_points to nearest 0.5 for teacher-friendly values
    rounded_total_points = round(total_points * 2) / 2 if total_points > 0 else None

    updated = manager.update_job_specs(
        job_id=job_id,
        name=name or None,
        description=description or None,
        total_questions=total_questions if total_questions > 0 else None,
        total_points=rounded_total_points,
        difficulty=difficulty or None,
        grade_level=grade_level or None,
        question_distribution=distribution,
        include_word_bank=include_word_bank,
        include_rubrics=include_rubrics,
    )

    if not updated:
        return json.dumps({"status": "error", "message": f"Job not found or no changes: {job_id}"})

    return json.dumps({
        "status": "success",
        "job_id": job_id,
        "message": "Test specifications updated",
    })


@mcp.tool()
def get_test_job(job_id: str) -> str:
    """
    Get detailed information about a test job.

    Args:
        job_id: The job ID to retrieve

    Returns:
        JSON with job details including specs, status, and counts
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
def list_test_jobs(
    limit: int = 50,
    offset: int = 0,
    status: str = "",
    search: str = "",
    include_archived: bool = False,
) -> str:
    """
    List test generation jobs with filtering and pagination.

    Args:
        limit: Maximum number of jobs to return (default: 50)
        offset: Number of jobs to skip for pagination
        status: Filter by status (CREATED, MATERIALS_ADDED, GENERATING, COMPLETE)
        search: Search in name and description
        include_archived: Include archived jobs (default: False)

    Returns:
        JSON with list of jobs and pagination info
    """
    manager = get_job_manager()
    result = manager.list_jobs(
        limit=limit,
        offset=offset,
        status=status or None,
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
def archive_test_job(job_id: str) -> str:
    """
    Archive a test job (soft delete).

    Args:
        job_id: The job ID to archive

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
def delete_test_job(job_id: str) -> str:
    """
    Permanently delete a test job and all associated data.

    Args:
        job_id: The job ID to delete

    Returns:
        JSON with status
    """
    manager = get_job_manager()

    # Also delete from knowledge base
    job = manager.get_job(job_id)
    if job and job.get("knowledge_topic"):
        try:
            kb = get_kb_manager()
            kb.delete_topic(job["knowledge_topic"])
        except Exception:
            pass  # Topic may not exist

    deleted = manager.delete_job(job_id)

    if not deleted:
        return json.dumps({"status": "error", "message": f"Job not found: {job_id}"})

    return json.dumps({
        "status": "success",
        "message": f"Deleted job {job_id} and all associated data",
    })


# ============================================================================
# Material Management Tools
# ============================================================================


@mcp.tool()
def add_materials_to_job(job_id: str, file_paths: List[str]) -> str:
    """
    Add teaching materials to a test job. Materials are ingested into the
    knowledge base for question generation.

    Supports: PDF, TXT, DOCX, MD files

    Args:
        job_id: The job ID
        file_paths: List of file paths to add

    Returns:
        JSON with ingestion summary
    """
    manager = get_job_manager()
    kb = get_kb_manager()

    job = manager.get_job(job_id)
    if not job:
        return json.dumps({"status": "error", "message": f"Job not found: {job_id}"})

    # Create or use existing topic
    topic = job.get("knowledge_topic") or f"testgen_{job_id}"

    # Ingest documents
    try:
        count = kb.ingest_documents(file_paths, topic)
    except Exception as e:
        return json.dumps({"status": "error", "message": f"Ingestion failed: {e}"})

    if count == 0:
        return json.dumps({
            "status": "warning",
            "message": "No documents were ingested. Check file paths and formats.",
        })

    # Update job with topic
    manager.set_knowledge_topic(job_id, topic)
    manager.update_status(job_id, "MATERIALS_ADDED")

    # Track materials in database
    materials_added = 0
    materials_errors = []

    for path in file_paths:
        p = Path(path)
        if p.exists():
            try:
                # Get preview
                if p.suffix.lower() == ".txt":
                    with open(p) as f:
                        preview = f.read(500)
                else:
                    preview = f"File: {p.name}"

                manager.add_material(
                    job_id=job_id,
                    file_path=str(p.absolute()),
                    file_name=p.name,
                    content_preview=preview,
                    content_type=p.suffix.lower().lstrip("."),
                )
                materials_added += 1
            except Exception as e:
                materials_errors.append(f"{p.name}: {str(e)}")
        else:
            materials_errors.append(f"{p.name}: file not found at {path}")

    result = {
        "status": "success",
        "job_id": job_id,
        "documents_ingested": count,
        "materials_added": materials_added,
        "knowledge_topic": topic,
        "message": f"Added {count} document(s). Ready to generate test with generate_test.",
    }

    if materials_errors:
        result["materials_errors"] = materials_errors

    return json.dumps(result)


@mcp.tool()
def list_job_materials(job_id: str) -> str:
    """
    List all materials added to a test job.

    Args:
        job_id: The job ID

    Returns:
        JSON with list of materials
    """
    manager = get_job_manager()
    materials = manager.get_job_materials(job_id)

    return json.dumps({
        "status": "success",
        "job_id": job_id,
        "count": len(materials),
        "materials": materials,
    })


@mcp.tool()
def query_job_materials(job_id: str, query: str) -> str:
    """
    Query the ingested materials for a job using RAG.

    Args:
        job_id: The job ID
        query: The question or search query

    Returns:
        JSON with answer from the materials
    """
    manager = get_job_manager()
    kb = get_kb_manager()

    job = manager.get_job(job_id)
    if not job:
        return json.dumps({"status": "error", "message": f"Job not found: {job_id}"})

    topic = job.get("knowledge_topic")
    if not topic:
        return json.dumps({"status": "error", "message": "No materials added to job"})

    try:
        answer = kb.query_knowledge(query, topic)
        return json.dumps({
            "status": "success",
            "job_id": job_id,
            "query": query,
            "answer": answer,
        })
    except Exception as e:
        return json.dumps({"status": "error", "message": f"Query failed: {e}"})


# ============================================================================
# Question Generation Tools
# ============================================================================


@mcp.tool()
def generate_test(job_id: str) -> str:
    """
    Generate all questions for a test based on the job specifications.
    This uses AI to create questions grounded in the added materials.

    Args:
        job_id: The job ID

    Returns:
        JSON with generation summary
    """
    generator = get_question_generator()
    result = generator.generate_all_questions(job_id)

    return json.dumps(result)


@mcp.tool()
def preview_test(job_id: str, organize_by: str = "type") -> str:
    """
    Preview the generated test with all questions.

    Args:
        job_id: The job ID
        organize_by: "type" (group by question type) or "number" (sequential)

    Returns:
        JSON with formatted test preview
    """
    formatter = get_formatter()
    result = formatter.format_test(job_id, organize_by=organize_by)

    return json.dumps(result)


@mcp.tool()
def get_test_questions(job_id: str) -> str:
    """
    Get all questions for a test job with full details.

    Args:
        job_id: The job ID

    Returns:
        JSON with all questions
    """
    manager = get_job_manager()
    questions = manager.get_job_questions(job_id)

    return json.dumps({
        "status": "success",
        "job_id": job_id,
        "count": len(questions),
        "questions": questions,
    })


@mcp.tool()
def regenerate_question(
    job_id: str,
    question_id: int,
    reason: str = "",
    difficulty: str = "",
) -> str:
    """
    Regenerate a specific question with AI.

    Args:
        job_id: The job ID
        question_id: The question ID to regenerate
        reason: Optional reason for regeneration (e.g., "too easy", "unclear wording")
        difficulty: Optional new difficulty level

    Returns:
        JSON with the new question
    """
    generator = get_question_generator()
    result = generator.regenerate_question(
        job_id=job_id,
        question_id=question_id,
        reason=reason or None,
        difficulty=difficulty or None,
    )

    return json.dumps(result)


@mcp.tool()
def approve_question(job_id: str, question_id: int) -> str:
    """
    Mark a question as approved.

    Args:
        job_id: The job ID
        question_id: The question ID to approve

    Returns:
        JSON with status
    """
    manager = get_job_manager()

    question = manager.get_question(question_id)
    if not question:
        return json.dumps({"status": "error", "message": f"Question not found: {question_id}"})

    if question["job_id"] != job_id:
        return json.dumps({"status": "error", "message": "Question does not belong to this job"})

    manager.update_question(question_id, status="APPROVED")

    return json.dumps({
        "status": "success",
        "question_id": question_id,
        "message": "Question approved",
    })


@mcp.tool()
def remove_question(job_id: str, question_id: int) -> str:
    """
    Remove a question from the test.

    Args:
        job_id: The job ID
        question_id: The question ID to remove

    Returns:
        JSON with status
    """
    manager = get_job_manager()

    question = manager.get_question(question_id)
    if not question:
        return json.dumps({"status": "error", "message": f"Question not found: {question_id}"})

    if question["job_id"] != job_id:
        return json.dumps({"status": "error", "message": "Question does not belong to this job"})

    manager.delete_question(question_id)

    return json.dumps({
        "status": "success",
        "question_id": question_id,
        "message": "Question removed",
    })


@mcp.tool()
def adjust_question(
    job_id: str,
    question_id: int,
    question_text: str = "",
    correct_answer: str = "",
    points: float = 0,
) -> str:
    """
    Make minor adjustments to a question (wording, answer, points).

    Args:
        job_id: The job ID
        question_id: The question ID to adjust
        question_text: New question text (empty = no change)
        correct_answer: New correct answer (empty = no change)
        points: New point value (0 = no change)

    Returns:
        JSON with status
    """
    manager = get_job_manager()

    question = manager.get_question(question_id)
    if not question:
        return json.dumps({"status": "error", "message": f"Question not found: {question_id}"})

    if question["job_id"] != job_id:
        return json.dumps({"status": "error", "message": "Question does not belong to this job"})

    # Round points to nearest 0.5 for teacher-friendly values
    rounded_points = None
    if points > 0:
        rounded_points = round(points * 2) / 2

    updated = manager.update_question(
        question_id=question_id,
        question_text=question_text or None,
        correct_answer=correct_answer or None,
        points=rounded_points,
    )

    if not updated:
        return json.dumps({"status": "warning", "message": "No changes made"})

    return json.dumps({
        "status": "success",
        "question_id": question_id,
        "message": "Question adjusted",
    })


# ============================================================================
# Answer Key Tools
# ============================================================================


@mcp.tool()
def get_answer_key(job_id: str, include_rubrics: bool = True) -> str:
    """
    Get the formatted answer key for a test.

    Args:
        job_id: The job ID
        include_rubrics: Include rubrics for short answer questions

    Returns:
        JSON with formatted answer key
    """
    formatter = get_formatter()
    result = formatter.format_answer_key(job_id, include_rubrics=include_rubrics)

    return json.dumps(result)


@mcp.tool()
def update_answer(job_id: str, question_id: int, new_answer: str) -> str:
    """
    Update the correct answer for a question.

    Args:
        job_id: The job ID
        question_id: The question ID
        new_answer: The new correct answer

    Returns:
        JSON with status
    """
    manager = get_job_manager()

    question = manager.get_question(question_id)
    if not question:
        return json.dumps({"status": "error", "message": f"Question not found: {question_id}"})

    if question["job_id"] != job_id:
        return json.dumps({"status": "error", "message": "Question does not belong to this job"})

    manager.update_question(question_id, correct_answer=new_answer)

    return json.dumps({
        "status": "success",
        "question_id": question_id,
        "message": f"Answer updated to: {new_answer}",
    })


@mcp.tool()
def update_rubric(job_id: str, question_id: int, rubric_json: str) -> str:
    """
    Update the rubric for a short answer question.

    Args:
        job_id: The job ID
        question_id: The question ID
        rubric_json: JSON string with rubric data

    Returns:
        JSON with status
    """
    manager = get_job_manager()

    question = manager.get_question(question_id)
    if not question:
        return json.dumps({"status": "error", "message": f"Question not found: {question_id}"})

    if question["job_id"] != job_id:
        return json.dumps({"status": "error", "message": "Question does not belong to this job"})

    if question["question_type"] != "sa":
        return json.dumps({"status": "error", "message": "Rubrics only apply to short answer questions"})

    try:
        rubric = json.loads(rubric_json)
    except json.JSONDecodeError as e:
        return json.dumps({"status": "error", "message": f"Invalid JSON: {e}"})

    manager.update_question(question_id, rubric=rubric)

    return json.dumps({
        "status": "success",
        "question_id": question_id,
        "message": "Rubric updated",
    })


# ============================================================================
# Export Tools
# ============================================================================


@mcp.tool()
def export_test_pdf(job_id: str) -> str:
    """
    Export the test as a PDF file.

    Args:
        job_id: The job ID

    Returns:
        JSON with base64-encoded PDF content
    """
    exporter = get_exporter()

    try:
        pdf_bytes = exporter.export_test_pdf(job_id)
        pdf_base64 = base64.b64encode(pdf_bytes).decode("utf-8")

        return json.dumps({
            "status": "success",
            "job_id": job_id,
            "content_type": "application/pdf",
            "encoding": "base64",
            "data": pdf_base64,
            "size_bytes": len(pdf_bytes),
        })
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


@mcp.tool()
def export_answer_key_pdf(job_id: str, include_rubrics: bool = True) -> str:
    """
    Export the answer key as a PDF file.

    Args:
        job_id: The job ID
        include_rubrics: Include rubrics for short answer questions

    Returns:
        JSON with base64-encoded PDF content
    """
    exporter = get_exporter()

    try:
        pdf_bytes = exporter.export_answer_key_pdf(job_id, include_rubrics=include_rubrics)
        pdf_base64 = base64.b64encode(pdf_bytes).decode("utf-8")

        return json.dumps({
            "status": "success",
            "job_id": job_id,
            "content_type": "application/pdf",
            "encoding": "base64",
            "data": pdf_base64,
            "size_bytes": len(pdf_bytes),
        })
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


@mcp.tool()
def export_to_files(job_id: str, output_dir: str) -> str:
    """
    Export test and answer key to files in a directory.

    Args:
        job_id: The job ID
        output_dir: Directory to save files

    Returns:
        JSON with file paths
    """
    exporter = get_exporter()

    try:
        result = exporter.export_to_files(job_id, output_dir)
        return json.dumps(result)
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


@mcp.tool()
def export_to_bubble_sheet(job_id: str) -> str:
    """
    Export MCQ questions in format suitable for bubble sheet creation.
    Can be used with edmcp-bubble to create grading sheets.

    Args:
        job_id: The job ID

    Returns:
        JSON with MCQ data for bubble sheet
    """
    formatter = get_formatter()
    result = formatter.get_mcq_for_bubble(job_id)

    return json.dumps(result)


# ============================================================================
# Validation & Statistics Tools
# ============================================================================


@mcp.tool()
def validate_test(job_id: str) -> str:
    """
    Validate the test for issues (coverage, difficulty balance, etc.).

    Args:
        job_id: The job ID

    Returns:
        JSON with validation results
    """
    generator = get_question_generator()
    result = generator.validate_coverage(job_id)

    # Add additional validations
    manager = get_job_manager()
    job = manager.get_job(job_id)

    errors = []
    warnings = []

    if result.get("status") == "error":
        errors.append(result.get("message", "Unknown validation error"))
    elif result.get("status") == "warning":
        warnings.append(result.get("message", ""))
    elif job and result.get("status") == "success":
        questions = manager.get_job_questions(job_id)

        # Check if there are any questions at all
        if not questions:
            errors.append("No questions have been generated yet")
        else:
            # Check question count
            expected = job.get("total_questions", 20)
            actual = len(questions)
            if actual < expected:
                warnings.append(f"Only {actual} questions generated, expected {expected}")

            # Check distribution
            dist = job.get("question_distribution", {})
            actual_dist = result.get("by_type", {})
            for q_type, expected_count in dist.items():
                actual_count = actual_dist.get(q_type, 0)
                if actual_count < expected_count:
                    warnings.append(f"Only {actual_count} {q_type.upper()} questions, expected {expected_count}")

            # Check for questions without source references
            no_source = len(questions) - result.get("source_references", 0)
            if no_source > 0:
                warnings.append(f"{no_source} question(s) have no source reference")

    # Determine overall validity
    valid = len(errors) == 0

    result["valid"] = valid
    result["errors"] = errors
    result["warnings"] = warnings

    return json.dumps(result)


@mcp.tool()
def get_test_statistics(job_id: str) -> str:
    """
    Get statistics about the generated test.

    Args:
        job_id: The job ID

    Returns:
        JSON with test statistics
    """
    manager = get_job_manager()
    job = manager.get_job(job_id)

    if not job:
        return json.dumps({"status": "error", "message": f"Job not found: {job_id}"})

    questions = manager.get_job_questions(job_id)

    # Calculate statistics
    by_type = {}
    by_difficulty = {}
    total_points = 0

    for q in questions:
        q_type = q.get("question_type", "unknown")
        by_type[q_type] = by_type.get(q_type, 0) + 1

        diff = q.get("difficulty", "medium")
        by_difficulty[diff] = by_difficulty.get(diff, 0) + 1

        total_points += q.get("points", 1.0)

    return json.dumps({
        "status": "success",
        "job_id": job_id,
        "name": job.get("name"),
        "status": job.get("status"),
        "total_questions": len(questions),
        "total_points": total_points,
        "target_points": job.get("total_points", 100),
        "by_type": by_type,
        "by_difficulty": by_difficulty,
        "materials_count": job.get("material_count", 0),
    })


if __name__ == "__main__":
    mcp.run()
