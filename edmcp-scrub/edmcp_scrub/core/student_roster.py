import csv
from pathlib import Path
from typing import Dict, Optional, Tuple
from dataclasses import dataclass
from difflib import SequenceMatcher
from edmcp_scrub.core.name_loader import NameLoader


@dataclass
class StudentInfo:
    """Data class representing a student record from school_names.csv"""
    id: int
    first_name: str
    last_name: str
    full_name: str
    grade: str
    email: str


class StudentRoster:
    """
    Student roster management with name lookup and fuzzy matching.
    Used for validating detected names and suggesting corrections.
    """

    def __init__(self, names_dir: Path):
        self.names_dir = Path(names_dir)
        self.name_loader = NameLoader(names_dir)
        self._student_map: Dict[str, StudentInfo] = {}
        self._load_roster()

    def _normalize(self, name: str) -> str:
        """Normalize name for case-insensitive matching"""
        return " ".join(name.lower().split())

    def _load_roster(self):
        """Loads school_names.csv with full student information."""
        self._student_map.clear()
        school_file = self.names_dir / "school_names.csv"

        if not school_file.exists():
            return

        with open(school_file, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                student_id = row.get("id", "")
                first = row.get("first_name", "").strip()
                last = row.get("last_name", "").strip()
                grade = row.get("grade", "").strip()
                email = row.get("email", "").strip()

                if first and last:
                    full_name = f"{first} {last}"
                    normalized_name = self._normalize(full_name)

                    student_info = StudentInfo(
                        id=int(student_id) if student_id.isdigit() else 0,
                        first_name=first,
                        last_name=last,
                        full_name=full_name,
                        grade=grade,
                        email=email,
                    )
                    self._student_map[normalized_name] = student_info

    def _fuzzy_match(
        self, student_name: str, threshold: float = 0.80
    ) -> Tuple[Optional[StudentInfo], float]:
        """
        Fuzzy matching to find best match for a student name.
        Uses SequenceMatcher to handle OCR errors and typos.
        """
        normalized_input = self._normalize(student_name)
        best_match = None
        best_score = 0.0

        for normalized_roster_name, student_info in self._student_map.items():
            similarity = SequenceMatcher(
                None, normalized_input, normalized_roster_name
            ).ratio()

            if similarity > best_score:
                best_score = similarity
                best_match = student_info

        if best_score >= threshold:
            return best_match, best_score

        return None, best_score

    def get_student_info(self, student_name: str) -> Optional[StudentInfo]:
        """Returns full student record by name lookup."""
        normalized = self._normalize(student_name)
        return self._student_map.get(normalized)

    def get_all_students(self) -> Dict[str, StudentInfo]:
        """Returns the complete student roster."""
        return self._student_map.copy()

    def get_full_name_set(self) -> set:
        """Returns set of normalized full names for name detection."""
        return set(self._student_map.keys())
