"""
Document intake pipeline for edmcp-scrub.
Handles PDF text extraction, OCR fallback, name detection, and page aggregation.
Extracted from edmcp-essay's OCRTool and server.py processing logic.
"""

import base64
import io
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, List, Union

import regex
import pdfplumber
from pdf2image import convert_from_path
from pypdf import PdfReader
from openai import OpenAI

from edmcp_core import DatabaseManager

# Name detection patterns (same as edmcp-essay)
NAME_HEADER_PATTERN = regex.compile(
    r"(?im)^\s*(?:name|id)\s*[:\-]\s*([\p{L}][\p{L}'-]*(?:\s+[\p{L}][\p{L}'-]*)?)"
)
CONTINUE_HEADER_PATTERN = regex.compile(r"(?im)^\s*continue\s*[:\-]\s*(.+)$")


@dataclass
class PageResult:
    number: int
    text: str
    detected_name: Optional[str]
    continuation_name: Optional[str]


class TestAggregate:
    def __init__(self, student_name: str, start_page: int):
        self.student_name = student_name
        self.start_page = start_page
        self.end_page = start_page
        self.parts: list[str] = []

    def append_page(self, text: str, page_number: int) -> None:
        self.parts.append(text.strip())
        self.start_page = min(self.start_page, page_number)
        self.end_page = max(self.end_page, page_number)

    def to_dict(self, original_file: str) -> dict:
        return {
            "student_name": self.student_name,
            "text": "\n\n".join(self.parts),
            "metadata": {
                "source_file": original_file,
                "start_page": self.start_page,
                "end_page": self.end_page,
                "page_count": self.end_page - self.start_page + 1,
            },
        }


class DocumentProcessor:
    """
    Processes PDF documents: extracts text, detects student names,
    aggregates pages per student, and stores in DB.
    """

    def __init__(
        self,
        db_manager: DatabaseManager,
        student_roster: Optional[set] = None,
    ):
        self.db_manager = db_manager
        self.student_roster = student_roster or set()
        self._ocr_client: Optional[OpenAI] = None
        self._ocr_model: Optional[str] = None

    def _get_ocr_client(self) -> OpenAI:
        """Lazy-init OCR client (only needed for scanned PDFs)."""
        if self._ocr_client is None:
            api_key = os.environ.get("QWEN_API_KEY") or os.environ.get("OPENAI_API_KEY")
            if not api_key:
                raise ValueError(
                    "QWEN_API_KEY or OPENAI_API_KEY environment variable is required for OCR."
                )
            base_url = os.environ.get("QWEN_BASE_URL")
            if not base_url:
                if api_key.startswith("sk-or-"):
                    base_url = "https://openrouter.ai/api/v1"
                else:
                    base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"

            self._ocr_client = OpenAI(api_key=api_key, base_url=base_url)
            self._ocr_model = os.environ.get("QWEN_API_MODEL", "qwen-vl-max")

        return self._ocr_client

    def detect_name(self, text: str) -> Optional[str]:
        """Detect student name in the top portion of text."""
        lines = text.splitlines()[:10]
        top_section = "\n".join(lines)

        # Try "Name:" or "ID:" header pattern
        match = NAME_HEADER_PATTERN.search(top_section)
        if match:
            return regex.sub(r"\s+", " ", match.group(1).strip())

        # Check against student roster
        if self.student_roster:
            for line in lines:
                normalized_line = regex.sub(r"\s+", " ", line.strip()).casefold()
                if normalized_line in self.student_roster:
                    return regex.sub(r"\s+", " ", line.strip())

        return None

    def detect_continuation(self, text: str) -> Optional[str]:
        """Detect CONTINUE markers that reference the original student name."""
        top_section = "\n".join(text.splitlines()[:10])
        match = CONTINUE_HEADER_PATTERN.search(top_section)
        return match.group(1).strip() if match else None

    @staticmethod
    def _extract_page_text_pdfplumber(page) -> str:
        """
        Extract text from a single pdfplumber page with paragraph-aware formatting.

        Uses word bounding boxes to detect line boundaries and gap analysis to
        distinguish paragraph breaks (large vertical gap) from line breaks (small gap).
        """
        words = page.extract_words(
            x_tolerance=3,
            y_tolerance=3,
            keep_blank_chars=False,
            use_text_flow=True,
        )
        if not words:
            return ""

        # Group words into lines by clustering on their `top` (y) coordinate.
        # Words within 3pt of each other vertically are on the same line.
        lines: list[list[dict]] = []
        current_line: list[dict] = []
        current_top = words[0]["top"]

        for word in words:
            if abs(word["top"] - current_top) <= 3:
                current_line.append(word)
            else:
                lines.append(current_line)
                current_line = [word]
                current_top = word["top"]
        if current_line:
            lines.append(current_line)

        if len(lines) < 2:
            return " ".join(w["text"] for w in lines[0]) if lines else ""

        # Compute top-to-top gaps between consecutive lines.
        tops = [line[0]["top"] for line in lines]
        gaps = [tops[i + 1] - tops[i] for i in range(len(tops) - 1)]

        # Median gap represents normal line spacing; 1.5× is the paragraph threshold.
        sorted_gaps = sorted(gaps)
        median_gap = sorted_gaps[len(sorted_gaps) // 2]
        para_threshold = median_gap * 1.5

        # Build the text, joining words on each line and inserting \n\n for paragraphs.
        line_texts = [" ".join(w["text"] for w in line) for line in lines]
        parts = [line_texts[0]]
        for i, gap in enumerate(gaps):
            separator = "\n\n" if gap >= para_threshold else "\n"
            parts.append(separator + line_texts[i + 1])

        return "".join(parts)

    @staticmethod
    def extract_text_from_pdf(pdf_path: Union[str, Path]) -> Optional[List[str]]:
        """
        Try native text extraction from PDF (for typed/digital PDFs).
        Returns list of page texts if successful, None if scanned/image-based.

        Uses pdfplumber for accurate layout analysis and paragraph detection.
        Falls back to pypdf if pdfplumber fails.
        """
        pdf_path = Path(pdf_path)
        try:
            page_texts = []
            total_chars = 0

            with pdfplumber.open(str(pdf_path)) as pdf:
                for page in pdf.pages:
                    text = DocumentProcessor._extract_page_text_pdfplumber(page)
                    page_texts.append(text)
                    total_chars += len(text.strip())

            num_pages = len(page_texts)
            avg_chars_per_page = total_chars / num_pages if num_pages else 0

            if avg_chars_per_page < 10:
                print(
                    f"[PDF Extract] {pdf_path.name}: Low text content "
                    f"({avg_chars_per_page:.0f} chars/page), using OCR",
                    file=sys.stderr,
                )
                return None

            print(
                f"[PDF Extract] {pdf_path.name}: Text extracted successfully "
                f"({avg_chars_per_page:.0f} chars/page)",
                file=sys.stderr,
            )
            return page_texts

        except Exception as e:
            print(
                f"[PDF Extract] {pdf_path.name}: pdfplumber failed ({e}), trying pypdf",
                file=sys.stderr,
            )

        # Fallback to pypdf
        try:
            reader = PdfReader(str(pdf_path))
            page_texts = []
            total_chars = 0

            for page in reader.pages:
                text = page.extract_text() or ""
                page_texts.append(text)
                total_chars += len(text.strip())

            avg_chars_per_page = total_chars / len(reader.pages) if reader.pages else 0

            if avg_chars_per_page < 10:
                print(
                    f"[PDF Extract] {pdf_path.name}: Low text content (pypdf fallback), using OCR",
                    file=sys.stderr,
                )
                return None

            print(
                f"[PDF Extract] {pdf_path.name}: Extracted via pypdf fallback "
                f"({avg_chars_per_page:.0f} chars/page)",
                file=sys.stderr,
            )
            return page_texts

        except Exception as e:
            print(
                f"[PDF Extract] {pdf_path.name}: Text extraction failed ({e}), using OCR",
                file=sys.stderr,
            )
            return None

    def ocr_image(self, image_bytes: bytes) -> str:
        """OCR a single image using Qwen VL."""
        client = self._get_ocr_client()
        base64_image = base64.b64encode(image_bytes).decode("utf-8")
        try:
            response = client.chat.completions.create(
                model=self._ocr_model,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": "Extract all text from this document image. "
                                "Preserve paragraph breaks as blank lines between paragraphs. "
                                "Return only the text found in the image. "
                                "Do not add any introductory or concluding remarks.",
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

    def process_pdf(
        self,
        pdf_path: Union[str, Path],
        dpi: int = 220,
        unknown_prefix: str = "Unknown Student",
    ) -> dict:
        """
        Process a single PDF: extract text, detect names, aggregate by student.

        Returns:
            dict with keys: used_ocr, student_count, records (list of dicts)
        """
        pdf_path = Path(pdf_path)
        page_results = []
        used_ocr = False

        # Try native text extraction first
        extracted_texts = self.extract_text_from_pdf(pdf_path)

        if extracted_texts:
            for i, text in enumerate(extracted_texts, 1):
                page_results.append(
                    PageResult(
                        number=i,
                        text=text,
                        detected_name=self.detect_name(text),
                        continuation_name=self.detect_continuation(text),
                    )
                )
        else:
            # Fallback to OCR
            used_ocr = True
            print(f"[OCR] {pdf_path.name}: Using OCR for scanned/image PDF", file=sys.stderr)
            images = convert_from_path(str(pdf_path), dpi=dpi)

            for i, image in enumerate(images, 1):
                buffered = io.BytesIO()
                image.convert("RGB").save(buffered, format="JPEG", quality=85)
                text = self.ocr_image(buffered.getvalue())

                page_results.append(
                    PageResult(
                        number=i,
                        text=text,
                        detected_name=self.detect_name(text),
                        continuation_name=self.detect_continuation(text),
                    )
                )

        aggregates = self._aggregate_pages(page_results, unknown_prefix)
        records = [agg.to_dict(str(pdf_path)) for agg in aggregates]

        return {
            "used_ocr": used_ocr,
            "student_count": len(aggregates),
            "records": records,
        }

    def batch_process(
        self,
        directory_path: str,
        batch_id: str,
        dpi: int = 220,
    ) -> dict:
        """
        Process all PDFs in a directory and store documents in a batch.

        Returns:
            dict with processing summary
        """
        input_path = Path(directory_path)
        if not input_path.exists():
            raise FileNotFoundError(f"Directory not found: {directory_path}")

        files = sorted(list(input_path.glob("*.[pP][dD][fF]")))
        if not files:
            raise FileNotFoundError(f"No PDF files found in {directory_path}")

        files_processed = 0
        files_using_ocr = 0
        files_using_text = 0
        errors = []

        print(f"[Scrub] Starting batch {batch_id}: Found {len(files)} files", file=sys.stderr)

        for file_path in files:
            try:
                print(f"[Scrub] Processing {file_path.name}...", file=sys.stderr)
                result = self.process_pdf(file_path, dpi=dpi)

                # Store each student's document in DB
                for record in result["records"]:
                    self.db_manager.add_scrubbed_document(
                        batch_id=batch_id,
                        student_name=record["student_name"],
                        raw_text=record["text"],
                        metadata=record["metadata"],
                    )

                if result["used_ocr"]:
                    files_using_ocr += 1
                else:
                    files_using_text += 1

                files_processed += 1

            except Exception as e:
                error_msg = f"{file_path.name}: {str(e)}"
                print(f"[Scrub] Error processing {file_path.name}: {e}", file=sys.stderr)
                errors.append(error_msg)

        docs = self.db_manager.get_batch_documents(batch_id)
        students_found = len(docs)

        print(
            f"[Scrub] Batch {batch_id} complete. "
            f"{files_processed}/{len(files)} files, {students_found} students.",
            file=sys.stderr,
        )

        method_parts = []
        if files_using_text > 0:
            method_parts.append(f"{files_using_text} via text extraction")
        if files_using_ocr > 0:
            method_parts.append(f"{files_using_ocr} via OCR")
        method_str = " and ".join(method_parts) if method_parts else "unknown method"

        return {
            "files_processed": files_processed,
            "students_found": students_found,
            "processing_method": method_str,
            "text_extraction": files_using_text,
            "ocr": files_using_ocr,
            "errors": errors if errors else None,
        }

    def _aggregate_pages(
        self, pages: List[PageResult], unknown_prefix: str
    ) -> List[TestAggregate]:
        """Group pages by student, handling continuations."""
        aggregates: list[TestAggregate] = []
        current: Optional[TestAggregate] = None
        unknown_counter = 0

        for page in pages:
            if page.detected_name:
                if current:
                    aggregates.append(current)
                current = TestAggregate(page.detected_name, page.number)
                current.append_page(page.text, page.number)
            elif (
                page.continuation_name
                and current
                and current.student_name.lower() == page.continuation_name.lower()
            ):
                current.append_page(page.text, page.number)
            elif current:
                current.append_page(page.text, page.number)
            else:
                unknown_counter += 1
                current = TestAggregate(
                    f"{unknown_prefix} {unknown_counter:02d}", page.number
                )
                current.append_page(page.text, page.number)

        if current:
            aggregates.append(current)
        return aggregates
