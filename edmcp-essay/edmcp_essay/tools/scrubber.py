import regex
from typing import Set, List, Union, Optional
from pathlib import Path
from edmcp_core import read_jsonl, write_jsonl, DatabaseManager
from edmcp_essay.core.name_loader import NameLoader


class Scrubber:
    """
    Scrubs PII (names) from the first 20 lines of text.
    """

    def __init__(self, names: Set[str], replacement: str = "[STUDENT_NAME]"):
        self.names = names
        self.replacement = replacement
        # Compile a regex that matches any of the names as whole words, case-insensitive
        if names:
            # Sort names by length descending to match longer names first if there are overlaps
            # (though we are doing atomic scrubbing, it's good practice)
            sorted_names = sorted(list(names), key=len, reverse=True)
            pattern = r"\b(" + "|".join(regex.escape(n) for n in sorted_names) + r")\b"
            self.regex = regex.compile(pattern, regex.IGNORECASE)
        else:
            self.regex = None

    def scrub_text(self, text: str, line_limit: int = 20, header_lines: int = 3) -> str:
        """
        Scrubs names from the first line_limit non-empty text lines of the first page,
        and from the first header_lines non-empty text lines of subsequent pages (for MLA/APA headers).

        Args:
            text: Text to scrub (may contain form feed \\f page separators)
            line_limit: Number of non-empty text lines to scrub from first page (default: 20)
            header_lines: Number of non-empty text lines to scrub from subsequent pages (default: 3)
        """
        if not self.regex or not text:
            return text

        # Split by form feed to get individual pages
        pages = text.split("\f")
        scrubbed_pages = []

        for page_num, page in enumerate(pages):
            lines = page.splitlines()
            scrubbed_lines = []

            # Determine how many non-empty lines to scrub on this page
            scrub_count = line_limit if page_num == 0 else header_lines
            non_empty_count = 0

            for line in lines:
                # Check if line has actual content (not just whitespace)
                if line.strip():
                    non_empty_count += 1
                    # Scrub if we're within the non-empty line limit
                    if non_empty_count <= scrub_count:
                        scrubbed_lines.append(self.regex.sub(self.replacement, line))
                    else:
                        scrubbed_lines.append(line)
                else:
                    # Preserve blank lines as-is
                    scrubbed_lines.append(line)

            scrubbed_pages.append("\n".join(scrubbed_lines))

        # Rejoin pages with form feed
        result = "\f".join(scrubbed_pages)

        # Handle trailing newline if original text had one
        if text.endswith("\n") and not result.endswith("\n"):
            result += "\n"
        return result


class ScrubberTool:
    """
    Modular tool to scrub PII from OCR results in a job directory.
    """
    def __init__(self, job_dir: Union[str, Path], names_dir: Optional[Union[str, Path]] = None, db_manager: Optional[DatabaseManager] = None):
        self.job_dir = Path(job_dir)
        self.job_id = self.job_dir.name
        self.db_manager = db_manager

        # Resolve names directory
        if names_dir is None:
            # Default to edmcp_essay/data/names relative to this file
            self.names_dir = Path(__file__).parent.parent / "data" / "names"
        else:
            self.names_dir = Path(names_dir)

        # Load names and initialize scrubber
        loader = NameLoader(self.names_dir)
        names = loader.load_all_names()
        self.scrubber = Scrubber(names)

    def scrub_job(self, input_filename: str = "ocr_results.jsonl", output_filename: str = "scrubbed_results.jsonl") -> Path:
        """
        Reads OCR results (from DB or JSONL), scrubs them, and writes to a new JSONL file + DB.

        Args:
            input_filename: Name of the input JSONL file in the job directory (fallback source).
            output_filename: Name of the output JSONL file to create.

        Returns:
            Path to the scrubbed results file.
        """
        output_path = self.job_dir / output_filename
        scrubbed_records = []

        if self.db_manager:
            # 1. Read from DB
            essays = self.db_manager.get_job_essays(self.job_id)

            for essay in essays:
                raw_text = essay.get("raw_text", "")
                scrubbed_text = self.scrubber.scrub_text(raw_text)

                # Update DB
                self.db_manager.update_essay_scrubbed(essay["id"], scrubbed_text)

                # Add to list for JSONL output
                scrubbed_records.append({
                    "job_id": self.job_id,
                    "essay_id": essay["id"],
                    "student_name": essay.get("student_name"),
                    "text": scrubbed_text,
                    "metadata": essay.get("metadata")
                })
        else:
            # 2. Fallback to JSONL
            input_path = self.job_dir / input_filename
            if not input_path.exists():
                raise FileNotFoundError(f"Input file not found: {input_path}")

            records = list(read_jsonl(input_path))
            for record in records:
                if "text" in record:
                    record["text"] = self.scrubber.scrub_text(record["text"])
                scrubbed_records.append(record)

        # Write to JSONL
        write_jsonl(output_path, scrubbed_records)
        return output_path
