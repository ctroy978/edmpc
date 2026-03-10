"""
Algorithmic edit-pattern analysis using Python difflib.

No AI calls. Detects patterns consistent with AI generation or copy-paste.
All returned snippets are < 200 chars and stripped of obvious PII patterns.
"""

import difflib
import re
from datetime import datetime, timezone
from typing import TypedDict


# Configurable thresholds
BULK_INSERT_THRESHOLD = 0.20       # fraction of final doc length added in one revision
FEW_REVISIONS_MIN_WORDS = 300      # min words to trigger few-revisions check
FEW_REVISIONS_MAX = 3              # max revisions before flagging
BURST_FRACTION = 0.60              # fraction of content added in burst window
BURST_WINDOW_MINUTES = 10          # burst window size
COLD_START_THRESHOLD = 0.80        # fraction of final content in first revision
WORK_HOURS_START = 6               # 6 AM
WORK_HOURS_END = 23                # 11 PM
DEADLINE_WINDOW_HOURS = 2          # all edits within N hours of deadline


_PII_PATTERN = re.compile(
    r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+|"  # email
    r"\b[A-Z][a-z]+ [A-Z][a-z]+\b",                        # Title Case Name
    re.UNICODE,
)


def _strip_pii(text: str) -> str:
    return _PII_PATTERN.sub("[REDACTED]", text)


def _truncate(text: str, max_len: int = 180) -> str:
    text = text.strip()
    if len(text) > max_len:
        return text[:max_len] + "…"
    return text


class Flag(TypedDict):
    flag_type: str
    severity: str          # "low" | "medium" | "high"
    description: str
    snippet: str           # anonymized, < 200 chars


class SubmissionFlags(TypedDict):
    flags: list[Flag]
    revision_count: int
    final_word_count: int


def _word_count(text: str) -> int:
    return len(text.split())


def _added_chars(old: str, new: str) -> tuple[int, str]:
    """Return (chars_added, first_insertion_snippet)."""
    matcher = difflib.SequenceMatcher(None, old, new, autojunk=False)
    added = 0
    first_snippet = ""
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag in ("insert", "replace"):
            chunk = new[j1:j2]
            added += len(chunk)
            if not first_snippet:
                first_snippet = chunk
    return added, first_snippet


def _parse_time(iso: str) -> datetime | None:
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except Exception:
        return None


def analyze_submission(
    revision_texts: list[tuple[str, str]],   # [(modified_time_iso, text), ...]
    deadline_iso: str | None = None,
) -> SubmissionFlags:
    """
    Analyze a sequence of revision texts and return detected flags.

    Args:
        revision_texts: Ordered list of (modified_time_iso, plain_text) tuples.
        deadline_iso:   ISO 8601 deadline string, used for timing checks.

    Returns:
        SubmissionFlags dict with flags list and summary stats.
    """
    flags: list[Flag] = []

    if not revision_texts:
        return SubmissionFlags(flags=flags, revision_count=0, final_word_count=0)

    final_text = revision_texts[-1][1]
    final_len = len(final_text)
    final_words = _word_count(final_text)
    n_revisions = len(revision_texts)

    # ── 1. Bulk insertion ──────────────────────────────────────────────────
    for i, (mod_time, text) in enumerate(revision_texts):
        prev_text = revision_texts[i - 1][1] if i > 0 else ""
        added, snippet = _added_chars(prev_text, text)
        if final_len > 0 and added / final_len > BULK_INSERT_THRESHOLD:
            flags.append(Flag(
                flag_type="bulk_insertion",
                severity="high",
                description=(
                    f"Revision {i + 1} added {added} characters "
                    f"({added / final_len:.0%} of final document) in one step."
                ),
                snippet=_truncate(_strip_pii(snippet)),
            ))

    # ── 2. Very few revisions ──────────────────────────────────────────────
    if final_words >= FEW_REVISIONS_MIN_WORDS and n_revisions <= FEW_REVISIONS_MAX:
        flags.append(Flag(
            flag_type="few_revisions",
            severity="medium",
            description=(
                f"Document has {final_words} words but only "
                f"{n_revisions} revision(s)."
            ),
            snippet="",
        ))

    # ── 3. Burst editing ──────────────────────────────────────────────────
    times = [_parse_time(t) for t, _ in revision_texts]
    if all(t is not None for t in times) and n_revisions >= 2:
        # Sliding window: find window of BURST_WINDOW_MINUTES containing most added chars
        burst_window_s = BURST_WINDOW_MINUTES * 60
        for i in range(n_revisions):
            window_added = 0
            window_snippet = ""
            t_start = times[i]
            for j in range(i, n_revisions):
                dt = (times[j] - t_start).total_seconds()
                if dt > burst_window_s:
                    break
                prev = revision_texts[j - 1][1] if j > 0 else ""
                added, snippet = _added_chars(prev, revision_texts[j][1])
                window_added += added
                if not window_snippet:
                    window_snippet = snippet
            if final_len > 0 and window_added / final_len > BURST_FRACTION:
                flags.append(Flag(
                    flag_type="burst_editing",
                    severity="high",
                    description=(
                        f"{window_added / final_len:.0%} of final content added "
                        f"within a {BURST_WINDOW_MINUTES}-minute window."
                    ),
                    snippet=_truncate(_strip_pii(window_snippet)),
                ))
                break  # one burst flag per document is enough

    # ── 4. Cold start ─────────────────────────────────────────────────────
    first_text = revision_texts[0][1]
    first_len = len(first_text)
    if final_len > 0 and first_len / final_len > COLD_START_THRESHOLD and n_revisions > 1:
        _, snippet = _added_chars("", first_text)
        flags.append(Flag(
            flag_type="cold_start",
            severity="medium",
            description=(
                f"First revision already contained {first_len / final_len:.0%} "
                "of the final document length with minimal subsequent editing."
            ),
            snippet=_truncate(_strip_pii(snippet)),
        ))

    # ── 5. Timing outliers ────────────────────────────────────────────────
    if all(t is not None for t in times) and times:
        # All edits outside business hours
        all_off_hours = all(
            not (WORK_HOURS_START <= t.hour < WORK_HOURS_END) for t in times  # type: ignore[union-attr]
        )
        if all_off_hours and n_revisions > 1:
            flags.append(Flag(
                flag_type="timing_off_hours",
                severity="low",
                description=(
                    f"All {n_revisions} edits occurred outside "
                    f"{WORK_HOURS_START}:00–{WORK_HOURS_END}:00."
                ),
                snippet="",
            ))

        # All edits within DEADLINE_WINDOW_HOURS of deadline
        if deadline_iso:
            deadline = _parse_time(deadline_iso)
            if deadline:
                window_s = DEADLINE_WINDOW_HOURS * 3600
                all_near_deadline = all(
                    0 <= (deadline - t).total_seconds() <= window_s  # type: ignore[operator]
                    for t in times
                )
                if all_near_deadline and n_revisions >= 1:
                    flags.append(Flag(
                        flag_type="deadline_crunch",
                        severity="medium",
                        description=(
                            f"All edits occurred within "
                            f"{DEADLINE_WINDOW_HOURS} hours of the deadline."
                        ),
                        snippet="",
                    ))

    return SubmissionFlags(
        flags=flags,
        revision_count=n_revisions,
        final_word_count=final_words,
    )
