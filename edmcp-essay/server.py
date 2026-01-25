#!/usr/bin/env python
"""
FastMCP server implementation for essay grading workflow.
Handles OCR, scrubbing, evaluation, and email distribution of student feedback.
"""

from __future__ import annotations

import base64
import io
import os
import sys
import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional, List, Any

import regex
from fastmcp import FastMCP
from pdf2image import convert_from_path
from openai import (
    OpenAI,
    APITimeoutError,
    APIConnectionError,
    RateLimitError,
    InternalServerError,
)
from dotenv import load_dotenv, find_dotenv

# Import from edmcp_core (shared library)
from edmcp_core import (
    DatabaseManager,
    KnowledgeBaseManager,
    retry_with_backoff,
    extract_json_from_text,
    get_openai_client,
    write_jsonl,
)

# Import from edmcp_essay (essay-specific modules)
from edmcp_essay.core.name_loader import NameLoader
from edmcp_essay.core.job_manager import JobManager
from edmcp_essay.core.prompts import get_evaluation_prompt
from edmcp_essay.core.report_generator import ReportGenerator
from edmcp_essay.core.student_roster import StudentRoster
from edmcp_essay.core.email_sender import EmailSender
from edmcp_essay.tools.scrubber import Scrubber, ScrubberTool
from edmcp_essay.tools.ocr import OCRTool
from edmcp_essay.tools.cleanup import CleanupTool
from edmcp_essay.tools.archive import ArchiveTool
from edmcp_essay.tools.converter import DocumentConverter
from edmcp_essay.tools.emailer import EmailerTool
from edmcp_essay.tools.name_fixer import NameFixerTool

# Define common AI exceptions for retries
AI_RETRIABLE_EXCEPTIONS = (
    APITimeoutError,
    APIConnectionError,
    RateLimitError,
    InternalServerError,
)

# Load environment variables from .env file
load_dotenv(find_dotenv())

# Constants
NAME_HEADER_PATTERN = regex.compile(
    r"(?im)^\s*(?:name|id)\s*[:\-]\s*([\p{L}][\p{L}'-]*(?:\s+[\p{L}][\p{L}'-]*)?)"
)
CONTINUE_HEADER_PATTERN = regex.compile(r"(?im)^\s*continue\s*[:\-]\s*(.+)$")

# Initialize using paths relative to this server.py file
SERVER_DIR = Path(__file__).parent
NAMES_DIR = SERVER_DIR / "edmcp_essay" / "data" / "names"

# Initialize Scrubber and Student Roster
loader = NameLoader(NAMES_DIR)
all_names = loader.load_all_names()
SCRUBBER = Scrubber(all_names)

# Load full student names for detection
STUDENT_ROSTER = loader.load_full_student_names()

# Initialize DB and JobManager
DB_PATH = SERVER_DIR / "edmcp.db"
JOBS_DIR = SERVER_DIR / "data" / "jobs"
DB_MANAGER = DatabaseManager(DB_PATH)
JOB_MANAGER = JobManager(JOBS_DIR, DB_MANAGER)

# Initialize Knowledge Base Manager with OCR tool for PDF ingestion
KB_MANAGER = KnowledgeBaseManager(str(SERVER_DIR / "data" / "vector_store"), ocr_tool_class=OCRTool)

# Initialize Report Generator
REPORT_GENERATOR = ReportGenerator(str(SERVER_DIR / "data" / "reports"), db_manager=DB_MANAGER)

# Initialize Email Components
STUDENT_ROSTER_WITH_EMAILS = StudentRoster(NAMES_DIR)
EMAIL_SENDER = EmailSender(
    smtp_host=os.environ.get("SMTP_HOST", "smtp-relay.brevo.com"),
    smtp_port=int(os.environ.get("SMTP_PORT", "587")),
    smtp_user=os.environ.get("SMTP_USER", ""),
    smtp_pass=os.environ.get("SMTP_PASS", ""),
    from_email=os.environ.get("FROM_EMAIL", ""),
    from_name=os.environ.get("FROM_NAME", "Grade Reports"),
    use_tls=os.environ.get("SMTP_TLS", "true").lower() == "true"
)
EMAILER_TOOL = EmailerTool(DB_MANAGER, REPORT_GENERATOR, STUDENT_ROSTER_WITH_EMAILS, EMAIL_SENDER)

# Initialize Name Fixer Tool
NAME_FIXER_TOOL = NameFixerTool(DB_MANAGER, STUDENT_ROSTER_WITH_EMAILS, REPORT_GENERATOR)

# Initialize Cleanup Tool
CLEANUP_TOOL = CleanupTool(DB_MANAGER, KB_MANAGER, JOB_MANAGER)

# Initialize Archive Tool
ARCHIVE_TOOL = ArchiveTool(DB_MANAGER, JOB_MANAGER, REPORT_GENERATOR)

# Initialize Document Converter
CONVERTER = DocumentConverter()

# Initialize the FastMCP server
mcp = FastMCP("Essay Grading MCP Server")


@dataclass
class PageResult:
    number: int
    text: str
    detected_name: Optional[str]
    continuation_name: Optional[str]


@dataclass
class TestAggregate:
    student_name: str
    start_page: int
    end_page: int
    parts: list[str]

    def append_page(self, text: str, page_number: int) -> None:
        self.parts.append(text)
        if page_number < self.start_page:
            self.start_page = page_number
        if page_number > self.end_page:
            self.end_page = page_number

    def to_json_record(self, original_pdf: str) -> dict:
        return {
            "student_name": self.student_name,
            "text": "\n\n".join(self.parts),
            "metadata": {
                "original_pdf": original_pdf,
                "start_page": self.start_page,
                "end_page": self.end_page,
                "page_count": self.end_page - self.start_page + 1,
            },
        }


def detect_name(text: str) -> Optional[str]:
    """Detect student name in the top portion of the OCR text."""
    lines = text.splitlines()[:10]
    top_section = "\n".join(lines)

    # First, try the traditional "Name:" or "ID:" pattern
    match = NAME_HEADER_PATTERN.search(top_section)
    if match:
        return match.group(1).strip()

    # If no match, check each line against the student roster
    for line in lines:
        normalized_line = regex.sub(r"\s+", " ", line.strip()).casefold()
        if normalized_line in STUDENT_ROSTER:
            return line.strip()

    return None


def detect_continuation_name(text: str) -> Optional[str]:
    """Detect CONTINUE markers that reference the original student name."""
    top_section = "\n".join(text.splitlines()[:10])
    match = CONTINUE_HEADER_PATTERN.search(top_section)
    if match:
        return match.group(1).strip()
    return None


def aggregate_tests(
    pages: Iterable[PageResult], *, unknown_prefix: str = "Unknown Student"
) -> list[TestAggregate]:
    aggregates: list[TestAggregate] = []
    current: Optional[TestAggregate] = None
    unknown_counter = 0
    aggregates_by_name: dict[str, TestAggregate] = {}
    pending_by_name: dict[str, list[PageResult]] = {}

    def normalize_name(name: Optional[str]) -> Optional[str]:
        if not name:
            return None
        collapsed = regex.sub(r"\s+", " ", name).strip()
        if not collapsed:
            return None
        return collapsed.casefold()

    def attach_pending(name_key: Optional[str], aggregate: TestAggregate) -> None:
        if not name_key:
            return
        pending_pages = pending_by_name.pop(name_key, [])
        for pending_page in sorted(pending_pages, key=lambda item: item.number):
            aggregate.append_page(pending_page.text, pending_page.number)

    for page in pages:
        if page.continuation_name:
            continuation_key = normalize_name(page.continuation_name)
            target = (
                aggregates_by_name.get(continuation_key) if continuation_key else None
            )
            if target is not None:
                target.append_page(page.text, page.number)
            else:
                if continuation_key:
                    pending_by_name.setdefault(continuation_key, []).append(page)
                else:
                    unknown_counter += 1
                    aggregate = TestAggregate(
                        student_name=f"{unknown_prefix} {unknown_counter:02d}",
                        start_page=page.number,
                        end_page=page.number,
                        parts=[page.text],
                    )
                    aggregates.append(aggregate)
            continue

        if page.detected_name:
            if current is not None:
                aggregates.append(current)
            current = TestAggregate(
                student_name=page.detected_name,
                start_page=page.number,
                end_page=page.number,
                parts=[page.text],
            )
            name_key = normalize_name(page.detected_name)
            if name_key:
                aggregates_by_name[name_key] = current
                attach_pending(name_key, current)
            continue

        if current is None:
            unknown_counter += 1
            current = TestAggregate(
                student_name=f"{unknown_prefix} {unknown_counter:02d}",
                start_page=page.number,
                end_page=page.number,
                parts=[page.text],
            )
        else:
            current.append_page(page.text, page.number)

    if current is not None:
        aggregates.append(current)

    for pending_key, pending_pages in pending_by_name.items():
        pending_pages.sort(key=lambda item: item.number)
        continuation_label = pending_pages[0].continuation_name
        if not continuation_label:
            unknown_counter += 1
            continuation_label = f"{unknown_prefix} {unknown_counter:02d}"
        aggregate = TestAggregate(
            student_name=continuation_label,
            start_page=pending_pages[0].number,
            end_page=pending_pages[0].number,
            parts=[],
        )
        for pending_page in pending_pages:
            aggregate.append_page(pending_page.text, pending_page.number)
        aggregates.append(aggregate)
    return aggregates


@retry_with_backoff(retries=3, exceptions=AI_RETRIABLE_EXCEPTIONS)
def _call_chat_completion(
    client: OpenAI, model: str, messages: List[Any], **kwargs: Any
) -> Any:
    """Helper to call OpenAI chat completions with retry logic."""
    return client.chat.completions.create(
        model=model,
        messages=messages,
        **kwargs,
    )


@retry_with_backoff(retries=3, exceptions=AI_RETRIABLE_EXCEPTIONS)
def ocr_image_with_qwen(
    client: OpenAI, image_bytes: bytes, model: Optional[str] = None
) -> str:
    model = model or os.environ.get("QWEN_API_MODEL") or "qwen-vl-max"
    base64_image = base64.b64encode(image_bytes).decode("utf-8")

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "Extract all text from this document image. Return only the text found in the image. Do not add any introductory or concluding remarks.",
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{base64_image}"
                            },
                        },
                    ],
                }
            ],
        )
        return response.choices[0].message.content or ""
    except Exception as e:
        raise RuntimeError(f"Qwen OCR failed: {str(e)}")


def _process_pdf_core(
    pdf_path: str,
    dpi: int = 220,
    model: Optional[str] = None,
    unknown_label: str = "Unknown Student",
    scrub: bool = True,
) -> dict:
    """Core logic to process a PDF and return raw results."""
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"File not found: {pdf_path}")

    page_results = []
    used_ocr = False

    # Try native text extraction first
    extracted_texts = OCRTool.extract_text_from_pdf(pdf_path)

    if extracted_texts:
        for i, text in enumerate(extracted_texts, 1):
            name = detect_name(text)
            continuation = detect_continuation_name(text)

            final_text = text
            if scrub:
                final_text = SCRUBBER.scrub_text(text)

            page_results.append(
                PageResult(
                    number=i,
                    text=final_text,
                    detected_name=name,
                    continuation_name=continuation,
                )
            )
    else:
        # Fallback to OCR
        used_ocr = True
        client = get_openai_client(
            api_key=os.environ.get("QWEN_API_KEY"), base_url=os.environ.get("QWEN_BASE_URL")
        )

        images = convert_from_path(pdf_path, dpi=dpi)

        for i, image in enumerate(images, 1):
            buffered = io.BytesIO()
            image.convert("RGB").save(buffered, format="JPEG", quality=85)
            image_bytes = buffered.getvalue()

            text = ocr_image_with_qwen(client, image_bytes, model=model)

            name = detect_name(text)
            continuation = detect_continuation_name(text)

            final_text = text
            if scrub:
                final_text = SCRUBBER.scrub_text(text)

            page_results.append(
                PageResult(
                    number=i,
                    text=final_text,
                    detected_name=name,
                    continuation_name=continuation,
                )
            )

    aggregates = aggregate_tests(page_results, unknown_prefix=unknown_label)
    results_json = [agg.to_json_record(pdf_path) for agg in aggregates]

    processing_method = "OCR (scanned/image PDF)" if used_ocr else "Fast text extraction (typed/digital PDF)"

    return {
        "status": "success",
        "file": pdf_path,
        "processing_method": processing_method,
        "used_ocr": used_ocr,
        "total_pages": len(page_results),
        "students_found": len(aggregates),
        "results": results_json,
    }


# ============================================================================
# MCP Tools
# ============================================================================

@mcp.tool
def create_job_with_materials(
    rubric: str,
    job_name: Optional[str] = None,
    question_text: Optional[str] = None,
    essay_format: Optional[str] = None,
    student_count: Optional[int] = None,
    knowledge_base_topic: Optional[str] = None,
) -> dict:
    """
    Creates a new grading job and stores all materials (rubric, question, metadata) in the database.
    This is the first step in the grading workflow - call this before batch_process_documents.

    Args:
        rubric: The complete grading rubric text
        job_name: Optional name for this grading job
        question_text: The essay question/prompt (optional)
        essay_format: Either "handwritten" or "typed" (optional)
        student_count: Expected number of students (optional)
        knowledge_base_topic: Topic name if reading materials were added to knowledge base (optional)

    Returns:
        Dictionary with job_id and confirmation message
    """
    try:
        job_id = JOB_MANAGER.create_job(
            job_name=job_name,
            rubric=rubric,
            question_text=question_text,
            essay_format=essay_format,
            student_count=student_count,
            knowledge_base_topic=knowledge_base_topic,
        )

        return {
            "status": "success",
            "job_id": job_id,
            "job_name": job_name,
            "essay_format": essay_format,
            "student_count": student_count,
            "knowledge_base_topic": knowledge_base_topic,
            "message": f"Job created: {job_id}. Materials stored in database. Ready for essay processing.",
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


@mcp.tool
def process_pdf_document(
    pdf_path: str,
    dpi: int = 220,
    model: Optional[str] = None,
    unknown_label: str = "Unknown Student",
) -> dict:
    """
    Process a single PDF document using the fastest available method.
    WARNING: Use this only for individual files. For batches, use batch_process_documents.

    Args:
        pdf_path: Path to the PDF file to process
        dpi: DPI for OCR image conversion (default: 220)
        model: Qwen model to use for OCR fallback
        unknown_label: Label for students without detected names

    Returns:
        Dictionary containing extracted text, student data, and processing method used.
    """
    try:
        return _process_pdf_core(pdf_path, dpi, model, unknown_label)
    except Exception as e:
        return {"status": "error", "error": str(e)}


@mcp.tool
def extract_text_from_image(image_path: str, model: Optional[str] = None) -> dict:
    """
    Extract text from a single image file using Qwen AI OCR.

    Args:
        image_path: Path to the image file
        model: Qwen model to use (default: env QWEN_API_MODEL or qwen-vl-max)

    Returns:
        Dictionary with extracted text
    """
    if not os.path.exists(image_path):
        return {"status": "error", "message": f"File not found: {image_path}"}

    try:
        client = get_openai_client(
            api_key=os.environ.get("QWEN_API_KEY"),
            base_url=os.environ.get("QWEN_BASE_URL"),
        )

        with open(image_path, "rb") as f:
            image_bytes = f.read()

        text = ocr_image_with_qwen(client, image_bytes, model=model)

        return {"status": "success", "extracted_text": text, "source": image_path}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def _batch_process_documents_core(
    directory_path: str,
    model: Optional[str] = None,
    dpi: int = 220,
    job_name: Optional[str] = None,
    job_id: Optional[str] = None,
) -> dict:
    """Core logic for batch processing documents."""
    input_path = Path(directory_path)
    if not input_path.exists():
        return {"status": "error", "message": f"Directory not found: {directory_path}"}

    if job_id:
        job_info = DB_MANAGER.get_job(job_id)
        if not job_info:
            return {"status": "error", "message": f"Job not found: {job_id}"}
    else:
        job_id = JOB_MANAGER.create_job(job_name=job_name)

    job_dir = JOB_MANAGER.get_job_directory(job_id)
    internal_jsonl = job_dir / "ocr_results.jsonl"

    files_processed = 0
    files_using_ocr = 0
    files_using_text_extraction = 0
    errors = []

    files = sorted(list(input_path.glob("*.[pP][dD][fF]")))

    if not files:
        return {
            "status": "warning",
            "message": f"No PDF files found in {directory_path}.",
        }

    print(f"[OCR-MCP] Starting Job {job_id}: Found {len(files)} files", file=sys.stderr)

    ocr_tool = OCRTool(job_dir=job_dir, job_id=job_id, db_manager=DB_MANAGER, student_roster=STUDENT_ROSTER)

    for file_path in files:
        try:
            print(f"[OCR-MCP] Processing {file_path.name}...", file=sys.stderr)
            result = ocr_tool.process_pdf(file_path, dpi=dpi)

            if result.get("used_ocr"):
                files_using_ocr += 1
            else:
                files_using_text_extraction += 1

            files_processed += 1

        except Exception as e:
            error_msg = f"{file_path.name}: {str(e)}"
            print(f"[OCR-MCP] Error processing {file_path.name}: {e}", file=sys.stderr)
            errors.append(error_msg)

    essays = DB_MANAGER.get_job_essays(job_id)
    students_found = len(essays)

    print(f"[OCR-MCP] Job {job_id} Completed. {files_processed}/{len(files)} files processed.", file=sys.stderr)

    method_summary = []
    if files_using_text_extraction > 0:
        method_summary.append(f"{files_using_text_extraction} via fast text extraction")
    if files_using_ocr > 0:
        method_summary.append(f"{files_using_ocr} via OCR")
    method_str = " and ".join(method_summary) if method_summary else "unknown method"

    return {
        "status": "success",
        "job_id": job_id,
        "job_name": job_name,
        "students_detected": students_found,
        "summary": f"Processed {files_processed} files ({method_str}). Found {students_found} student records.",
        "processing_details": {
            "total_files": files_processed,
            "text_extraction": files_using_text_extraction,
            "ocr": files_using_ocr,
        },
        "output_file": str(internal_jsonl.absolute()),
        "errors": errors if errors else None,
    }


@mcp.tool
def batch_process_documents(
    directory_path: str,
    model: Optional[str] = None,
    dpi: int = 220,
    job_name: Optional[str] = None,
    job_id: Optional[str] = None,
) -> dict:
    """
    Process all PDF documents in a directory using the fastest available method.

    Args:
        directory_path: Directory containing PDF files to process
        model: Qwen model to use for OCR fallback
        dpi: DPI for OCR image conversion (default: 220)
        job_name: Optional name/title for the job
        job_id: Optional existing job ID to add essays to

    Returns:
        Summary containing Job ID, counts, and processing method breakdown.
    """
    return _batch_process_documents_core(directory_path, model, dpi, job_name, job_id)


@mcp.tool
def get_job_statistics(job_id: str) -> dict:
    """
    Returns a manifest of the job's essays for inspection.

    Args:
        job_id: The ID of the job to inspect.

    Returns:
        Dictionary containing a list of students, page counts, and word counts.
    """
    essays = DB_MANAGER.get_job_essays(job_id)
    if not essays:
        return {"status": "warning", "message": f"No essays found for job {job_id}"}

    manifest = []
    for essay in essays:
        raw_text = essay.get("raw_text", "")
        word_count = len(raw_text.split()) if raw_text else 0
        metadata = essay.get("metadata", {})
        page_count = metadata.get("page_count", "N/A")

        manifest.append({
            "essay_id": essay["id"],
            "student_name": essay["student_name"],
            "page_count": page_count,
            "word_count": word_count,
        })

    return {
        "status": "success",
        "job_id": job_id,
        "total_students": len(essays),
        "manifest": manifest,
    }


@mcp.tool
def scrub_processed_job(job_id: str) -> dict:
    """
    Scrubs PII from all essays in a processed job.

    Args:
        job_id: The ID of the job to scrub.

    Returns:
        Summary of scrubbing operation.
    """
    print(f"[Scrubber-MCP] Scrubbing Job {job_id}...", file=sys.stderr)

    try:
        job_dir = JOB_MANAGER.get_job_directory(job_id)
        scrubber_tool = ScrubberTool(job_dir=job_dir, names_dir=NAMES_DIR, db_manager=DB_MANAGER)
        output_path = scrubber_tool.scrub_job()

        essays = DB_MANAGER.get_job_essays(job_id)
        scrubbed_count = len([e for e in essays if e["status"] == "SCRUBBED"])

        print(f"[Scrubber-MCP] Job {job_id} Scrubbed. {scrubbed_count} essays processed.", file=sys.stderr)

        return {
            "status": "success",
            "job_id": job_id,
            "scrubbed_count": scrubbed_count,
            "total_essays": len(essays),
            "output_file": str(output_path),
        }

    except Exception as e:
        print(f"[Scrubber-MCP] Error scrubbing job {job_id}: {e}", file=sys.stderr)
        return {"status": "error", "message": str(e)}


@mcp.tool
def validate_student_names(job_id: str) -> dict:
    """
    Validates detected student names against the school roster CSV.

    Args:
        job_id: The ID of the job to validate.

    Returns:
        Dictionary with matched and mismatched students.
    """
    essays = DB_MANAGER.get_job_essays(job_id)

    if not essays:
        return {"status": "error", "message": f"No essays found for job {job_id}"}

    essays_by_id = {essay.get("id"): essay for essay in essays}
    detected_names = {essay.get("student_name", "Unknown"): essay.get("id") for essay in essays}
    all_students = STUDENT_ROSTER_WITH_EMAILS.get_all_students()

    matched = []
    mismatched = []

    for detected_name, essay_id in detected_names.items():
        student_info = STUDENT_ROSTER_WITH_EMAILS.get_student_info(detected_name)

        if student_info:
            matched.append({
                "essay_id": essay_id,
                "detected_name": detected_name,
                "roster_name": student_info.full_name,
                "email": student_info.email,
                "grade": student_info.grade
            })
        else:
            essay = essays_by_id.get(essay_id, {})
            raw_text = essay.get("raw_text", "")
            preview = raw_text[:300].strip() if raw_text else "(No text available)"
            if len(raw_text) > 300:
                preview += "..."

            mismatched.append({
                "essay_id": essay_id,
                "detected_name": detected_name,
                "essay_preview": preview,
                "reason": "Name not found in school roster"
            })

    detected_names_lower = {name.lower() for name in detected_names.keys()}
    missing_count = sum(1 for roster_name in all_students.keys() if roster_name.lower() not in detected_names_lower)

    return {
        "status": "needs_corrections" if mismatched else "validated",
        "matched_students": matched,
        "mismatched_students": mismatched,
        "total_detected": len(essays),
        "total_matched": len(matched),
        "total_mismatched": len(mismatched),
        "total_missing": missing_count,
        "message": f"Found {len(mismatched)} name(s) that need correction" if mismatched else "All student names validated successfully"
    }


@mcp.tool
def get_essay_preview(job_id: str, essay_id: int, max_lines: int = 50) -> dict:
    """
    Returns the first N lines of an essay's raw text for student identification.

    Args:
        job_id: The ID of the job.
        essay_id: The essay database ID to preview.
        max_lines: Maximum number of lines to return (default: 50).

    Returns:
        Dictionary with essay preview text.
    """
    essays = DB_MANAGER.get_job_essays(job_id)

    if not essays:
        return {"status": "error", "message": f"No essays found for job {job_id}"}

    essay = next((e for e in essays if e.get("id") == essay_id), None)

    if not essay:
        return {"status": "error", "message": f"Essay ID {essay_id} not found in job {job_id}"}

    raw_text = essay.get("raw_text", "")
    if not raw_text:
        return {"status": "error", "message": f"Essay ID {essay_id} has no text content"}

    all_lines = raw_text.split("\n")
    preview_lines = all_lines[:max_lines]
    preview = "\n".join(preview_lines)

    return {
        "status": "success",
        "essay_id": essay_id,
        "detected_name": essay.get("student_name", "Unknown"),
        "preview": preview,
        "total_lines": len(all_lines),
        "lines_shown": len(preview_lines)
    }


@mcp.tool
def correct_detected_name(job_id: str, essay_id: int, corrected_name: str) -> dict:
    """
    Corrects a student name in the database BEFORE grading begins.

    Args:
        job_id: The ID of the job.
        essay_id: The essay database ID to correct.
        corrected_name: The corrected student name.

    Returns:
        Dictionary with correction result.
    """
    student_info = STUDENT_ROSTER_WITH_EMAILS.get_student_info(corrected_name)

    if not student_info:
        all_students = STUDENT_ROSTER_WITH_EMAILS.get_all_students()
        corrected_lower = corrected_name.lower()

        possible_matches = [
            (name, info) for name, info in all_students.items()
            if corrected_lower in name.lower() or name.lower() in corrected_lower
        ]

        if possible_matches:
            return {
                "status": "not_in_roster",
                "message": f"'{corrected_name}' not found in roster. Did you mean one of these?",
                "possible_matches": [
                    {"name": info.full_name, "email": info.email, "grade": info.grade}
                    for name, info in possible_matches[:5]
                ]
            }
        else:
            return {
                "status": "not_in_roster",
                "message": f"'{corrected_name}' not found in school roster."
            }

    essays = DB_MANAGER.get_job_essays(job_id)
    essay = next((e for e in essays if e.get("id") == essay_id), None)

    if not essay:
        return {"status": "error", "message": f"Essay {essay_id} not found in job {job_id}"}

    old_name = essay.get("student_name", "Unknown")

    try:
        cursor = DB_MANAGER.conn.cursor()
        cursor.execute("UPDATE essays SET student_name = ? WHERE id = ?", (student_info.full_name, essay_id))
        DB_MANAGER.conn.commit()

        return {
            "status": "success",
            "essay_id": essay_id,
            "old_name": old_name,
            "new_name": student_info.full_name,
            "email": student_info.email,
            "grade": student_info.grade,
            "message": f"Successfully corrected essay {essay_id} from '{old_name}' to '{student_info.full_name}'"
        }
    except Exception as e:
        return {"status": "error", "message": f"Database update failed: {str(e)}"}


def _normalize_processed_job_core(job_id: str, model: Optional[str] = None) -> dict:
    """Core logic for normalizing text in a job using xAI."""
    print(f"[Cleanup-MCP] Normalizing Job {job_id}...", file=sys.stderr)

    model = model or os.environ.get("CLEANING_API_MODEL") or os.environ.get("XAI_API_MODEL") or "grok-beta"

    try:
        client = get_openai_client(
            api_key=os.environ.get("CLEANING_API_KEY") or os.environ.get("XAI_API_KEY"),
            base_url=os.environ.get("CLEANING_BASE_URL") or os.environ.get("XAI_BASE_URL"),
        )
    except Exception as e:
        return {"status": "error", "message": f"Failed to get AI client: {e}"}

    essays = DB_MANAGER.get_job_essays(job_id)

    if not essays:
        return {"status": "warning", "message": f"No essays found for job {job_id}"}

    normalized_count = 0
    errors = []

    for essay in essays:
        try:
            essay_id = essay["id"]
            text_to_normalize = essay["scrubbed_text"] or essay["raw_text"]

            if not text_to_normalize:
                continue

            messages = [
                {
                    "role": "system",
                    "content": "You are a text normalization assistant. Fix OCR errors and typos while preserving the original meaning. Return ONLY the normalized text.",
                },
                {"role": "user", "content": f"Normalize the following text:\n\n{text_to_normalize}"},
            ]
            response = _call_chat_completion(client, model, messages)
            normalized_text = response.choices[0].message.content.strip()

            DB_MANAGER.update_essay_normalized(essay_id, normalized_text)
            normalized_count += 1

        except Exception as e:
            error_msg = f"Essay {essay['id']}: {str(e)}"
            print(f"[Cleanup-MCP] Error normalizing essay {essay['id']}: {e}", file=sys.stderr)
            errors.append(error_msg)

    print(f"[Cleanup-MCP] Job {job_id} Normalized. {normalized_count}/{len(essays)} essays processed.", file=sys.stderr)

    return {
        "status": "success",
        "job_id": job_id,
        "normalized_count": normalized_count,
        "total_essays": len(essays),
        "errors": errors if errors else None,
    }


def _evaluate_job_core(
    job_id: str,
    rubric: str,
    context_material: str,
    model: Optional[str] = None,
    system_instructions: Optional[str] = None,
) -> dict:
    """Core logic for evaluating essays in a job."""
    print(f"[Evaluation-MCP] Evaluating Job {job_id}...", file=sys.stderr)

    if not rubric:
        job_info = DB_MANAGER.get_job(job_id)
        if not job_info:
            return {"status": "error", "message": f"Job not found: {job_id}"}
        rubric = job_info.get("rubric")
        if not rubric:
            return {"status": "error", "message": f"No rubric found for job {job_id}"}
        print(f"[Evaluation-MCP] Using rubric from database", file=sys.stderr)

    model = model or os.environ.get("EVALUATION_API_MODEL") or os.environ.get("XAI_API_MODEL") or "grok-beta"

    try:
        client = get_openai_client(
            api_key=os.environ.get("EVALUATION_API_KEY") or os.environ.get("XAI_API_KEY"),
            base_url=os.environ.get("EVALUATION_BASE_URL") or os.environ.get("XAI_BASE_URL"),
        )
    except Exception as e:
        return {"status": "error", "message": f"Failed to get AI client: {e}"}

    essays = DB_MANAGER.get_job_essays(job_id)

    if not essays:
        return {"status": "warning", "message": f"No essays found for job {job_id}"}

    evaluated_count = 0
    errors = []

    evaluation_schema = {
        "type": "object",
        "properties": {
            "criteria": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "score": {"type": ["string", "number"]},
                        "feedback": {
                            "type": "object",
                            "properties": {
                                "justification": {"type": "string"},
                                "examples": {"type": "array", "items": {"type": "string"}},
                                "advice": {"type": "string"},
                                "rewritten_example": {"type": "string"}
                            },
                            "required": ["justification", "examples", "advice", "rewritten_example"],
                            "additionalProperties": False
                        }
                    },
                    "required": ["name", "score", "feedback"],
                    "additionalProperties": False
                }
            },
            "overall_score": {"type": "string"},
            "summary": {"type": "string"}
        },
        "required": ["criteria", "overall_score", "summary"],
        "additionalProperties": False
    }

    response_format = {
        "type": "json_schema",
        "json_schema": {"name": "essay_evaluation", "strict": True, "schema": evaluation_schema}
    }

    for essay in essays:
        try:
            essay_id = essay["id"]
            text_to_evaluate = essay.get("normalized_text") or essay.get("scrubbed_text") or essay.get("raw_text")

            if not text_to_evaluate:
                continue

            prompt = get_evaluation_prompt(text_to_evaluate, rubric, context_material, system_instructions)

            messages = [
                {"role": "system", "content": "You are a professional academic evaluator."},
                {"role": "user", "content": prompt},
            ]

            response = _call_chat_completion(client, model, messages, response_format=response_format, max_tokens=4000, temperature=0.1)
            raw_eval_text = response.choices[0].message.content.strip()

            eval_data = extract_json_from_text(raw_eval_text)
            if not eval_data:
                job_dir = JOB_MANAGER.get_job_directory(job_id)
                error_file = job_dir / f"failed_eval_essay_{essay_id}.json"
                with open(error_file, "w") as f:
                    f.write(raw_eval_text)
                raise ValueError(f"Failed to extract valid JSON. Response saved to {error_file}")

            eval_json_str = json.dumps(eval_data)
            grade = str(eval_data.get("overall_score") or eval_data.get("score") or "")

            DB_MANAGER.update_essay_evaluation(essay_id, eval_json_str, grade)
            evaluated_count += 1

        except Exception as e:
            error_msg = f"Essay {essay['id']}: {str(e)}"
            print(f"[Evaluation-MCP] Error evaluating essay {essay['id']}: {e}", file=sys.stderr)
            errors.append(error_msg)

    print(f"[Evaluation-MCP] Job {job_id} Evaluated. {evaluated_count}/{len(essays)} essays processed.", file=sys.stderr)

    return {
        "status": "success",
        "job_id": job_id,
        "evaluated_count": evaluated_count,
        "total_essays": len(essays),
        "errors": errors if errors else None,
    }


@mcp.tool
def evaluate_job(
    job_id: str,
    rubric: Optional[str] = None,
    context_material: str = "",
    model: Optional[str] = None,
    system_instructions: Optional[str] = None,
) -> dict:
    """
    Evaluates all essays in a processed job using AI based on a rubric.

    Args:
        job_id: The ID of the job to evaluate.
        rubric: The grading criteria text (optional if stored in database).
        context_material: The source material or answer key context (optional).
        model: The AI model to use.
        system_instructions: Optional custom instructions for the AI evaluator.

    Returns:
        Summary of evaluation operation.
    """
    return _evaluate_job_core(job_id, rubric or "", context_material, model, system_instructions)


@mcp.tool
def add_to_knowledge_base(file_paths: List[str], topic: str) -> dict:
    """
    Adds local files to the knowledge base for a specific topic.

    Args:
        file_paths: List of paths to files.
        topic: A name for the collection.

    Returns:
        Summary of ingestion.
    """
    try:
        count = KB_MANAGER.ingest_documents(file_paths, topic)
        return {
            "status": "success",
            "topic": topic,
            "documents_added": count,
            "message": f"Successfully indexed {count} documents into topic '{topic}'.",
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


@mcp.tool
def query_knowledge_base(query: str, topic: str, include_raw_context: bool = False) -> dict:
    """
    Queries the knowledge base for information about a specific topic.

    Args:
        query: The question or search term.
        topic: The topic collection to search in.
        include_raw_context: If true, returns the raw text chunks.

    Returns:
        Synthesized answer and optional context.
    """
    try:
        answer = KB_MANAGER.query_knowledge(query, topic)
        result: dict[str, Any] = {"status": "success", "topic": topic, "answer": answer}

        if include_raw_context:
            chunks = KB_MANAGER.retrieve_context_chunks(query, topic)
            result["context_chunks"] = chunks

        return result
    except Exception as e:
        return {"status": "error", "message": str(e)}


@mcp.tool
def generate_gradebook(job_id: str) -> dict:
    """
    Generates a CSV gradebook for a job.

    Args:
        job_id: The ID of the job to report on.

    Returns:
        Summary with the path to the CSV file.
    """
    try:
        essays = DB_MANAGER.get_job_essays(job_id)
        if not essays:
            return {"status": "error", "message": f"No essays found for job {job_id}"}

        csv_path = REPORT_GENERATOR.generate_csv_gradebook(job_id, essays)
        return {
            "status": "success",
            "job_id": job_id,
            "csv_path": csv_path,
            "message": f"Gradebook generated at {csv_path}",
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


@mcp.tool
def generate_student_feedback(job_id: str) -> dict:
    """
    Generates individual PDF feedback reports for each student.

    Args:
        job_id: The ID of the job to report on.

    Returns:
        Summary with paths to generated files.
    """
    try:
        essays = DB_MANAGER.get_job_essays(job_id)
        if not essays:
            return {"status": "error", "message": f"No essays found for job {job_id}"}

        pdf_dir = REPORT_GENERATOR.generate_student_feedback_pdfs(job_id, essays)
        zip_path = REPORT_GENERATOR.zip_directory(pdf_dir, f"{job_id}_student_feedback", job_id=job_id)

        return {
            "status": "success",
            "job_id": job_id,
            "pdf_directory": pdf_dir,
            "zip_path": zip_path,
            "message": f"Individual feedback PDFs generated and zipped at {zip_path}",
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


@mcp.tool
def get_report_file(job_id: str, report_type: str, essay_id: Optional[int] = None) -> dict:
    """
    Retrieve a report file from the database.

    Args:
        job_id: The job ID for the report
        report_type: Type of report ('gradebook_csv', 'student_feedback_zip', 'student_pdf')
        essay_id: Required only for 'student_pdf' report_type.

    Returns:
        Dictionary with base64-encoded file content.
    """
    try:
        valid_types = ['gradebook_csv', 'student_feedback_zip', 'student_pdf']
        if report_type not in valid_types:
            return {"status": "error", "message": f"Invalid report_type. Must be one of: {', '.join(valid_types)}"}

        if report_type == 'student_pdf' and essay_id is None:
            return {"status": "error", "message": "essay_id is required for 'student_pdf'"}

        report = DB_MANAGER.get_report_with_metadata(job_id, report_type, essay_id)

        if not report:
            return {"status": "error", "message": f"No {report_type} report found for job {job_id}"}

        content_base64 = base64.b64encode(report['content']).decode('utf-8')

        return {
            "status": "success",
            "filename": report['filename'],
            "content_base64": content_base64,
            "size_bytes": len(report['content']),
            "created_at": report['created_at'],
            "message": f"Successfully retrieved {report['filename']}"
        }

    except Exception as e:
        return {"status": "error", "message": f"Error retrieving report: {str(e)}"}


@mcp.tool
def download_reports_locally(job_id: str) -> dict:
    """
    Downloads report files from database to local temp directory.

    Args:
        job_id: The job ID for the reports to download

    Returns:
        Dictionary with paths to downloaded files.
    """
    try:
        import tempfile

        gradebook = DB_MANAGER.get_report_with_metadata(job_id, 'gradebook_csv')
        if not gradebook:
            return {"status": "error", "message": f"Gradebook CSV not found for job {job_id}"}

        feedback_zip = DB_MANAGER.get_report_with_metadata(job_id, 'student_feedback_zip')
        if not feedback_zip:
            return {"status": "error", "message": f"Student feedback ZIP not found for job {job_id}"}

        temp_dir = Path(tempfile.gettempdir()) / "edagent_downloads" / job_id
        temp_dir.mkdir(parents=True, exist_ok=True)

        gradebook_path = temp_dir / gradebook['filename']
        with open(gradebook_path, 'wb') as f:
            f.write(gradebook['content'])

        zip_path = temp_dir / feedback_zip['filename']
        with open(zip_path, 'wb') as f:
            f.write(feedback_zip['content'])

        return {
            "status": "success",
            "job_id": job_id,
            "gradebook_path": str(gradebook_path.absolute()),
            "feedback_zip_path": str(zip_path.absolute()),
            "message": f"Reports downloaded successfully to {temp_dir}"
        }

    except Exception as e:
        return {"status": "error", "message": f"Error downloading reports: {str(e)}"}


@mcp.tool
async def send_student_feedback_emails(job_id: str) -> dict:
    """
    Sends individual PDF feedback reports to students via email.

    Args:
        job_id: The ID of the graded job to send feedback for.

    Returns:
        Summary of sent/failed/skipped emails.
    """
    try:
        result = await EMAILER_TOOL.send_feedback_emails(
            job_id=job_id,
            subject_template=None,
            body_template="default_feedback",
            dry_run=False,
            filter_students=None
        )
        return result
    except Exception as e:
        return {"status": "error", "message": str(e)}


@mcp.tool
def identify_email_problems(job_id: str) -> dict:
    """
    Identifies students who cannot be emailed and why.

    Args:
        job_id: The ID of the graded job to check.

    Returns:
        Dictionary with problem students and ready-to-send count.
    """
    return NAME_FIXER_TOOL.identify_email_problems(job_id)


@mcp.tool
def verify_student_name_correction(job_id: str, essay_id: int, suggested_name: str) -> dict:
    """
    Verifies that a suggested name correction exists in the roster.

    Args:
        job_id: The ID of the graded job.
        essay_id: The essay database ID to correct.
        suggested_name: The corrected student name.

    Returns:
        Dictionary with match details or possible matches.
    """
    return NAME_FIXER_TOOL.verify_student_name_correction(job_id, essay_id, suggested_name)


@mcp.tool
def apply_student_name_correction(job_id: str, essay_id: int, confirmed_name: str) -> dict:
    """
    Applies a confirmed name correction to the database.

    Args:
        job_id: The ID of the graded job.
        essay_id: The essay database ID to update.
        confirmed_name: The confirmed correct student name.

    Returns:
        Dictionary with update result.
    """
    return NAME_FIXER_TOOL.apply_student_name_correction(job_id, essay_id, confirmed_name)


@mcp.tool
def skip_student_email(job_id: str, essay_id: int, reason: str = "Manual delivery") -> dict:
    """
    Marks a student to skip for email delivery.

    Args:
        job_id: The ID of the graded job.
        essay_id: The essay database ID to skip.
        reason: Reason for skipping.

    Returns:
        Dictionary with skip result.
    """
    return NAME_FIXER_TOOL.skip_student_email(job_id, essay_id, reason)


@mcp.tool
def get_email_log(job_id: str) -> dict:
    """
    Retrieves the email delivery log for a completed job.

    Args:
        job_id: The job ID from the grading process.

    Returns:
        Dictionary with lists of sent, failed, and skipped emails.
    """
    log_path = SERVER_DIR / "data" / "reports" / job_id / "email_log.jsonl"

    if not log_path.exists():
        return {"error": f"No email log found for job_id={job_id}", "sent": [], "failed": [], "skipped": []}

    sent = []
    failed = []
    skipped = []

    with open(log_path) as f:
        for line in f:
            record = json.loads(line)
            entry = {
                "student_name": record.get("student_name"),
                "email": record.get("email"),
                "timestamp": record.get("timestamp"),
            }

            if record["status"] == "SENT":
                sent.append(entry)
            elif record["status"] == "FAILED":
                entry["error"] = record.get("error")
                failed.append(entry)
            elif record["status"] == "SKIPPED":
                entry["reason"] = record.get("reason")
                skipped.append(entry)

    return {
        "job_id": job_id,
        "total_sent": len(sent),
        "total_failed": len(failed),
        "total_skipped": len(skipped),
        "sent": sent,
        "failed": failed,
        "skipped": skipped
    }


@mcp.tool
def cleanup_old_jobs(retention_days: int = 210, dry_run: bool = False) -> dict:
    """
    Deletes jobs older than the specified retention period.

    Args:
        retention_days: Number of days to keep jobs.
        dry_run: If True, lists what would be deleted without taking action.

    Returns:
        Summary of deleted jobs.
    """
    return CLEANUP_TOOL.cleanup_old_jobs(retention_days, dry_run)


@mcp.tool
def delete_knowledge_topic(topic: str) -> dict:
    """
    Manually deletes a Knowledge Base topic.

    Args:
        topic: The name of the topic to delete.

    Returns:
        Status of the operation.
    """
    return CLEANUP_TOOL.delete_knowledge_topic(topic)


@mcp.tool
def search_past_jobs(query: str, start_date: Optional[str] = None, end_date: Optional[str] = None) -> dict:
    """
    Searches for past jobs by keyword.

    Args:
        query: The search keyword.
        start_date: Optional start date (YYYY-MM-DD).
        end_date: Optional end date (YYYY-MM-DD).

    Returns:
        List of matching jobs.
    """
    return ARCHIVE_TOOL.search_past_jobs(query, start_date, end_date)


@mcp.tool
def export_job_archive(job_id: str) -> dict:
    """
    Exports a comprehensive ZIP archive of a job.

    Args:
        job_id: The ID of the job to export.

    Returns:
        Path to the generated ZIP file.
    """
    return ARCHIVE_TOOL.export_job_archive(job_id)


@mcp.tool
def convert_pdf_to_text(file_path: str, output_path: Optional[str] = None, use_ocr: bool = False) -> dict:
    """
    Converts a PDF to plain text format.

    Args:
        file_path: Path to the PDF file
        output_path: Optional path for output text file
        use_ocr: If True, uses OCR for scanned PDFs

    Returns:
        Dictionary with status and text content.
    """
    try:
        txt_path = CONVERTER.convert_pdf_to_text(file_path, output_path, use_ocr)

        with open(txt_path, 'r', encoding='utf-8') as f:
            text_content = f.read()

        return {
            "status": "success",
            "input_file": file_path,
            "output_file": str(txt_path),
            "text_content": text_content,
            "message": f"Successfully converted to text: {txt_path}",
            "used_ocr": use_ocr,
        }
    except Exception as e:
        return {"status": "error", "input_file": file_path, "error": str(e)}


@mcp.tool
def read_text_file(file_path: str) -> dict:
    """
    Reads a plain text file and returns its contents.

    Args:
        file_path: Path to the text file

    Returns:
        Dictionary with text content
    """
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            text_content = f.read()

        return {
            "status": "success",
            "file_path": file_path,
            "text_content": text_content,
            "message": f"Successfully read text file: {file_path}",
        }
    except FileNotFoundError:
        return {"status": "error", "file_path": file_path, "error": f"File not found: {file_path}"}
    except Exception as e:
        return {"status": "error", "file_path": file_path, "error": str(e)}


@mcp.tool
def check_conversion_capabilities() -> dict:
    """
    Checks which document conversion tools are available.

    Returns:
        Dictionary with capability status and installation instructions.
    """
    return CONVERTER.get_conversion_info()


@mcp.tool
def convert_image_to_pdf(file_path: str, output_path: Optional[str] = None) -> dict:
    """
    Converts a single image file to PDF format.

    Args:
        file_path: Path to the image file
        output_path: Optional path for output PDF

    Returns:
        Dictionary with status and path to converted PDF.
    """
    try:
        pdf_path = CONVERTER.convert_image_to_pdf(file_path, output_path)
        return {
            "status": "success",
            "input_file": file_path,
            "output_file": str(pdf_path),
            "message": f"Successfully converted image to PDF: {pdf_path}",
        }
    except Exception as e:
        return {"status": "error", "input_file": file_path, "error": str(e)}


@mcp.tool
def batch_convert_images_to_pdf(input_dir: str, output_dir: str) -> dict:
    """
    Converts all image files in a directory to individual PDFs.

    Args:
        input_dir: Directory containing image files
        output_dir: Directory to save converted PDFs

    Returns:
        Dictionary with conversion summary.
    """
    try:
        pdf_paths = CONVERTER.batch_convert_images_to_pdf(input_dir, output_dir)
        return {
            "status": "success",
            "input_directory": input_dir,
            "output_directory": output_dir,
            "files_converted": len(pdf_paths),
            "converted_files": [str(p) for p in pdf_paths],
            "message": f"Successfully converted {len(pdf_paths)} images to PDF.",
        }
    except Exception as e:
        return {"status": "error", "input_directory": input_dir, "error": str(e)}


@mcp.tool
def merge_images_to_pdf(image_paths: List[str], output_path: str) -> dict:
    """
    Merges multiple images into a single multi-page PDF.

    Args:
        image_paths: List of paths to image files
        output_path: Path for the output PDF file

    Returns:
        Dictionary with status and path to merged PDF.
    """
    try:
        pdf_path = CONVERTER.merge_images_to_pdf(image_paths, output_path)
        return {
            "status": "success",
            "input_files": image_paths,
            "output_file": str(pdf_path),
            "pages": len(image_paths),
            "message": f"Successfully merged {len(image_paths)} images into PDF: {pdf_path}",
        }
    except Exception as e:
        return {"status": "error", "input_files": image_paths, "error": str(e)}


if __name__ == "__main__":
    mcp.run()
