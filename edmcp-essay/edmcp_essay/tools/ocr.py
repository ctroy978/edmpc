import base64
import io
import os
import sys
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, List, Iterable, Union

import regex
from pdf2image import convert_from_path
from pypdf import PdfReader
from openai import OpenAI
from edmcp_core import write_jsonl, DatabaseManager

# Reuse patterns from original implementation
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
        self.parts = []

    def append_page(self, text: str, page_number: int) -> None:
        self.parts.append(text)
        self.start_page = min(self.start_page, page_number)
        self.end_page = max(self.end_page, page_number)

    def to_dict(self, original_file: str, job_id: str) -> dict:
        # Use form feed (\f) as page separator for MLA header scrubbing
        return {
            "job_id": job_id,
            "student_name": self.student_name,
            "text": "\f".join(self.parts),
            "metadata": {
                "source_file": original_file,
                "start_page": self.start_page,
                "end_page": self.end_page,
                "page_count": self.end_page - self.start_page + 1,
            },
        }


class OCRTool:
    def __init__(
        self,
        job_dir: Optional[Union[str, Path]] = None,
        job_id: Optional[str] = None,
        db_manager: Optional[DatabaseManager] = None,
        student_roster: Optional[set] = None,
    ):
        self.job_dir = Path(job_dir) if job_dir else None
        self.job_id = job_id or (self.job_dir.name if self.job_dir else "generic_job")
        self.db_manager = db_manager
        self.student_roster = student_roster or set()
        self.client = self._get_client()
        self.model = os.environ.get("QWEN_API_MODEL", "qwen-vl-max")

    def _get_client(self) -> OpenAI:
        api_key = os.environ.get("QWEN_API_KEY") or os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise ValueError(
                "QWEN_API_KEY or OPENAI_API_KEY environment variable is required."
            )

        base_url = os.environ.get("QWEN_BASE_URL")
        if not base_url:
            if api_key.startswith("sk-or-"):
                base_url = "https://openrouter.ai/api/v1"
            else:
                base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"

        return OpenAI(api_key=api_key, base_url=base_url)

    def detect_name(self, text: str) -> Optional[str]:
        lines = text.splitlines()[:10]
        top_section = "\n".join(lines)

        # First, try the traditional "Name:" or "ID:" pattern
        match = NAME_HEADER_PATTERN.search(top_section)
        if match:
            return regex.sub(r"\s+", " ", match.group(1).strip())

        # If no match and we have a student roster, check against it
        if self.student_roster:
            for line in lines:
                # Normalize the line for case-insensitive comparison
                normalized_line = regex.sub(r"\s+", " ", line.strip()).casefold()

                # Check if this line matches any full student name in the roster
                if normalized_line in self.student_roster:
                    return regex.sub(r"\s+", " ", line.strip())

        return None

    def detect_continuation(self, text: str) -> Optional[str]:
        top_section = "\n".join(text.splitlines()[:10])
        match = CONTINUE_HEADER_PATTERN.search(top_section)
        return match.group(1).strip() if match else None

    @staticmethod
    def extract_text_from_pdf(pdf_path: Union[str, Path]) -> Optional[List[str]]:
        """
        Try to extract text directly from PDF (for typed/digital PDFs).
        Returns list of page texts if successful, None if PDF is scanned/image-based.
        """
        try:
            reader = PdfReader(str(pdf_path))
            page_texts = []
            total_chars = 0

            for page in reader.pages:
                text = page.extract_text()
                page_texts.append(text)
                total_chars += len(text.strip())

            # Check if we got meaningful text (not just whitespace)
            # Heuristic: if average is less than 10 chars per page, probably scanned
            avg_chars_per_page = total_chars / len(reader.pages) if reader.pages else 0

            if avg_chars_per_page < 10:
                print(f"[PDF Extract] {pdf_path.name if isinstance(pdf_path, Path) else pdf_path}: Low text content ({avg_chars_per_page:.0f} chars/page), using OCR", file=sys.stderr)
                return None

            print(f"[PDF Extract] {pdf_path.name if isinstance(pdf_path, Path) else pdf_path}: Text extracted successfully ({avg_chars_per_page:.0f} chars/page)", file=sys.stderr)
            return page_texts

        except Exception as e:
            print(f"[PDF Extract] {pdf_path.name if isinstance(pdf_path, Path) else pdf_path}: Text extraction failed ({e}), using OCR", file=sys.stderr)
            return None

    def ocr_image(self, image_bytes: bytes) -> str:
        base64_image = base64.b64encode(image_bytes).decode("utf-8")
        try:
            response = self.client.chat.completions.create(
                model=self.model,
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

    def process_pdf(
        self,
        pdf_path: Union[str, Path],
        dpi: int = 220,
        unknown_prefix: str = "Unknown Student",
    ) -> dict:
        """
        Process a PDF file and return processing results with metadata.

        Returns:
            dict with keys:
                - output_path: Path to JSONL results
                - used_ocr: bool indicating if OCR was used
                - student_count: number of students found
        """
        if self.job_dir is None:
            raise ValueError("job_dir is required for process_pdf")

        pdf_path = Path(pdf_path)
        page_results = []
        used_ocr = False

        # Try text extraction first (fast, free)
        extracted_texts = self.extract_text_from_pdf(pdf_path)

        if extracted_texts:
            # Success! Use extracted text (no OCR needed)
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
            # Text extraction failed, fall back to OCR (slow, expensive)
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
        records = [agg.to_dict(str(pdf_path), self.job_id) for agg in aggregates]

        # Write to JSONL (Backup/Handoff)
        output_path = self.job_dir / "ocr_results.jsonl"
        write_jsonl(output_path, records, append=True)

        # Write to DB if manager is present
        if self.db_manager:
            for record in records:
                self.db_manager.add_essay(
                    job_id=self.job_id,
                    student_name=record["student_name"],
                    raw_text=record["text"],
                    metadata=record["metadata"],
                )

        return {
            "output_path": output_path,
            "used_ocr": used_ocr,
            "student_count": len(aggregates),
        }

    def _aggregate_pages(
        self, pages: List[PageResult], unknown_prefix: str
    ) -> List[TestAggregate]:
        # Simple implementation for now, porting the core logic
        aggregates = []
        current = None
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

    def extract_text_via_ocr(self, pdf_path: Union[str, Path], dpi: int = 220) -> str:
        """
        Extracts all text from a PDF file using OCR, without any student name aggregation.
        Useful for ingesting reference materials (rubrics, textbooks) that might be scanned.
        NOTE: This always uses OCR. For typed PDFs, use extract_text_from_pdf() instead.
        """
        pdf_path = Path(pdf_path)
        if not pdf_path.exists():
            raise FileNotFoundError(f"File not found: {pdf_path}")

        images = convert_from_path(str(pdf_path), dpi=dpi)
        full_text_parts = []

        for image in images:
            buffered = io.BytesIO()
            image.convert("RGB").save(buffered, format="JPEG", quality=85)
            # Use the existing ocr_image method which handles the API call
            text = self.ocr_image(buffered.getvalue())
            full_text_parts.append(text)

        return "\n\n".join(full_text_parts)
