import json
from pathlib import Path
from typing import Iterator, Iterable, Union, Any

def read_jsonl(file_path: Union[str, Path]) -> Iterator[dict]:
    """
    Reads a JSONL file and yields dictionaries.
    
    Args:
        file_path: Path to the JSONL file.
        
    Yields:
        dict: Parsed JSON object.
    
    Raises:
        json.JSONDecodeError: If a line is not valid JSON.
        FileNotFoundError: If the file does not exist.
    """
    path = Path(file_path)
    with path.open('r', encoding='utf-8') as f:
        for line in f:
            if line.strip():  # Skip empty lines
                yield json.loads(line)

def write_jsonl(file_path: Union[str, Path], data: Iterable[Any], append: bool = False) -> None:
    """
    Writes an iterable of objects to a JSONL file.
    
    Args:
        file_path: Path to the output file.
        data: Iterable of objects to write.
        append: If True, appends to the file instead of overwriting.
    """
    mode = 'a' if append else 'w'
    path = Path(file_path)
    
    with path.open(mode, encoding='utf-8') as f:
        for item in data:
            f.write(json.dumps(item) + '\n')
