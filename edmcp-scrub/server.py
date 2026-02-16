"""
Scrub MCP Server - FastMCP server for document PII scrubbing.

Standalone document intake and name-scrubbing pipeline.
Teacher submits documents, validates/corrects student names,
scrubs PII, and gets a batch ID that downstream servers can
use to retrieve clean documents.
"""

import sys
from pathlib import Path
from typing import Optional, List

from fastmcp import FastMCP
from edmcp_core import DatabaseManager, load_edmcp_config

from edmcp_scrub.core.name_loader import NameLoader
from edmcp_scrub.core.scrubber import Scrubber, ScrubberTool
from edmcp_scrub.core.student_roster import StudentRoster
from edmcp_scrub.core.document_processor import DocumentProcessor

# Load environment variables from central .env file
load_edmcp_config()

# Initialize using paths relative to this server.py file
SERVER_DIR = Path(__file__).parent
NAMES_DIR = SERVER_DIR / "edmcp_scrub" / "data" / "names"
DB_PATH = SERVER_DIR / "edmcp.db"

# Initialize shared components
DB_MANAGER = DatabaseManager(DB_PATH)

# Name loading
LOADER = NameLoader(NAMES_DIR)
ALL_NAMES = LOADER.load_all_names()
SCRUBBER = Scrubber(ALL_NAMES)

# Student roster for name validation/correction
STUDENT_ROSTER = StudentRoster(NAMES_DIR)
STUDENT_ROSTER_NAMES = STUDENT_ROSTER.get_full_name_set()

# Document processor for PDF intake
DOC_PROCESSOR = DocumentProcessor(
    db_manager=DB_MANAGER,
    student_roster=STUDENT_ROSTER_NAMES,
)

# Initialize the FastMCP server
mcp = FastMCP("Document Scrubber Server")


# ============================================================================
# MCP Tools
# ============================================================================


@mcp.tool
def create_batch(batch_name: Optional[str] = None) -> dict:
    """
    Creates a new scrub batch. This is the first step — call this before
    batch_process_documents or add_text_documents.

    Args:
        batch_name: Optional name for this batch

    Returns:
        Dictionary with batch_id
    """
    try:
        batch_id = DB_MANAGER.create_scrub_batch(name=batch_name)
        return {
            "status": "success",
            "batch_id": batch_id,
            "message": f"Created batch '{batch_name or batch_id}'",
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


@mcp.tool
def batch_process_documents(
    directory_path: str,
    batch_name: Optional[str] = None,
    batch_id: Optional[str] = None,
    dpi: int = 220,
) -> dict:
    """
    Process all PDF documents in a directory: extract text, detect student names,
    and store in a batch. Tries native text extraction first, falls back to OCR.

    Args:
        directory_path: Directory containing PDF files to process
        batch_name: Optional name for a new batch (ignored if batch_id provided)
        batch_id: Optional existing batch ID to add documents to
        dpi: DPI for OCR image conversion (default: 220)

    Returns:
        Summary with batch_id, student counts, and processing method breakdown
    """
    try:
        if batch_id:
            batch = DB_MANAGER.get_scrub_batch(batch_id)
            if not batch:
                return {"status": "error", "message": f"Batch not found: {batch_id}"}
        else:
            batch_id = DB_MANAGER.create_scrub_batch(name=batch_name)

        result = DOC_PROCESSOR.batch_process(directory_path, batch_id, dpi=dpi)

        return {
            "status": "success",
            "batch_id": batch_id,
            "batch_name": batch_name,
            "students_detected": result["students_found"],
            "summary": (
                f"Processed {result['files_processed']} files "
                f"({result['processing_method']}). "
                f"Found {result['students_found']} student documents."
            ),
            "processing_details": {
                "total_files": result["files_processed"],
                "text_extraction": result["text_extraction"],
                "ocr": result["ocr"],
            },
            "errors": result["errors"],
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


@mcp.tool
def add_text_documents(
    batch_id: str,
    texts: list[dict],
) -> dict:
    """
    Add pre-extracted text documents to a batch.
    Use this when text has already been extracted (e.g., by another server).

    Args:
        batch_id: The batch to add documents to
        texts: List of dicts, each with 'text' and optional 'student_name' and 'metadata'

    Returns:
        Summary with doc_ids added
    """
    try:
        batch = DB_MANAGER.get_scrub_batch(batch_id)
        if not batch:
            return {"status": "error", "message": f"Batch not found: {batch_id}"}

        doc_ids = []
        for item in texts:
            raw_text = item.get("text", "")
            student_name = item.get("student_name") or DOC_PROCESSOR.detect_name(raw_text)
            metadata = item.get("metadata")

            doc_id = DB_MANAGER.add_scrubbed_document(
                batch_id=batch_id,
                student_name=student_name,
                raw_text=raw_text,
                metadata=metadata,
            )
            doc_ids.append(doc_id)

        return {
            "status": "success",
            "batch_id": batch_id,
            "documents_added": len(doc_ids),
            "doc_ids": doc_ids,
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


@mcp.tool
def validate_student_names(batch_id: str) -> dict:
    """
    Validates detected student names against the school roster CSV.
    Returns matched and mismatched students so teacher can correct before scrubbing.

    Args:
        batch_id: The batch ID to validate

    Returns:
        Dictionary with matched and mismatched students
    """
    docs = DB_MANAGER.get_batch_documents(batch_id)

    if not docs:
        return {"status": "error", "message": f"No documents found for batch {batch_id}"}

    all_students = STUDENT_ROSTER.get_all_students()

    matched = []
    mismatched = []

    for doc in docs:
        doc_id = doc["id"]
        detected_name = doc.get("student_name", "Unknown")
        student_info = STUDENT_ROSTER.get_student_info(detected_name)

        if student_info:
            matched.append({
                "doc_id": doc_id,
                "detected_name": detected_name,
                "roster_name": student_info.full_name,
                "email": student_info.email,
            })
        else:
            raw_text = doc.get("raw_text", "")
            preview = raw_text[:300].strip() if raw_text else "(No text available)"
            if len(raw_text) > 300:
                preview += "..."

            mismatched.append({
                "doc_id": doc_id,
                "detected_name": detected_name,
                "document_preview": preview,
                "reason": "Name not found in school roster",
            })

    detected_names_lower = {
        doc.get("student_name", "Unknown").lower() for doc in docs
    }
    missing_count = sum(
        1 for roster_name in all_students.keys()
        if roster_name.lower() not in detected_names_lower
    )

    return {
        "status": "needs_corrections" if mismatched else "validated",
        "matched_students": matched,
        "mismatched_students": mismatched,
        "total_detected": len(docs),
        "total_matched": len(matched),
        "total_mismatched": len(mismatched),
        "total_missing_from_roster": missing_count,
        "message": (
            f"Found {len(mismatched)} name(s) that need correction"
            if mismatched
            else "All student names validated successfully"
        ),
    }


@mcp.tool
def get_document_preview(batch_id: str, doc_id: int, max_lines: int = 50) -> dict:
    """
    Returns the first N lines of a document's raw text for student identification.

    Args:
        batch_id: The batch ID
        doc_id: The document database ID to preview
        max_lines: Maximum number of lines to return (default: 50)

    Returns:
        Dictionary with document preview text
    """
    doc = DB_MANAGER.get_scrubbed_document(doc_id)

    if not doc:
        return {"status": "error", "message": f"Document {doc_id} not found"}

    if doc["batch_id"] != batch_id:
        return {"status": "error", "message": f"Document {doc_id} not in batch {batch_id}"}

    raw_text = doc.get("raw_text", "")
    if not raw_text:
        return {"status": "error", "message": f"Document {doc_id} has no text content"}

    all_lines = raw_text.split("\n")
    preview_lines = all_lines[:max_lines]
    preview = "\n".join(preview_lines)

    return {
        "status": "success",
        "doc_id": doc_id,
        "detected_name": doc.get("student_name", "Unknown"),
        "preview": preview,
        "total_lines": len(all_lines),
        "lines_shown": len(preview_lines),
    }


@mcp.tool
def correct_detected_name(batch_id: str, doc_id: int, corrected_name: str) -> dict:
    """
    Corrects a student name in the database. Use after validate_student_names
    identifies mismatches and teacher identifies the real student.

    Args:
        batch_id: The batch ID
        doc_id: The document database ID to correct
        corrected_name: The corrected student name

    Returns:
        Dictionary with correction result
    """
    student_info = STUDENT_ROSTER.get_student_info(corrected_name)

    if not student_info:
        # Offer fuzzy suggestions
        all_students = STUDENT_ROSTER.get_all_students()
        corrected_lower = corrected_name.lower()

        possible_matches = [
            (name, info)
            for name, info in all_students.items()
            if corrected_lower in name.lower() or name.lower() in corrected_lower
        ]

        if possible_matches:
            return {
                "status": "not_in_roster",
                "message": f"'{corrected_name}' not found in roster. Did you mean one of these?",
                "possible_matches": [
                    {"name": info.full_name, "email": info.email}
                    for name, info in possible_matches[:5]
                ],
            }
        else:
            return {
                "status": "not_in_roster",
                "message": f"'{corrected_name}' not found in school roster.",
            }

    doc = DB_MANAGER.get_scrubbed_document(doc_id)
    if not doc:
        return {"status": "error", "message": f"Document {doc_id} not found"}

    if doc["batch_id"] != batch_id:
        return {"status": "error", "message": f"Document {doc_id} not in batch {batch_id}"}

    old_name = doc.get("student_name", "Unknown")

    try:
        DB_MANAGER.update_document_name(doc_id, student_info.full_name)

        return {
            "status": "success",
            "doc_id": doc_id,
            "old_name": old_name,
            "new_name": student_info.full_name,
            "email": student_info.email,
            "message": (
                f"Corrected document {doc_id} from "
                f"'{old_name}' to '{student_info.full_name}'"
            ),
        }
    except Exception as e:
        return {"status": "error", "message": f"Database update failed: {str(e)}"}


@mcp.tool
def scrub_batch(batch_id: str) -> dict:
    """
    Scrubs PII from all documents in a batch.
    Run this after names have been validated/corrected.

    Args:
        batch_id: The batch ID to scrub

    Returns:
        Summary of scrubbing operation
    """
    print(f"[Scrub] Scrubbing batch {batch_id}...", file=sys.stderr)

    try:
        batch = DB_MANAGER.get_scrub_batch(batch_id)
        if not batch:
            return {"status": "error", "message": f"Batch not found: {batch_id}"}

        custom_words = DB_MANAGER.get_batch_custom_scrub_words(batch_id)

        scrubber_tool = ScrubberTool(names_dir=NAMES_DIR, db_manager=DB_MANAGER)
        scrubbed_count = scrubber_tool.scrub_batch(
            batch_id, custom_words=custom_words if custom_words else None
        )

        print(
            f"[Scrub] Batch {batch_id} scrubbed. {scrubbed_count} documents processed.",
            file=sys.stderr,
        )

        return {
            "status": "success",
            "batch_id": batch_id,
            "scrubbed_count": scrubbed_count,
            "custom_words_applied": len(custom_words),
        }
    except Exception as e:
        print(f"[Scrub] Error scrubbing batch {batch_id}: {e}", file=sys.stderr)
        return {"status": "error", "message": str(e)}


@mcp.tool
def add_custom_scrub_words(batch_id: str, words: List[str]) -> dict:
    """
    Add custom words/names to scrub for a batch.
    These will be scrubbed in addition to roster names and detected names.

    Args:
        batch_id: The batch ID
        words: List of words to scrub (e.g., ["Kaitlyn", "Mr. Cooper"])

    Returns:
        Confirmation with word count
    """
    try:
        batch = DB_MANAGER.get_scrub_batch(batch_id)
        if not batch:
            return {"status": "error", "message": f"Batch not found: {batch_id}"}

        cleaned_words = [w.strip() for w in words if w and w.strip()]

        # Merge with existing
        existing = DB_MANAGER.get_batch_custom_scrub_words(batch_id)
        merged = list(set(existing + cleaned_words))

        DB_MANAGER.set_batch_custom_scrub_words(batch_id, merged)

        return {
            "status": "success",
            "batch_id": batch_id,
            "words_saved": len(merged),
            "words": merged,
            "message": f"Saved {len(merged)} custom scrub word(s) for batch {batch_id}",
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


@mcp.tool
def get_custom_scrub_words(batch_id: str) -> dict:
    """
    Retrieve custom scrub words for a batch.

    Args:
        batch_id: The batch ID

    Returns:
        List of custom scrub words
    """
    try:
        batch = DB_MANAGER.get_scrub_batch(batch_id)
        if not batch:
            return {"status": "error", "message": f"Batch not found: {batch_id}"}

        words = DB_MANAGER.get_batch_custom_scrub_words(batch_id)
        return {
            "status": "success",
            "batch_id": batch_id,
            "word_count": len(words),
            "words": words,
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


@mcp.tool
def re_scrub_batch(batch_id: str) -> dict:
    """
    Re-scrub all documents in a batch. Use after adding custom scrub words
    or correcting names.

    Args:
        batch_id: The batch ID

    Returns:
        Summary of re-scrubbing operation
    """
    return scrub_batch(batch_id)


@mcp.tool
def get_batch_statistics(batch_id: str) -> dict:
    """
    Returns a manifest of the batch's documents for inspection.

    Args:
        batch_id: The batch ID to inspect

    Returns:
        Dictionary with list of students, page counts, and word counts
    """
    docs = DB_MANAGER.get_batch_documents(batch_id)
    if not docs:
        return {"status": "warning", "message": f"No documents found for batch {batch_id}"}

    manifest = []
    for doc in docs:
        raw_text = doc.get("raw_text", "")
        word_count = len(raw_text.split()) if raw_text else 0
        metadata = doc.get("metadata", {}) or {}
        page_count = metadata.get("page_count", "N/A")

        manifest.append({
            "doc_id": doc["id"],
            "student_name": doc["student_name"],
            "page_count": page_count,
            "word_count": word_count,
            "status": doc["status"],
        })

    return {
        "status": "success",
        "batch_id": batch_id,
        "total_documents": len(docs),
        "manifest": manifest,
    }


@mcp.tool
def list_batches() -> dict:
    """
    List all scrub batches.

    Returns:
        List of batches with their status
    """
    try:
        batches = DB_MANAGER.list_scrub_batches()
        return {
            "status": "success",
            "count": len(batches),
            "batches": batches,
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


@mcp.tool
def get_batch_documents(batch_id: str) -> dict:
    """
    Get all documents in a batch with scrubbed text.
    This is the primary read method for downstream servers.

    Args:
        batch_id: The batch ID

    Returns:
        List of documents with scrubbed text, student name, and metadata
    """
    try:
        docs = DB_MANAGER.get_batch_documents(batch_id)
        summary = []
        for doc in docs:
            summary.append({
                "doc_id": doc["id"],
                "student_name": doc["student_name"],
                "status": doc["status"],
                "scrubbed_text": doc["scrubbed_text"],
                "metadata": doc["metadata"],
            })
        return {
            "status": "success",
            "batch_id": batch_id,
            "count": len(summary),
            "documents": summary,
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


@mcp.tool
def get_scrubbed_document(doc_id: int) -> dict:
    """
    Get a single scrubbed document by ID.

    Args:
        doc_id: The document ID

    Returns:
        Full document details including scrubbed text
    """
    try:
        doc = DB_MANAGER.get_scrubbed_document(doc_id)
        if not doc:
            return {"status": "error", "message": f"Document not found: {doc_id}"}

        return {
            "status": "success",
            "document": {
                "doc_id": doc["id"],
                "batch_id": doc["batch_id"],
                "student_name": doc["student_name"],
                "status": doc["status"],
                "scrubbed_text": doc["scrubbed_text"],
                "metadata": doc["metadata"],
            },
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


if __name__ == "__main__":
    mcp.run()
