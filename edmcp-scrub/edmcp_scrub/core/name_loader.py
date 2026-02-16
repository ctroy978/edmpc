import csv
from pathlib import Path
from typing import Set


class NameLoader:
    """
    Loads and parses name lists from CSV files for scrubbing.
    """

    def __init__(self, names_dir: Path, min_length: int = 2):
        self.names_dir = Path(names_dir)
        self.min_length = min_length
        self.scrub_set: Set[str] = set()

    def load_all_names(self) -> Set[str]:
        """
        Loads all name parts from known CSV files in the names directory.
        Returns a set of lowercase individual name parts for scrubbing.
        """
        self.scrub_set.clear()

        school_file = self.names_dir / "school_names.csv"
        if school_file.exists():
            self.scrub_set.update(self._load_school_names(school_file))

        common_file = self.names_dir / "common_names.csv"
        if common_file.exists():
            self.scrub_set.update(self._load_common_names(common_file))

        return self.scrub_set

    def load_full_student_names(self) -> Set[str]:
        """
        Loads full student names (first + last) from school_names.csv.
        Returns a set of normalized full names like "john doe".
        """
        full_names = set()
        school_file = self.names_dir / "school_names.csv"

        if not school_file.exists():
            return full_names

        with open(school_file, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                first = row.get("first_name", "").strip()
                last = row.get("last_name", "").strip()

                if first and last:
                    full_name = f"{first} {last}"
                    normalized = self._normalize(full_name)
                    if len(normalized) >= self.min_length:
                        full_names.add(normalized)

        return full_names

    def _normalize(self, name: str) -> str:
        return " ".join(name.lower().split())

    def _is_valid(self, name: str) -> bool:
        return len(name) >= self.min_length

    def _load_school_names(self, file_path: Path) -> Set[str]:
        names = set()
        with open(file_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if "first_name" in row:
                    val = self._normalize(row["first_name"])
                    if self._is_valid(val):
                        names.add(val)
                if "last_name" in row:
                    val = self._normalize(row["last_name"])
                    if self._is_valid(val):
                        names.add(val)
        return names

    def _load_common_names(self, file_path: Path) -> Set[str]:
        names = set()
        with open(file_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if "name" in row:
                    val = self._normalize(row["name"])
                    if self._is_valid(val):
                        names.add(val)
        return names
