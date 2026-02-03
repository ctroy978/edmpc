"""
Exporter - Export tests and answer keys to PDF and other formats.
"""

import io
from typing import Any, Dict, Optional

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    PageBreak,
)
from reportlab.lib.enums import TA_LEFT, TA_CENTER

from edmcp_testgen.core.test_job_manager import TestJobManager
from edmcp_testgen.core.formatter import Formatter


class Exporter:
    """Export tests and answer keys to various formats."""

    def __init__(self, job_manager: TestJobManager, formatter: Formatter):
        self.job_manager = job_manager
        self.formatter = formatter

    def _get_styles(self) -> Dict[str, ParagraphStyle]:
        """Get paragraph styles for PDF generation."""
        base_styles = getSampleStyleSheet()

        styles = {
            "title": ParagraphStyle(
                "title",
                parent=base_styles["Title"],
                fontSize=18,
                spaceAfter=12,
                alignment=TA_CENTER,
            ),
            "subtitle": ParagraphStyle(
                "subtitle",
                parent=base_styles["Normal"],
                fontSize=12,
                spaceAfter=6,
                alignment=TA_CENTER,
                textColor=colors.gray,
            ),
            "section": ParagraphStyle(
                "section",
                parent=base_styles["Heading2"],
                fontSize=14,
                spaceBefore=18,
                spaceAfter=8,
                textColor=colors.darkblue,
            ),
            "instructions": ParagraphStyle(
                "instructions",
                parent=base_styles["Normal"],
                fontSize=10,
                spaceAfter=12,
                textColor=colors.gray,
                fontName="Helvetica-Oblique",
            ),
            "question": ParagraphStyle(
                "question",
                parent=base_styles["Normal"],
                fontSize=11,
                spaceBefore=8,
                spaceAfter=4,
                leftIndent=0,
            ),
            "option": ParagraphStyle(
                "option",
                parent=base_styles["Normal"],
                fontSize=10,
                leftIndent=24,
                spaceBefore=2,
            ),
            "answer": ParagraphStyle(
                "answer",
                parent=base_styles["Normal"],
                fontSize=10,
                leftIndent=24,
                textColor=colors.darkgreen,
            ),
            "rubric": ParagraphStyle(
                "rubric",
                parent=base_styles["Normal"],
                fontSize=9,
                leftIndent=36,
                textColor=colors.gray,
            ),
            "header_info": ParagraphStyle(
                "header_info",
                parent=base_styles["Normal"],
                fontSize=11,
                spaceBefore=12,
                spaceAfter=6,
            ),
        }

        return styles

    def export_test_pdf(
        self,
        job_id: str,
        include_header: bool = True,
        include_name_line: bool = True,
    ) -> bytes:
        """
        Export the test as a PDF.

        Args:
            job_id: The job ID
            include_header: Whether to include test title and info
            include_name_line: Whether to include name/date lines

        Returns:
            PDF bytes
        """
        test_data = self.formatter.format_test(job_id)
        if test_data.get("status") == "error":
            raise ValueError(test_data.get("message", "Failed to format test"))

        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=LETTER,
            leftMargin=0.75 * inch,
            rightMargin=0.75 * inch,
            topMargin=0.75 * inch,
            bottomMargin=0.75 * inch,
        )

        styles = self._get_styles()
        story = []

        # Header
        if include_header:
            story.append(Paragraph(test_data.get("name", "Test"), styles["title"]))
            if test_data.get("description"):
                story.append(Paragraph(test_data["description"], styles["subtitle"]))
            story.append(
                Paragraph(
                    f"Total Points: {test_data.get('total_points', 0)}",
                    styles["subtitle"],
                )
            )
            story.append(Spacer(1, 12))

        # Name/Date lines
        if include_name_line:
            story.append(
                Paragraph(
                    "Name: __________________________ &nbsp;&nbsp;&nbsp; Date: ______________",
                    styles["header_info"],
                )
            )
            story.append(Spacer(1, 18))

        # Sections
        for section in test_data.get("sections", []):
            story.append(Paragraph(section.get("title", "Questions"), styles["section"]))
            if section.get("instructions"):
                story.append(
                    Paragraph(section["instructions"], styles["instructions"])
                )

            for q in section.get("questions", []):
                q_num = q.get("number", "?")
                points = q.get("points")
                points_str = f" ({points} pts)" if points else ""

                # Question text
                story.append(
                    Paragraph(
                        f"<b>{q_num}.</b>{points_str} {q.get('text', '')}",
                        styles["question"],
                    )
                )

                # Options for MCQ
                if q.get("type") == "mcq" and q.get("options"):
                    for opt in q["options"]:
                        story.append(
                            Paragraph(
                                f"{opt.get('letter', '?')}. {opt.get('text', '')}",
                                styles["option"],
                            )
                        )

                # Space for answer (SA questions)
                if q.get("type") == "sa":
                    story.append(Spacer(1, 48))

                story.append(Spacer(1, 8))

        # Word bank if present
        if test_data.get("word_bank"):
            story.append(Spacer(1, 18))
            story.append(Paragraph("Word Bank", styles["section"]))
            word_bank_text = "&nbsp;&nbsp;|&nbsp;&nbsp;".join(test_data["word_bank"])
            story.append(Paragraph(word_bank_text, styles["instructions"]))

        doc.build(story)
        return buffer.getvalue()

    def export_answer_key_pdf(
        self,
        job_id: str,
        include_rubrics: bool = True,
    ) -> bytes:
        """
        Export the answer key as a PDF.

        Args:
            job_id: The job ID
            include_rubrics: Whether to include rubrics for SA questions

        Returns:
            PDF bytes
        """
        key_data = self.formatter.format_answer_key(job_id, include_rubrics=include_rubrics)
        if key_data.get("status") == "error":
            raise ValueError(key_data.get("message", "Failed to format answer key"))

        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=LETTER,
            leftMargin=0.75 * inch,
            rightMargin=0.75 * inch,
            topMargin=0.75 * inch,
            bottomMargin=0.75 * inch,
        )

        styles = self._get_styles()
        story = []

        # Header
        story.append(Paragraph(key_data.get("name", "Answer Key"), styles["title"]))
        story.append(
            Paragraph(
                f"Total Points: {key_data.get('total_points', 0)}",
                styles["subtitle"],
            )
        )
        story.append(Spacer(1, 18))

        # Quick reference section for MCQ/FIB
        mcq_fib = [a for a in key_data.get("answers", []) if a.get("type") in ("mcq", "fib")]
        if mcq_fib:
            story.append(Paragraph("Quick Reference", styles["section"]))

            # Create a table for quick answers
            table_data = []
            row = []
            for i, answer in enumerate(mcq_fib):
                q_num = answer.get("number", "?")
                correct = answer.get("correct_answer", "?")
                row.append(f"{q_num}. {correct}")
                if len(row) == 5 or i == len(mcq_fib) - 1:
                    table_data.append(row)
                    row = []

            if table_data:
                t = Table(table_data, colWidths=[1.3 * inch] * 5)
                t.setStyle(
                    TableStyle(
                        [
                            ("FONTSIZE", (0, 0), (-1, -1), 10),
                            ("ALIGN", (0, 0), (-1, -1), "LEFT"),
                            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                        ]
                    )
                )
                story.append(t)
                story.append(Spacer(1, 12))

        # Detailed answers
        story.append(Paragraph("Detailed Answers", styles["section"]))

        for answer in key_data.get("answers", []):
            q_num = answer.get("number", "?")
            q_type = answer.get("type", "unknown")
            points = answer.get("points", 1)

            if q_type == "mcq":
                story.append(
                    Paragraph(
                        f"<b>{q_num}.</b> ({points} pts) <b>{answer.get('correct_answer', '?')}</b>",
                        styles["question"],
                    )
                )

            elif q_type == "fib":
                story.append(
                    Paragraph(
                        f"<b>{q_num}.</b> ({points} pts) <b>{answer.get('correct_answer', '?')}</b>",
                        styles["question"],
                    )
                )

            elif q_type == "sa":
                story.append(
                    Paragraph(
                        f"<b>{q_num}.</b> ({points} pts)",
                        styles["question"],
                    )
                )
                # Question text
                if answer.get("question_text"):
                    story.append(
                        Paragraph(
                            f"<i>Q: {answer['question_text'][:200]}{'...' if len(answer.get('question_text', '')) > 200 else ''}</i>",
                            styles["option"],
                        )
                    )
                # Model answer
                story.append(
                    Paragraph(
                        f"<b>Model Answer:</b> {answer.get('model_answer', '')}",
                        styles["answer"],
                    )
                )
                # Rubric
                if include_rubrics and answer.get("rubric"):
                    rubric = answer["rubric"]
                    story.append(
                        Paragraph(
                            f"<b>Rubric</b> ({rubric.get('total_points', points)} pts):",
                            styles["rubric"],
                        )
                    )
                    for criterion in rubric.get("criteria", []):
                        story.append(
                            Paragraph(
                                f"• {criterion.get('name')}: {criterion.get('points')} pts",
                                styles["rubric"],
                            )
                        )

            story.append(Spacer(1, 6))

        doc.build(story)
        return buffer.getvalue()

    def export_to_files(
        self,
        job_id: str,
        output_dir: str,
    ) -> Dict[str, Any]:
        """
        Export test and answer key to files in a directory.

        Args:
            job_id: The job ID
            output_dir: Directory to save files

        Returns:
            Dict with file paths
        """
        from pathlib import Path

        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        job = self.job_manager.get_job(job_id)
        if not job:
            return {"status": "error", "message": f"Job not found: {job_id}"}

        # Generate safe filename
        safe_name = "".join(c if c.isalnum() or c in " -_" else "_" for c in job.get("name", "test"))
        safe_name = safe_name.strip()[:50] or "test"

        # Export test PDF
        test_pdf_path = output_path / f"{safe_name}_test.pdf"
        test_pdf = self.export_test_pdf(job_id)
        with open(test_pdf_path, "wb") as f:
            f.write(test_pdf)

        # Export answer key PDF
        key_pdf_path = output_path / f"{safe_name}_answer_key.pdf"
        key_pdf = self.export_answer_key_pdf(job_id)
        with open(key_pdf_path, "wb") as f:
            f.write(key_pdf)

        # Export text versions
        test_txt_path = output_path / f"{safe_name}_test.txt"
        with open(test_txt_path, "w") as f:
            f.write(self.formatter.format_test_text(job_id))

        key_txt_path = output_path / f"{safe_name}_answer_key.txt"
        with open(key_txt_path, "w") as f:
            f.write(self.formatter.format_answer_key_text(job_id))

        return {
            "status": "success",
            "job_id": job_id,
            "output_dir": str(output_path),
            "files": {
                "test_pdf": str(test_pdf_path),
                "answer_key_pdf": str(key_pdf_path),
                "test_txt": str(test_txt_path),
                "answer_key_txt": str(key_txt_path),
            },
        }
