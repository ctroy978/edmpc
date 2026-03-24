"""
Report Generator - Produces standalone HTML student feedback reports.
"""

import csv
import json
import re
import shutil
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any, Dict, List, Optional

from edmcp_regrade.core.regrade_job_manager import RegradeJobManager


def _normalize_essay_text(raw_text: str) -> str:
    """Mirror the UI normalization so annotation text-matching works correctly.

    The review UI applies this same normalization before displaying the essay,
    so teacher quotes are drawn from the normalized text.  The report generator
    must normalize the stored essay_text in the same way, otherwise
    `text.find(selected_text)` fails for word-per-line PDF sources.
    """
    text = raw_text.replace("\f", "\n\n")
    text = re.sub(r"(?:\s*\n){3,}", "\n\n", text)

    pages = text.split("\n\n")

    normalized: List[str] = []
    for page in pages:
        page = page.strip()
        if not page:
            continue

        # Collapse space-only blank lines (pypdf word-per-line artifact: "word\n \nword")
        # to single newlines so they don't generate fake paragraph breaks.
        page = re.sub(r"\n([ \t]*\n)+", "\n", page)

        lines = page.split("\n")
        non_blank = [l for l in lines if l.strip()]
        if not non_blank:
            continue

        avg_len = sum(len(l) for l in non_blank) / len(non_blank)
        if avg_len > 200:
            normalized.append(" ".join(page.split()))
            continue

        lengths = [len(l.rstrip()) for l in non_blank if len(l.strip()) > 20]
        if len(lengths) < 3:
            normalized.append(" ".join(page.split()))
            continue

        typical = sorted(lengths)[int(len(lengths) * 0.75)]
        threshold = typical * 0.65

        rebuilt: List[str] = []
        for i, line in enumerate(lines):
            stripped = line.rstrip()
            rebuilt.append(stripped)
            if i >= len(lines) - 1:
                continue
            if stripped == "":
                rebuilt.append("")
                continue
            next_line = lines[i + 1].strip()
            is_short = len(stripped.strip()) > 0 and len(stripped.rstrip()) < threshold
            ends_sentence = bool(re.search(r'[.!?"\'\u201d)]\s*$', stripped))
            if is_short and ends_sentence and next_line:
                rebuilt.append("")

        page_text = "\n".join(rebuilt)
        page_text = re.sub(r"(?<!\n)\n(?!\n)", " ", page_text)
        page_text = re.sub(r" {2,}", " ", page_text)
        normalized.append(page_text.strip())

    return "\n\n".join(normalized)


def _try_get_generated_flag(teacher_comments_raw: str) -> bool:
    """Return True if teacher_comments JSON has report_generated: true."""
    try:
        parsed = json.loads(teacher_comments_raw)
        return isinstance(parsed, dict) and bool(parsed.get("report_generated"))
    except (json.JSONDecodeError, TypeError):
        return False


class ReportGenerator:
    """Generates self-contained HTML feedback reports for students."""

    def __init__(self, job_manager: RegradeJobManager):
        self.job_manager = job_manager

    def _load_identity_map(self, job_id: str) -> Dict[str, str]:
        """Return a mapping of anon_id → real student name from job metadata."""
        raw = self.job_manager.get_metadata(job_id, "identity_map")
        if not raw or not isinstance(raw, dict):
            return {}
        result = {}
        for anon_id, entry in raw.items():
            if isinstance(entry, dict):
                name = entry.get("student_name", "")
                if name:
                    result[anon_id] = name
        return result

    def generate_student_report(
        self, job_id: str, essay_id: int
    ) -> Dict[str, Any]:
        """Generate a standalone HTML report for a single essay."""
        job = self.job_manager.get_job(job_id)
        if not job:
            return {"status": "error", "message": f"Job not found: {job_id}"}

        essay = self.job_manager.get_essay(essay_id)
        if not essay:
            return {"status": "error", "message": f"Essay not found: {essay_id}"}
        if essay["job_id"] != job_id:
            return {"status": "error", "message": "Essay does not belong to this job"}

        # Resolve anonymized ID to real student name via the job's identity map
        anon_id = essay.get("student_identifier", "")
        name_map = self._load_identity_map(job_id)
        if anon_id in name_map:
            essay = dict(essay)
            essay["student_identifier"] = name_map[anon_id]

        html = self._build_html(job, essay)

        return {
            "status": "success",
            "job_id": job_id,
            "essay_id": essay_id,
            "student_identifier": essay.get("student_identifier", "Unknown"),
            "html": html,
        }

    def _detoken_essay(self, essay: Dict[str, Any], real_name: str) -> Dict[str, Any]:
        """Replace [STUDENT_NAME] tokens with the real student name in all text fields."""
        if not real_name or real_name == "Unknown":
            return essay
        placeholder = "[STUDENT_NAME]"
        essay = dict(essay)  # shallow copy — we'll replace individual keys
        if essay.get("essay_text"):
            essay["essay_text"] = essay["essay_text"].replace(placeholder, real_name)
        if essay.get("evaluation"):
            eval_json = json.dumps(essay["evaluation"]).replace(placeholder, real_name)
            essay["evaluation"] = json.loads(eval_json)
        if essay.get("teacher_comments"):
            essay["teacher_comments"] = essay["teacher_comments"].replace(placeholder, real_name)
        return essay

    def _build_html(self, job: Dict[str, Any], essay: Dict[str, Any]) -> str:
        """Build the complete standalone HTML document."""
        real_name = essay.get("student_identifier") or "Unknown"
        essay = self._detoken_essay(essay, real_name)
        student = escape(real_name)
        job_name = escape(job.get("name") or "")
        assignment = escape(job.get("assignment_title") or "")

        # Determine final grade (teacher override or AI grade)
        final_grade = escape(essay.get("teacher_grade") or essay.get("grade") or "N/A")

        # Build sections
        rubric_section = self._build_rubric_section(essay)
        comments_section = self._build_comments_section(essay)
        essay_section = self._build_essay_section(essay)

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Feedback: {student} - {assignment or job_name}</title>
<style>
{self._get_css()}
</style>
</head>
<body>
<div class="container">
    <header>
        <h1>Essay Feedback Report</h1>
        <div class="meta">
            <div class="meta-item"><strong>Student:</strong> {student}</div>
            {"<div class='meta-item'><strong>Assignment:</strong> " + assignment + "</div>" if assignment else ""}
            {"<div class='meta-item'><strong>Class:</strong> " + escape(job.get('class_name') or '') + "</div>" if job.get('class_name') else ""}
            <div class="meta-item"><strong>Final Grade:</strong> <span class="grade">{final_grade}</span></div>
        </div>
    </header>

    {rubric_section}
    {comments_section}
    {essay_section}

    <footer>
        <p>Generated by Essay Regrade System</p>
    </footer>
</div>
</body>
</html>"""

    def _build_rubric_section(self, essay: Dict[str, Any]) -> str:
        """Build the unified feedback section as cards.

        One section, card format throughout:
        - First card: the merged teacher/AI overall feedback prose
        - Remaining cards: per-criterion breakdown with teacher score overrides applied
        """
        eval_data = essay.get("evaluation")
        if not eval_data or not isinstance(eval_data, dict):
            return ""

        criteria = eval_data.get("criteria", [])
        if not criteria:
            return ""

        # Extract teacher score overrides
        teacher_overrides: dict = {}
        tc_raw = essay.get("teacher_comments") or ""
        if tc_raw:
            try:
                parsed_tc = json.loads(tc_raw)
                if isinstance(parsed_tc, dict):
                    for o in parsed_tc.get("criteria_overrides", []):
                        cname = o.get("name", "")
                        cscore = o.get("score", "")
                        if cname and cscore:
                            teacher_overrides[cname] = cscore
            except (json.JSONDecodeError, TypeError):
                pass

        cards = []

        # Per-criterion cards with teacher score overrides applied
        for c in criteria:
            raw_name = str(c.get("name", ""))
            name = escape(raw_name)
            ai_score = str(c.get("score", ""))
            score = escape(teacher_overrides.get(raw_name, ai_score))

            feedback = c.get("feedback", {})
            if isinstance(feedback, dict):
                ai_explanation = str(feedback.get("explanation", "")
                                     or feedback.get("justification", ""))
            else:
                ai_explanation = str(feedback) if feedback else ""

<<<<<<< HEAD
            justification = escape(ai_justification)
=======
            # Use blended justification if available, otherwise fall back to AI explanation
            explanation = escape(blended_justification_map.get(raw_name, ai_explanation))
>>>>>>> echoRubric

            explanation_html = (
                f'<p class="card-explanation">{explanation}</p>'
                if explanation else ""
            )

            cards.append(
                '<div class="feedback-card">'
                '<div class="card-header">'
                f'<span class="card-name">{name}</span>'
                f'<span class="card-score">{score}</span>'
                '</div>'
                f'{explanation_html}'
                '</div>'
            )

        return (
            '<section class="rubric-section">'
            '<h2>Feedback</h2>'
            + "".join(cards)
            + '</section>'
        )

    def _build_comments_section(self, essay: Dict[str, Any]) -> str:
<<<<<<< HEAD
        """Build the teacher comments section shown below the rubric cards.

        Displays AI-polished teacher notes (refined_teacher_notes) if available,
        falling back to raw teacher_notes. Returns empty string if no notes exist.
        """
        tc_raw = essay.get("teacher_comments") or ""
        notes_text = ""
        if tc_raw:
            try:
                parsed_tc = json.loads(tc_raw)
                if isinstance(parsed_tc, dict):
                    notes_text = parsed_tc.get("refined_teacher_notes") or parsed_tc.get("teacher_notes") or ""
            except (json.JSONDecodeError, TypeError):
                notes_text = tc_raw

        if not notes_text or not notes_text.strip():
            return ""

        return (
            '<section class="teacher-comments-section">'
            '<h2>Teacher Comments</h2>'
            '<div class="teacher-comments-box">'
            f'<p>{escape(notes_text.strip())}</p>'
            '</div>'
=======
        """Render refined teacher notes as a 'Teacher Comments' section if available."""
        tc_raw = essay.get("teacher_comments") or ""
        if not tc_raw:
            return ""
        try:
            parsed = json.loads(tc_raw)
        except (json.JSONDecodeError, TypeError):
            return ""
        if not isinstance(parsed, dict):
            return ""
        if not parsed.get("report_generated"):
            return ""
        refined_notes = parsed.get("refined_teacher_notes", "")
        if not refined_notes or not str(refined_notes).strip():
            return ""
        return (
            '<section class="comments-section">'
            '<h2>Teacher Comments</h2>'
            f'<p>{escape(str(refined_notes))}</p>'
>>>>>>> echoRubric
            '</section>'
        )

    def _build_essay_section(self, essay: Dict[str, Any]) -> str:
        """Build the annotated essay section with highlighted passages."""
        essay_text = essay.get("essay_text", "")
        if not essay_text:
            return ""

        # Normalize to match what the teacher sees in the review UI, so that
        # annotation selected_text can be found via text.find().
        essay_text = _normalize_essay_text(essay_text)

        annotations = essay.get("teacher_annotations")
        if isinstance(annotations, str):
            try:
                annotations = json.loads(annotations)
            except json.JSONDecodeError:
                annotations = None

        # Build annotated HTML
        annotated_html = self._apply_annotations(essay_text, annotations)

        return f"""
    <section class="essay-section">
        <h2>Student Essay</h2>
        <div class="essay-text">{annotated_html}</div>
    </section>"""

    def _apply_annotations(self, text: str, annotations: Optional[List[Dict[str, Any]]]) -> str:
        """Apply highlight annotations to essay text. Falls back to plain escaped text."""
        if not annotations or not isinstance(annotations, list):
            # No annotations — just escape and preserve paragraphs
            paragraphs = text.split("\n\n")
            return "".join(f"<p>{escape(p.strip())}</p>" for p in paragraphs if p.strip())

        # Build a list of (start, end, comment) for matched annotations
        matches = []
        for ann in annotations:
            selected = ann.get("selected_text", "")
            comment = ann.get("comment", "")
            if not selected:
                continue

            idx = text.find(selected)
            if idx == -1:
                # Try case-insensitive
                idx = text.lower().find(selected.lower())
            if idx >= 0:
                matches.append((idx, idx + len(selected), comment))

        # Sort by position, resolve overlaps (keep first)
        matches.sort(key=lambda m: m[0])
        filtered = []
        last_end = 0
        for start, end, comment in matches:
            if start >= last_end:
                filtered.append((start, end, comment))
                last_end = end

        # Build HTML with highlights
        parts = []
        pos = 0
        for start, end, comment in filtered:
            # Text before this highlight
            if start > pos:
                segment = text[pos:start]
                parts.append(self._text_to_html(segment))

            # Highlighted text
            highlighted = escape(text[start:end])
            tooltip = escape(comment)
            parts.append(
                f'<span class="highlight" data-tooltip="{tooltip}">{highlighted}</span>'
            )
            pos = end

        # Remaining text
        if pos < len(text):
            parts.append(self._text_to_html(text[pos:]))

        return "".join(parts)

    def _text_to_html(self, text: str) -> str:
        """Convert plain text segment to HTML with paragraph breaks."""
        paragraphs = text.split("\n\n")
        result = []
        for p in paragraphs:
            stripped = p.strip()
            if stripped:
                result.append(f"<p>{escape(stripped)}</p>")
        return "".join(result) if result else escape(text)

    def generate_gradebook_csv(self, job_id: str, output_dir: Path) -> str:
        """
        Generate a CSV gradebook for a job.

        Columns: Student Name, Final Score, <criterion_1>, <criterion_2>, ...
        Final Score uses teacher_grade if set, otherwise AI grade.
        Per-criterion scores use teacher overrides (from teacher_comments JSON) when available.

        Returns the path to the generated CSV file, or "" on failure.
        """
        essays = self.job_manager.get_job_essays(job_id, include_text=True)
        if not essays:
            return ""

        name_map = self._load_identity_map(job_id)

        # Discover all unique criteria names in order of first appearance
        criteria_names: List[str] = []
        for essay in essays:
            eval_data = essay.get("evaluation")
            if not eval_data or not isinstance(eval_data, dict):
                continue
            for c in eval_data.get("criteria", []):
                name = c.get("name")
                if name and name not in criteria_names:
                    criteria_names.append(name)

        headers = ["Student Name", "Final Score"] + criteria_names

        output_dir.mkdir(parents=True, exist_ok=True)
        csv_path = output_dir / f"{job_id}_gradebook.csv"

        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=headers, extrasaction="ignore")
            writer.writeheader()

            for essay in essays:
                anon_id = essay.get("student_identifier", "Unknown")
                student = name_map.get(anon_id, anon_id)
                final_score = essay.get("teacher_grade") or essay.get("grade") or ""

                # Extract per-criterion teacher overrides from teacher_comments JSON
                teacher_overrides: Dict[str, str] = {}
                tc_raw = essay.get("teacher_comments") or ""
                if tc_raw:
                    try:
                        parsed_tc = json.loads(tc_raw)
                        if isinstance(parsed_tc, dict):
                            for o in parsed_tc.get("criteria_overrides", []):
                                cname = o.get("name", "")
                                cscore = o.get("score", "")
                                if cname and cscore:
                                    teacher_overrides[cname] = cscore
                    except (json.JSONDecodeError, TypeError):
                        pass

                row: Dict[str, Any] = {"Student Name": student, "Final Score": final_score}

                eval_data = essay.get("evaluation")
                if eval_data and isinstance(eval_data, dict):
                    for c in eval_data.get("criteria", []):
                        cname = c.get("name", "")
                        ai_score = str(c.get("score", ""))
                        row[cname] = teacher_overrides.get(cname, ai_score)

                writer.writerow(row)

        return str(csv_path)

    def package_evaluation_reports(self, job_id: str, output_base: Path) -> Dict[str, Any]:
        """
        Bundle all student HTML feedback reports + a gradebook CSV into a ZIP archive.

        Only includes essays with status GRADED, REVIEWED, or APPROVED.
        Returns a dict with status, zip_path, report_count, and csv_path.
        """
        job = self.job_manager.get_job(job_id)
        if not job:
            return {"status": "error", "message": f"Job not found: {job_id}"}

        essays = self.job_manager.get_job_essays(job_id, include_text=True)
        if not essays:
            return {"status": "error", "message": "No essays found for this job"}

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        staging = output_base / f"{job_id}_package_{timestamp}"
        staging.mkdir(parents=True, exist_ok=True)

        report_count = 0
        skipped = []
        name_map = self._load_identity_map(job_id)

        try:
            for essay in essays:
                if essay.get("status") not in ("GRADED", "REVIEWED", "APPROVED"):
                    anon = essay.get("student_identifier", f"id:{essay['id']}")
                    skipped.append(name_map.get(anon, anon))
                    continue

                essay_id = essay["id"]
                anon_id = essay.get("student_identifier") or "unknown"
                student = name_map.get(anon_id, anon_id).replace(" ", "_")
                html_result = self.generate_student_report(job_id, essay_id)

                if html_result.get("status") == "success":
                    html_path = staging / f"{student}_feedback.html"
                    html_path.write_text(html_result["html"], encoding="utf-8")
                    report_count += 1

<<<<<<< HEAD
            # Generate gradebook CSV into the output directory (not staging, which is deleted)
            csv_path = self.generate_gradebook_csv(job_id, output_base)
=======
            # Generate gradebook CSV into the staging directory (included in zip)
            csv_staging_path = self.generate_gradebook_csv(job_id, staging)
>>>>>>> echoRubric

            # Zip the staging directory
            zip_base = output_base / f"{job_id}_reports_{timestamp}"
            zip_path = shutil.make_archive(str(zip_base), "zip", str(staging))

            # Copy CSV to output_base so it survives staging cleanup
            csv_path = ""
            if csv_staging_path:
                dest = output_base / Path(csv_staging_path).name
                shutil.copy2(csv_staging_path, dest)
                csv_path = str(dest)

            return {
                "status": "success",
                "zip_path": zip_path,
                "csv_path": csv_path,
                "report_count": report_count,
                "skipped": skipped,
            }

        finally:
            if staging.exists():
                shutil.rmtree(staging)

    def _get_css(self) -> str:
        return """
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
    font-family: Georgia, 'Times New Roman', serif;
    line-height: 1.6;
    color: #333;
    background: #f5f5f5;
}
.container {
    max-width: 800px;
    margin: 2rem auto;
    background: #fff;
    padding: 2rem 3rem;
    box-shadow: 0 2px 8px rgba(0,0,0,0.1);
    border-radius: 4px;
}
header { margin-bottom: 2rem; border-bottom: 2px solid #2c3e50; padding-bottom: 1rem; }
h1 { color: #2c3e50; font-size: 1.6rem; margin-bottom: 0.5rem; }
h2 { color: #2c3e50; font-size: 1.3rem; margin: 1.5rem 0 0.75rem; }
h3 { font-size: 1.1rem; margin: 0.75rem 0 0.5rem; }
.meta { display: flex; flex-wrap: wrap; gap: 1rem; font-size: 0.95rem; }
.meta-item { }
.grade { font-weight: bold; color: #2c3e50; font-size: 1.1em; }

/* Feedback cards */
.feedback-card {
    border: 1px solid #e2e8f0;
    border-radius: 8px;
    padding: 12px 14px;
    margin-bottom: 10px;
    background: #f8fafc;
}
.card-header {
    display: flex;
    align-items: center;
    gap: 10px;
    margin-bottom: 6px;
}
.card-name {
    font-weight: 600;
    font-size: 1em;
    color: #1e293b;
}
.card-score {
    background: #3b82f6;
    color: #fff;
    font-size: 0.8em;
    font-weight: bold;
    padding: 2px 8px;
    border-radius: 12px;
    white-space: nowrap;
}
.card-justification {
    margin: 0 0 4px 0;
    font-size: 0.9em;
    color: #334155;
    line-height: 1.7;
}
.card-advice {
    margin: 0 0 4px 0;
    font-size: 0.9em;
    color: #1d4ed8;
}
.card-quote {
    margin: 6px 0 0 0;
    padding: 6px 10px;
    border-left: 3px solid #94a3b8;
    color: #64748b;
    font-style: italic;
    font-size: 0.85em;
}

/* Teacher Comments Section */
.teacher-comments-section { margin-top: 1.5rem; }
.teacher-comments-box {
    background: #fefce8;
    border-left: 4px solid #ca8a04;
    border-radius: 0 6px 6px 0;
    padding: 14px 18px;
    font-size: 0.95em;
    color: #1c1917;
    line-height: 1.7;
}
.teacher-comments-box p { margin: 0; }

/* Essay Section */
.essay-section { margin-top: 1.5rem; }
.essay-text { background: #fafafa; border: 1px solid #eee; border-radius: 4px; padding: 1.5rem; }
.essay-text p { margin-bottom: 1rem; text-indent: 2rem; }
.highlight {
    position: relative;
    background: #fff3b0;
    border-bottom: 2px solid #d4a017;
    cursor: help;
    padding: 0 2px;
}
.highlight::after {
    content: attr(data-tooltip);
    position: absolute;
    bottom: calc(100% + 8px);
    left: 50%;
    transform: translateX(-50%);
    background: #2c3e50;
    color: #fff;
    padding: 8px 12px;
    border-radius: 6px;
    font-size: 0.85em;
    font-family: Georgia, serif;
    line-height: 1.5;
    white-space: pre-wrap;
    max-width: 280px;
    min-width: 120px;
    text-align: left;
    z-index: 1000;
    display: none;
    pointer-events: none;
    box-shadow: 0 2px 8px rgba(0,0,0,0.25);
}
.highlight::before {
    content: '';
    position: absolute;
    bottom: calc(100% + 2px);
    left: 50%;
    transform: translateX(-50%);
    border: 6px solid transparent;
    border-top-color: #2c3e50;
    z-index: 1001;
    display: none;
    pointer-events: none;
}
.highlight:hover { background: #ffe066; }
.highlight:hover::after { display: block; }
.highlight:hover::before { display: block; }

footer { margin-top: 2rem; padding-top: 1rem; border-top: 1px solid #eee; text-align: center; font-size: 0.8rem; color: #999; }

@media (max-width: 600px) {
    .container { padding: 1rem; margin: 0; }
    .meta { flex-direction: column; gap: 0.3rem; }
}
"""
