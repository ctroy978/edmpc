
import json
import csv
import os
import shutil
from pathlib import Path
from typing import List, Dict, Any
from reportlab.lib.pagesizes import LETTER
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak


class ReportGenerator:
    """
    Handles generation of CSV gradebooks and individual student feedback PDFs.
    PDFs are stored in the database for persistence and emailing, and also
    written to the filesystem for AI agent/teacher download.
    """

    def __init__(self, output_base_dir: str = "data/reports", db_manager=None):
        self.output_base_dir = Path(output_base_dir).resolve()  # Convert to absolute path
        self.output_base_dir.mkdir(parents=True, exist_ok=True)
        self.styles = getSampleStyleSheet()
        self.db_manager = db_manager

    def _get_job_dir(self, job_id: str) -> Path:
        job_dir = self.output_base_dir / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        return job_dir

    def generate_csv_gradebook(self, job_id: str, essays: List[Dict[str, Any]]) -> str:
        """
        Generates a CSV gradebook for a specific job.
        Returns the path to the generated CSV file.
        """
        job_dir = self._get_job_dir(job_id)
        csv_path = job_dir / f"{job_id}_gradebook.csv"

        if not essays:
            return ""

        # Determine all unique criteria names across all essays for headers
        criteria_names = []
        for essay in essays:
            eval_data = self._parse_evaluation(essay.get('evaluation'))
            if eval_data and 'criteria' in eval_data:
                for crit in eval_data['criteria']:
                    name = crit.get('name')
                    if name and name not in criteria_names:
                        criteria_names.append(name)

        headers = ['Student Name', 'Overall Score', 'Grade Status'] + criteria_names + ['Summary']

        with open(csv_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            writer.writeheader()

            for essay in essays:
                eval_data = self._parse_evaluation(essay.get('evaluation'))
                row = {
                    'Student Name': essay.get('student_name', 'Unknown'),
                    'Overall Score': essay.get('grade', ''),
                    'Grade Status': essay.get('status', ''),
                    'Summary': eval_data.get('summary', '') if eval_data else ''
                }

                if eval_data and 'criteria' in eval_data:
                    for crit in eval_data['criteria']:
                        row[crit.get('name')] = crit.get('score', '')

                writer.writerow(row)

        # Store CSV in database if db_manager is available
        if self.db_manager:
            with open(csv_path, 'rb') as f:
                csv_content = f.read()
            self.db_manager.store_report(
                job_id=job_id,
                report_type='gradebook_csv',
                filename=f"{job_id}_gradebook.csv",
                content=csv_content,
                essay_id=None
            )

        return str(csv_path.resolve())

    def generate_student_feedback_pdfs(self, job_id: str, essays: List[Dict[str, Any]]) -> str:
        """
        Generates individual PDF feedback reports for each student.
        Stores PDFs in the database AND writes them to the filesystem.
        Returns the path to the directory containing the PDFs.
        """
        job_dir = self._get_job_dir(job_id)
        pdf_dir = job_dir / "feedback_pdfs"
        pdf_dir.mkdir(parents=True, exist_ok=True)

        for essay in essays:
            student_name = essay.get('student_name', 'Unknown Student').replace(' ', '_')
            essay_id = essay.get('id', 'unknown')
            pdf_path = pdf_dir / f"{student_name}_{essay_id}.pdf"

            # Generate PDF to filesystem
            self._create_student_pdf(essay, pdf_path)

            # Also store in database if db_manager is available
            if self.db_manager and essay_id != 'unknown':
                with open(pdf_path, 'rb') as f:
                    pdf_content = f.read()
                self.db_manager.store_report(
                    job_id=job_id,
                    report_type='student_pdf',
                    filename=f"{student_name}_{essay_id}.pdf",
                    content=pdf_content,
                    essay_id=essay_id
                )

        return str(pdf_dir.resolve())

    def zip_directory(self, dir_path: str, zip_name: str, job_id: str = None) -> str:
        """
        Zips a directory and returns the path to the ZIP file.
        Optionally stores in database if job_id and db_manager are provided.
        """
        dir_to_zip = Path(dir_path)
        if not dir_to_zip.exists():
            return ""

        # create_archive adds .zip extension automatically
        zip_base_path = dir_to_zip.parent / zip_name
        shutil.make_archive(str(zip_base_path), 'zip', str(dir_to_zip))

        zip_path = Path(str(zip_base_path) + ".zip")

        # Store ZIP in database if db_manager and job_id are available
        if self.db_manager and job_id:
            with open(zip_path, 'rb') as f:
                zip_content = f.read()
            self.db_manager.store_report(
                job_id=job_id,
                report_type='student_feedback_zip',
                filename=zip_path.name,
                content=zip_content,
                essay_id=None
            )

        return str(zip_path.resolve())

    def _parse_evaluation(self, eval_json: Any) -> Dict[str, Any]:
        if not eval_json:
            return {}
        if isinstance(eval_json, dict):
            return eval_json
        try:
            return json.loads(eval_json)
        except json.JSONDecodeError:
            return {}

    def _create_student_pdf(self, essay: Dict[str, Any], pdf_path: Path):
        """Creates a professional PDF report for a single student."""
        doc = SimpleDocTemplate(str(pdf_path), pagesize=LETTER)
        elements = []

        eval_data = self._parse_evaluation(essay.get('evaluation'))
        student_name = essay.get('student_name', 'Unknown Student')
        overall_score = essay.get('grade', 'N/A')

        # Header
        elements.append(Paragraph(f"Student Feedback Report", self.styles['Title']))
        elements.append(Spacer(1, 12))
        elements.append(Paragraph(f"<b>Student Name:</b> {student_name}", self.styles['Normal']))
        elements.append(Paragraph(f"<b>Overall Score:</b> {overall_score}", self.styles['Normal']))
        elements.append(Spacer(1, 24))

        # Overall Summary
        if eval_data.get('summary'):
            elements.append(Paragraph("<b>Overall Summary</b>", self.styles['Heading2']))
            elements.append(Paragraph(eval_data['summary'], self.styles['Normal']))
            elements.append(Spacer(1, 12))

        # Criteria Breakdown
        if eval_data.get('criteria'):
            elements.append(Paragraph("<b>Detailed Criteria Breakdown</b>", self.styles['Heading2']))
            elements.append(Spacer(1, 6))

            for crit in eval_data['criteria']:
                elements.append(Paragraph(f"<b>{crit.get('name', 'Criterion')}: {crit.get('score', 'N/A')}</b>", self.styles['Heading3']))

                feedback = crit.get('feedback', {})
                if isinstance(feedback, dict):
                    # Justification (why this score was given)
                    justification = feedback.get('justification')
                    if justification:
                        elements.append(Paragraph(justification, self.styles['Normal']))
                        elements.append(Spacer(1, 6))

                    # Examples/Quotes
                    quotes = feedback.get('examples', [])
                    if quotes:
                        elements.append(Paragraph("<i>Evidence from Essay:</i>", self.styles['Italic']))
                        for q in quotes:
                            # Use f-string carefully or just concatenation
                            elements.append(Paragraph(f"• \"{q}\"", self.styles['Normal']))

                    # Advice
                    advice = feedback.get('advice')
                    if advice:
                        elements.append(Paragraph(f"<b>Advice for Improvement:</b> {advice}", self.styles['Normal']))

                    # Rewritten Example
                    rewrite = feedback.get('rewritten_example')
                    if rewrite:
                        elements.append(Paragraph(f"<b>Suggested Revision:</b> {rewrite}", self.styles['Normal']))

                elements.append(Spacer(1, 12))

        # Original Text (Optional/Appendix)
        elements.append(PageBreak())
        elements.append(Paragraph("<b>Appendix: Submitted Text</b>", self.styles['Heading2']))
        elements.append(Spacer(1, 12))

        # Preserve whitespace for the essay text
        essay_text = essay.get('normalized_text') or essay.get('scrubbed_text') or essay.get('raw_text') or ""
        text_style = ParagraphStyle('EssayText', parent=self.styles['Normal'], fontName='Helvetica', leading=14)
        # Replace form feeds (page separators) with visual separator, then newlines with line breaks
        essay_text_formatted = essay_text.replace('\f', '<br/><br/>--- Page Break ---<br/><br/>').replace('\n', '<br/>')
        elements.append(Paragraph(essay_text_formatted, text_style))

        doc.build(elements)
