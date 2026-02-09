"""
Highlighter tool for generating annotated HTML feedback reports.

Combines the feedback report (grades, criteria breakdown) with an AI-annotated
version of the student's essay featuring highlighted passages with hover tooltips.
"""

import json
import os
import re
import shutil
import sys
import difflib
from html import escape
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

from openai import (
    APITimeoutError,
    APIConnectionError,
    RateLimitError,
    InternalServerError,
)

from edmcp_core import (
    DatabaseManager,
    get_openai_client,
    retry_with_backoff,
    extract_json_from_text,
)

AI_RETRIABLE_EXCEPTIONS = (
    APITimeoutError,
    APIConnectionError,
    RateLimitError,
    InternalServerError,
)

HIGHLIGHT_SYSTEM_PROMPT = "You are an expert writing coach for high school and college essays."

HIGHLIGHT_USER_PROMPT = """Read the student essay below. Do two things:

1. EXTRACT ESSAY BODY: Return only the essay text from the introduction paragraph through the conclusion. Exclude any front matter such as the student's name, date, teacher name, class/period, or title header. Return the body EXACTLY as written — do not fix errors or change wording.

2. IDENTIFY HIGHLIGHTS: Find exactly 5-6 specific passages in the essay body that could be improved. Focus on the most impactful issues:
   - Grammar or spelling mistakes
   - Unclear or awkward sentences that need rewriting for clarity
   - Weak transitions between ideas or paragraphs
   - Poor word choice
   - Structural issues (paragraph organization, topic sentences)
   - Weak or missing evidence/support

   For each highlight, quote the EXACT text (verbatim, including any errors) and provide a brief, constructive suggestion (1-2 sentences).

Return ONLY valid JSON:
{{
  "essay_body": "exact essay text from intro to conclusion",
  "highlights": [
    {{
      "quote": "exact substring from essay_body",
      "category": "grammar|clarity|transitions|word_choice|structure|evidence",
      "suggestion": "constructive advice"
    }}
  ]
}}

STUDENT ESSAY:
{scrubbed_text}"""

CATEGORY_COLORS = {
    "grammar": ("#fff3cd", "#856404", "Grammar"),
    "clarity": ("#cce5ff", "#004085", "Clarity"),
    "transitions": ("#d4edda", "#155724", "Transitions"),
    "word_choice": ("#f8d7da", "#721c24", "Word Choice"),
    "structure": ("#e2d5f1", "#4a235a", "Structure"),
    "evidence": ("#d1ecf1", "#0c5460", "Evidence"),
}


class HighlighterTool:
    """Generates annotated HTML feedback reports with highlighted essay passages."""

    def __init__(self, db_manager: DatabaseManager, report_base_dir: str = "data/reports"):
        self.db_manager = db_manager
        self.report_base_dir = Path(report_base_dir).resolve()
        self.report_base_dir.mkdir(parents=True, exist_ok=True)

    def _get_job_dir(self, job_id: str) -> Path:
        job_dir = self.report_base_dir / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        return job_dir

    # ── Text normalization ─────────────────────────────────────────────

    @staticmethod
    def _normalize_pdf_text(text: str) -> str:
        """Clean up PDF text-extraction artifacts.

        Collapses single newlines (line-wrapping) into spaces while
        preserving double-newlines (paragraph breaks).
        """
        text = re.sub(r"(?:\s*\n){3,}", "\n\n", text)     # 3+ newlines (with spaces between) → paragraph break
        text = re.sub(r"(?<!\n)\n(?!\n)", " ", text)     # single \n → space
        text = re.sub(r" {2,}", " ", text)               # collapse multiple spaces
        return text.strip()

    # ── AI highlight call ──────────────────────────────────────────────

    VALID_CATEGORIES = frozenset(CATEGORY_COLORS.keys())

    @staticmethod
    @retry_with_backoff(retries=3, exceptions=AI_RETRIABLE_EXCEPTIONS)
    def _call_highlight_api(client, model: str, scrubbed_text: str) -> str:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": HIGHLIGHT_SYSTEM_PROMPT},
                {"role": "user", "content": HIGHLIGHT_USER_PROMPT.format(scrubbed_text=scrubbed_text)},
            ],
            temperature=0.2,
            max_tokens=4000,
            response_format={"type": "json_object"},
        )
        return response.choices[0].message.content.strip()

    def get_highlights(
        self, scrubbed_text: str, client, model: str
    ) -> Dict[str, Any]:
        """Call AI to get essay body and highlight annotations.

        Always returns a dict with 'essay_body' (str) and 'highlights' (list).
        Falls back to the original text with no highlights on any failure.
        """
        fallback = {"essay_body": scrubbed_text, "highlights": []}

        raw = self._call_highlight_api(client, model, scrubbed_text)
        data = extract_json_from_text(raw)
        if not data or not isinstance(data, dict):
            print("[Highlighter] AI response did not contain valid JSON", file=sys.stderr)
            return fallback

        # -- Validate essay_body --
        essay_body = data.get("essay_body")
        if not isinstance(essay_body, str) or not essay_body.strip():
            print("[Highlighter] AI returned empty or non-string essay_body, using original", file=sys.stderr)
            essay_body = scrubbed_text

        # Guard against the AI rewriting the essay: the returned body
        # must share at least 80% of its content with the original.
        similarity = difflib.SequenceMatcher(None, scrubbed_text, essay_body).ratio()
        if similarity < 0.80:
            print(
                f"[Highlighter] AI essay_body diverged from original (similarity={similarity:.2f}), using original",
                file=sys.stderr,
            )
            essay_body = scrubbed_text

        # -- Validate highlights --
        raw_highlights = data.get("highlights")
        if not isinstance(raw_highlights, list):
            print("[Highlighter] AI returned non-list highlights, discarding", file=sys.stderr)
            return {"essay_body": essay_body, "highlights": []}

        valid_highlights = []
        for i, hl in enumerate(raw_highlights):
            if not isinstance(hl, dict):
                continue
            quote = hl.get("quote")
            suggestion = hl.get("suggestion")
            category = hl.get("category", "")
            if not isinstance(quote, str) or not quote.strip():
                print(f"[Highlighter] Highlight {i}: missing/empty quote, skipping", file=sys.stderr)
                continue
            if not isinstance(suggestion, str) or not suggestion.strip():
                print(f"[Highlighter] Highlight {i}: missing/empty suggestion, skipping", file=sys.stderr)
                continue
            if category not in self.VALID_CATEGORIES:
                print(f"[Highlighter] Highlight {i}: invalid category '{category}', defaulting to 'grammar'", file=sys.stderr)
                category = "grammar"
            valid_highlights.append({
                "quote": quote,
                "category": category,
                "suggestion": suggestion,
            })

        return {"essay_body": essay_body, "highlights": valid_highlights}

    # ── Quote matching ─────────────────────────────────────────────────

    @staticmethod
    def _normalize_whitespace(text: str) -> str:
        return re.sub(r"\s+", " ", text).strip()

    @classmethod
    def _find_quote_position(cls, essay_body: str, quote: str) -> Optional[Tuple[int, int]]:
        """3-tier fallback: exact → whitespace-normalized → fuzzy (0.85)."""
        # Tier 1: exact match
        idx = essay_body.find(quote)
        if idx != -1:
            return (idx, idx + len(quote))

        # Tier 2: whitespace-normalized
        norm_body = cls._normalize_whitespace(essay_body)
        norm_quote = cls._normalize_whitespace(quote)
        norm_idx = norm_body.find(norm_quote)
        if norm_idx != -1:
            # Map normalized position back to original
            orig_start = cls._map_normalized_pos_to_original(essay_body, norm_body, norm_idx)
            orig_end = cls._map_normalized_pos_to_original(essay_body, norm_body, norm_idx + len(norm_quote))
            if orig_start is not None and orig_end is not None:
                return (orig_start, orig_end)

        # Tier 3: fuzzy match using SequenceMatcher
        return cls._fuzzy_find(essay_body, quote, threshold=0.85)

    @staticmethod
    def _map_normalized_pos_to_original(original: str, normalized: str, norm_pos: int) -> Optional[int]:
        """Map a position in whitespace-normalized text back to the original."""
        orig_i = 0
        norm_i = 0
        while orig_i < len(original) and norm_i < norm_pos:
            if re.match(r"\s", original[orig_i]):
                # Consume all whitespace in original, only one in normalized
                while orig_i < len(original) and re.match(r"\s", original[orig_i]):
                    orig_i += 1
                norm_i += 1  # The single space in normalized
            else:
                orig_i += 1
                norm_i += 1
        return orig_i if norm_i == norm_pos else None

    @staticmethod
    def _fuzzy_find(essay_body: str, quote: str, threshold: float = 0.85) -> Optional[Tuple[int, int]]:
        """Slide a window over essay_body looking for best fuzzy match."""
        quote_len = len(quote)
        if quote_len == 0:
            return None

        best_ratio = 0.0
        best_span = None

        # Check windows of varying size around the quote length
        for window_size in range(max(1, quote_len - 20), quote_len + 21):
            if window_size > len(essay_body):
                continue
            for start in range(0, len(essay_body) - window_size + 1, max(1, window_size // 4)):
                candidate = essay_body[start : start + window_size]
                ratio = difflib.SequenceMatcher(None, quote, candidate).ratio()
                if ratio > best_ratio:
                    best_ratio = ratio
                    best_span = (start, start + window_size)

        if best_ratio >= threshold and best_span is not None:
            return best_span
        return None

    @classmethod
    def match_highlights(
        cls, essay_body: str, highlights: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Match highlight quotes to positions, resolve overlaps, return sorted list."""
        matched = []
        for hl in highlights:
            quote = hl.get("quote", "")
            if not quote:
                continue
            pos = cls._find_quote_position(essay_body, quote)
            if pos is None:
                print(f"[Highlighter] Warning: could not find quote: {quote[:60]}...", file=sys.stderr)
                continue
            matched.append({
                "start": pos[0],
                "end": pos[1],
                "category": hl.get("category", "grammar"),
                "suggestion": hl.get("suggestion", ""),
                "quote": quote,
            })

        # Sort by start position
        matched.sort(key=lambda h: h["start"])

        # Resolve overlaps: keep earlier, discard later
        resolved = []
        for hl in matched:
            if resolved and hl["start"] < resolved[-1]["end"]:
                print(f"[Highlighter] Discarding overlapping highlight: {hl['quote'][:40]}...", file=sys.stderr)
                continue
            resolved.append(hl)

        return resolved

    # ── HTML generation ────────────────────────────────────────────────

    @staticmethod
    def _parse_evaluation(eval_json) -> Dict[str, Any]:
        if not eval_json:
            return {}
        if isinstance(eval_json, dict):
            return eval_json
        try:
            return json.loads(eval_json)
        except (json.JSONDecodeError, TypeError):
            return {}

    def build_feedback_html(
        self,
        essay_data: Dict[str, Any],
        highlights: List[Dict[str, Any]],
        essay_body: str,
    ) -> str:
        """Build a self-contained HTML feedback report with annotated essay.

        Args:
            essay_data: Essay record from DB (with evaluation, grade, student_name, etc.)
            highlights: List of matched highlights with start/end positions
            essay_body: The AI-extracted essay body text
        """
        student_name = essay_data.get("student_name", "Unknown Student")
        overall_score = essay_data.get("grade", "N/A")
        eval_data = self._parse_evaluation(essay_data.get("evaluation"))
        summary = eval_data.get("summary", "")
        criteria = eval_data.get("criteria", [])

        # Build feedback report section
        report_html = self._build_report_section(student_name, overall_score, summary, criteria)

        # Build annotated essay section
        essay_html = self._build_annotated_essay(essay_body, highlights)

        # Determine which categories are present
        used_categories = {h["category"] for h in highlights}
        legend_html = self._build_legend(used_categories)

        return self._wrap_full_document(student_name, report_html, legend_html, essay_html)

    @staticmethod
    def _build_report_section(
        student_name: str,
        overall_score: str,
        summary: str,
        criteria: List[Dict[str, Any]],
    ) -> str:
        parts = []
        parts.append(f'<h1>Student Feedback Report</h1>')
        parts.append(f'<p><strong>Student Name:</strong> {escape(student_name)}</p>')
        parts.append(f'<p><strong>Overall Score:</strong> {escape(str(overall_score))}</p>')

        if summary:
            parts.append(f'<h2>Overall Summary</h2>')
            parts.append(f'<p>{escape(summary)}</p>')

        if criteria:
            parts.append(f'<h2>Detailed Criteria Breakdown</h2>')
            for crit in criteria:
                name = escape(str(crit.get("name", "Criterion")))
                score = escape(str(crit.get("score", "N/A")))
                parts.append(f'<h3>{name}: {score}</h3>')

                feedback = crit.get("feedback", {})
                if isinstance(feedback, dict):
                    justification = feedback.get("justification")
                    if justification:
                        parts.append(f'<p>{escape(justification)}</p>')

                    examples = feedback.get("examples", [])
                    if examples:
                        parts.append('<p><em>Evidence from Essay:</em></p><ul>')
                        for ex in examples:
                            parts.append(f'<li>&ldquo;{escape(str(ex))}&rdquo;</li>')
                        parts.append('</ul>')

                    advice = feedback.get("advice")
                    if advice:
                        parts.append(f'<p><strong>Advice for Improvement:</strong> {escape(advice)}</p>')

                    rewrite = feedback.get("rewritten_example")
                    if rewrite:
                        parts.append(f'<p><strong>Suggested Revision:</strong> {escape(rewrite)}</p>')

        return "\n".join(parts)

    @staticmethod
    def _build_annotated_essay(essay_body: str, highlights: List[Dict[str, Any]]) -> str:
        """Segment-based approach: split at highlight boundaries, escape each chunk."""
        if not essay_body:
            return "<p><em>No essay text available.</em></p>"

        segments = []
        prev_end = 0

        for hl in highlights:
            # Plain text before this highlight
            if hl["start"] > prev_end:
                plain = essay_body[prev_end : hl["start"]]
                segments.append(escape(plain))

            # Highlighted text
            highlighted_text = essay_body[hl["start"] : hl["end"]]
            cat = hl.get("category", "grammar")
            suggestion = hl.get("suggestion", "")
            cat_info = CATEGORY_COLORS.get(cat, CATEGORY_COLORS["grammar"])
            bg_color = cat_info[0]
            text_color = cat_info[1]
            cat_label = cat_info[2]

            tooltip_content = f"<strong>{escape(cat_label)}:</strong> {escape(suggestion)}"
            segments.append(
                f'<span class="highlight" style="background-color:{bg_color};" data-category="{escape(cat)}">'
                f'{escape(highlighted_text)}'
                f'<span class="tooltip" style="border-color:{bg_color};">{tooltip_content}</span>'
                f'</span>'
            )
            prev_end = hl["end"]

        # Remaining text after last highlight
        if prev_end < len(essay_body):
            segments.append(escape(essay_body[prev_end:]))

        body_html = "".join(segments)
        # Preserve paragraph structure
        body_html = body_html.replace("\n\n", "</p><p>").replace("\n", " ")
        return f"<p>{body_html}</p>"

    @staticmethod
    def _build_legend(used_categories: set) -> str:
        if not used_categories:
            return ""
        items = []
        for cat in ("grammar", "clarity", "transitions", "word_choice", "structure", "evidence"):
            if cat in used_categories:
                info = CATEGORY_COLORS[cat]
                items.append(
                    f'<span class="legend-item">'
                    f'<span class="legend-swatch" style="background-color:{info[0]};"></span>'
                    f'{escape(info[2])}'
                    f'</span>'
                )
        return '<div class="legend">' + " ".join(items) + "</div>"

    @staticmethod
    def _wrap_full_document(
        student_name: str, report_html: str, legend_html: str, essay_html: str
    ) -> str:
        return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Feedback Report - {escape(student_name)}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: Georgia, 'Times New Roman', serif;
    line-height: 1.7;
    color: #333;
    max-width: 800px;
    margin: 0 auto;
    padding: 24px;
    background: #fff;
  }}
  h1 {{ font-size: 1.6em; margin: 0 0 12px; color: #2c3e50; }}
  h2 {{ font-size: 1.3em; margin: 24px 0 8px; color: #2c3e50; border-bottom: 1px solid #ddd; padding-bottom: 4px; }}
  h3 {{ font-size: 1.1em; margin: 16px 0 6px; color: #34495e; }}
  p {{ margin: 6px 0; }}
  ul {{ margin: 4px 0 8px 24px; }}
  li {{ margin: 2px 0; }}
  hr {{ border: none; border-top: 2px solid #2c3e50; margin: 32px 0; }}
  .report-section {{ margin-bottom: 24px; }}
  .essay-section {{
    margin-top: 16px;
    padding: 20px;
    background: #fafafa;
    border: 1px solid #e0e0e0;
    border-radius: 4px;
    font-size: 0.95em;
  }}
  .highlight {{
    position: relative;
    cursor: pointer;
    border-radius: 2px;
    padding: 1px 2px;
  }}
  .tooltip {{
    display: none;
    position: absolute;
    bottom: calc(100% + 6px);
    left: 50%;
    transform: translateX(-50%);
    background: #fff;
    border: 2px solid #ccc;
    border-radius: 6px;
    padding: 8px 12px;
    font-size: 0.85em;
    line-height: 1.4;
    width: 280px;
    box-shadow: 0 2px 8px rgba(0,0,0,0.15);
    z-index: 100;
    color: #333;
    text-align: left;
  }}
  .tooltip::after {{
    content: '';
    position: absolute;
    top: 100%;
    left: 50%;
    transform: translateX(-50%);
    border: 6px solid transparent;
    border-top-color: #fff;
  }}
  .highlight:hover .tooltip {{ display: block; }}
  .legend {{
    margin: 12px 0;
    padding: 8px 12px;
    background: #f8f9fa;
    border-radius: 4px;
    font-size: 0.85em;
  }}
  .legend-item {{ margin-right: 16px; white-space: nowrap; }}
  .legend-swatch {{
    display: inline-block;
    width: 14px;
    height: 14px;
    border-radius: 2px;
    vertical-align: middle;
    margin-right: 4px;
  }}
  @media (max-width: 600px) {{
    body {{ padding: 12px; }}
    .tooltip {{ width: 220px; }}
  }}
</style>
</head>
<body>

<div class="report-section">
{report_html}
</div>

<hr>

<h2>Annotated Essay</h2>
<p><em>Highlighted passages have improvement suggestions. Hover over (or tap on mobile) a highlight to see advice.</em></p>
{legend_html}
<div class="essay-section">
{essay_html}
</div>

<script>
// Mobile tap-to-toggle + viewport repositioning
(function() {{
  var highlights = document.querySelectorAll('.highlight');
  var activeTooltip = null;

  highlights.forEach(function(el) {{
    el.addEventListener('click', function(e) {{
      e.stopPropagation();
      var tip = el.querySelector('.tooltip');
      if (!tip) return;

      if (activeTooltip && activeTooltip !== tip) {{
        activeTooltip.style.display = 'none';
      }}

      if (tip.style.display === 'block') {{
        tip.style.display = 'none';
        activeTooltip = null;
      }} else {{
        tip.style.display = 'block';
        activeTooltip = tip;

        // Reposition if off-screen
        var rect = tip.getBoundingClientRect();
        if (rect.left < 4) {{
          tip.style.left = '0';
          tip.style.transform = 'none';
        }} else if (rect.right > window.innerWidth - 4) {{
          tip.style.left = 'auto';
          tip.style.right = '0';
          tip.style.transform = 'none';
        }}
        if (rect.top < 4) {{
          tip.style.bottom = 'auto';
          tip.style.top = 'calc(100% + 6px)';
        }}
      }}
    }});
  }});

  document.addEventListener('click', function() {{
    if (activeTooltip) {{
      activeTooltip.style.display = 'none';
      activeTooltip = null;
    }}
  }});
}})();
</script>

</body>
</html>"""

    # ── Batch generation ───────────────────────────────────────────────

    def generate_annotated_feedback_for_job(
        self, job_id: str, model: Optional[str] = None
    ) -> Dict[str, Any]:
        """Generate annotated HTML feedback for all graded essays in a job.

        Returns summary dict with generated count, errors, and zip path.
        """
        model = model or os.environ.get("EVALUATION_API_MODEL") or os.environ.get("XAI_API_MODEL") or "grok-beta"

        try:
            client = get_openai_client(
                api_key=os.environ.get("EVALUATION_API_KEY") or os.environ.get("XAI_API_KEY"),
                base_url=os.environ.get("EVALUATION_BASE_URL") or os.environ.get("XAI_BASE_URL"),
            )
        except Exception as e:
            return {"status": "error", "message": f"Failed to get AI client: {e}"}

        essays = self.db_manager.get_job_essays(job_id)
        if not essays:
            return {"status": "error", "message": f"No essays found for job {job_id}"}

        job_dir = self._get_job_dir(job_id)
        html_dir = job_dir / "feedback_html"
        html_dir.mkdir(parents=True, exist_ok=True)

        generated = 0
        errors = []

        for essay in essays:
            essay_id = essay.get("id")
            student_name = essay.get("student_name", "Unknown Student")
            status = essay.get("status", "")

            if status != "GRADED":
                errors.append(f"{student_name} (essay {essay_id}): not graded (status={status})")
                continue

            raw_text = essay.get("scrubbed_text") or essay.get("normalized_text") or essay.get("raw_text")
            if not raw_text:
                errors.append(f"{student_name} (essay {essay_id}): no text available")
                continue
            text = self._normalize_pdf_text(raw_text)

            print(f"[Highlighter] Processing {student_name}...", file=sys.stderr)

            try:
                result = self.get_highlights(text, client, model)
            except Exception as e:
                print(f"[Highlighter] AI call failed for {student_name}: {e}", file=sys.stderr)
                result = {"essay_body": text, "highlights": []}

            essay_body = result["essay_body"]
            highlights = self.match_highlights(essay_body, result["highlights"])

            html_content = self.build_feedback_html(essay, highlights, essay_body)

            # Write to filesystem
            safe_name = student_name.replace(" ", "_")
            html_path = html_dir / f"{safe_name}_{essay_id}.html"
            html_path.write_text(html_content, encoding="utf-8")

            # Store in database
            self.db_manager.store_report(
                job_id=job_id,
                report_type="student_html",
                filename=f"{safe_name}_{essay_id}.html",
                content=html_content.encode("utf-8"),
                essay_id=essay_id,
            )

            generated += 1
            print(f"[Highlighter] Done: {student_name} ({len(highlights)} highlights)", file=sys.stderr)

        # ZIP all HTML files for teacher download
        zip_path = ""
        if generated > 0:
            zip_base = job_dir / f"{job_id}_student_feedback"
            shutil.make_archive(str(zip_base), "zip", str(html_dir))
            zip_full_path = Path(str(zip_base) + ".zip")
            zip_path = str(zip_full_path.resolve())

            # Store ZIP in database
            with open(zip_full_path, "rb") as f:
                zip_content = f.read()
            self.db_manager.store_report(
                job_id=job_id,
                report_type="student_feedback_zip",
                filename=zip_full_path.name,
                content=zip_content,
                essay_id=None,
            )

        print(
            f"[Highlighter] Job {job_id}: {generated}/{len(essays)} reports generated, {len(errors)} errors",
            file=sys.stderr,
        )

        return {
            "status": "success" if not errors else "warning",
            "job_id": job_id,
            "generated_count": generated,
            "total_essays": len(essays),
            "errors": errors if errors else None,
            "html_directory": str(html_dir.resolve()),
            "zip_path": zip_path,
        }
