"""
Build anonymized audit reports from per-document SubmissionFlags.

Maps document indices to "Submission A", "Submission B", etc.
Optionally calls an xAI-compatible API for narrative summaries.
"""

import os
import re
from typing import Any

from .diff_analyzer import SubmissionFlags


_LABEL_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

# Additional PII scrub pass — catch anything diff_analyzer might have missed
_PII_RE = re.compile(
    r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+|"
    r"\b[A-Z][a-z]+ [A-Z][a-z]+\b",
    re.UNICODE,
)


def _label(index: int) -> str:
    if index < 26:
        return f"Submission {_LABEL_LETTERS[index]}"
    return f"Submission {index + 1}"


def _scrub(text: str) -> str:
    return _PII_RE.sub("[REDACTED]", text)


def _severity_score(severity: str) -> int:
    return {"high": 3, "medium": 2, "low": 1}.get(severity, 0)


def build_report(
    flags_per_doc: list[SubmissionFlags],
    file_ids: list[str] | None = None,
) -> dict[str, Any]:
    """
    Build a structured, anonymized audit report.

    Args:
        flags_per_doc: List of SubmissionFlags, one per document (in order).
        file_ids:      Corresponding Drive file IDs (same order). When provided,
                       each submission entry includes a drive_url the teacher can
                       click to open the document and inspect its version history.
                       File IDs are not sent to any LLM.

    Returns:
        Structured report dict with summary and per-submission details.
    """
    submissions = []
    total_flags = 0
    high_count = 0
    medium_count = 0
    low_count = 0

    for i, sf in enumerate(flags_per_doc):
        label = _label(i)
        clean_flags = []
        for flag in sf["flags"]:
            clean_flag = {
                "flag_type": flag["flag_type"],
                "severity": flag["severity"],
                "description": _scrub(flag["description"]),
                "snippet": _scrub(flag["snippet"]),
            }
            clean_flags.append(clean_flag)
            s = flag["severity"]
            if s == "high":
                high_count += 1
            elif s == "medium":
                medium_count += 1
            else:
                low_count += 1

        total_flags += len(clean_flags)
        max_sev = max(
            (f["severity"] for f in clean_flags),
            key=_severity_score,
            default="none",
        )

        entry: dict[str, Any] = {
            "label": label,
            "revision_count": sf["revision_count"],
            "final_word_count": sf["final_word_count"],
            "flag_count": len(clean_flags),
            "max_severity": max_sev,
            "flags": clean_flags,
        }

        if file_ids and i < len(file_ids):
            fid = file_ids[i]
            entry["drive_url"] = f"https://docs.google.com/document/d/{fid}/edit"

        # Optional AI narrative summary
        if os.environ.get("EDITCHECK_USE_AI_SUMMARY", "false").lower() == "true":
            entry["ai_summary"] = _generate_ai_summary(label, entry)

        submissions.append(entry)

    # Sort by descending severity score then flag count for easy triage
    submissions.sort(
        key=lambda s: (_severity_score(s["max_severity"]), s["flag_count"]),
        reverse=True,
    )

    return {
        "status": "success",
        "summary": {
            "total_submissions": len(submissions),
            "total_flags": total_flags,
            "high": high_count,
            "medium": medium_count,
            "low": low_count,
            "flagged_submissions": sum(1 for s in submissions if s["flag_count"] > 0),
        },
        "submissions": submissions,
    }


def _generate_ai_summary(label: str, entry: dict[str, Any]) -> str:
    """
    Call xAI-compatible API for a one-paragraph narrative per submission.
    Returns empty string on any failure (AI summary is strictly opt-in).
    """
    try:
        from openai import OpenAI

        client = OpenAI(
            api_key=os.environ.get("EVALUATION_API_KEY", ""),
            base_url=os.environ.get("EVALUATION_BASE_URL", "https://api.x.ai/v1"),
        )
        model = os.environ.get("EVALUATION_API_MODEL", "grok-3-mini")

        flag_lines = "\n".join(
            f"- [{f['severity'].upper()}] {f['flag_type']}: {f['description']}"
            for f in entry["flags"]
        )
        prompt = (
            f"You are helping a teacher review academic integrity concerns. "
            f"The submission is anonymized as '{label}'. "
            f"The following algorithmic flags were detected:\n{flag_lines}\n\n"
            f"Write a single, concise paragraph (≤100 words) summarizing the "
            f"pattern of concern. Do not assume guilt. Do not include any student "
            f"names or personal information. Use neutral, professional language."
        )

        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=200,
        )
        return resp.choices[0].message.content or ""
    except Exception:
        return ""
