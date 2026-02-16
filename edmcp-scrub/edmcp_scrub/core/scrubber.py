import regex
from typing import Set, List, Optional
from pathlib import Path

from edmcp_core import DatabaseManager
from edmcp_scrub.core.name_loader import NameLoader


class Scrubber:
    """
    Regex-based name replacement engine.
    Matches whole words, case-insensitive, longest-first.
    """

    def __init__(self, names: Set[str], replacement: str = "[STUDENT_NAME]"):
        self.names = names
        self.replacement = replacement
        if names:
            sorted_names = sorted(list(names), key=len, reverse=True)
            pattern = r"\b(" + "|".join(regex.escape(n) for n in sorted_names) + r")\b"
            self.regex = regex.compile(pattern, regex.IGNORECASE)
        else:
            self.regex = None

    def scrub_text(self, text: str) -> str:
        """Replaces all matched names with the replacement token."""
        if not self.regex or not text:
            return text
        return self.regex.sub(self.replacement, text)


class ScrubberTool:
    """
    Scrubs PII from documents in a batch.
    Mirrors edmcp-essay's ScrubberTool but operates on scrubbed_documents table.
    """

    def __init__(
        self,
        names_dir: Path,
        db_manager: DatabaseManager,
    ):
        self.db_manager = db_manager
        self.names_dir = Path(names_dir)

        loader = NameLoader(self.names_dir)
        names = loader.load_all_names()
        self.scrubber = Scrubber(names)

    @staticmethod
    def _get_name_parts(full_name: str) -> Set[str]:
        """Extract individual name parts from a full name for scrubbing."""
        if not full_name:
            return set()
        parts = set()
        for part in full_name.replace("-", " ").replace("_", " ").split():
            normalized = part.strip().lower()
            if len(normalized) >= 2:
                parts.add(normalized)
        return parts

    def scrub_batch(
        self,
        batch_id: str,
        custom_words: Optional[List[str]] = None,
    ) -> int:
        """
        Scrubs all documents in a batch. Reads from DB, scrubs, writes back to DB.

        Args:
            batch_id: The batch to scrub
            custom_words: Optional additional words to scrub

        Returns:
            Number of documents scrubbed
        """
        custom_scrubber = None
        if custom_words:
            normalized_custom = {
                w.strip().lower() for w in custom_words
                if w and w.strip() and len(w.strip()) >= 2
            }
            if normalized_custom:
                custom_scrubber = Scrubber(normalized_custom)

        docs = self.db_manager.get_batch_documents(batch_id)
        scrubbed_count = 0

        for doc in docs:
            raw_text = doc.get("raw_text", "")
            detected_name = doc.get("student_name", "")

            # Layer 1: Roster names
            scrubbed_text = self.scrubber.scrub_text(raw_text)

            # Layer 2: Detected name parts (handles nicknames/preferred names)
            detected_name_parts = self._get_name_parts(detected_name)
            if detected_name_parts:
                detected_scrubber = Scrubber(detected_name_parts)
                scrubbed_text = detected_scrubber.scrub_text(scrubbed_text)

            # Layer 3: Custom words
            if custom_scrubber:
                scrubbed_text = custom_scrubber.scrub_text(scrubbed_text)

            self.db_manager.update_document_scrubbed(doc["id"], scrubbed_text)
            scrubbed_count += 1

        if scrubbed_count > 0:
            self.db_manager.update_scrub_batch_status(batch_id, "SCRUBBED")

        return scrubbed_count
