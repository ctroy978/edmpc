# edmcp-scrub Usage Guide

## Overview

edmcp-scrub is the document intake and PII scrubbing server. Teachers submit documents here first, validate/correct student names, scrub personally identifiable information, then hand the batch ID to downstream servers (grading, analysis, etc.) to consume clean documents.

## Running the Server

```bash
cd edmcp-scrub
uv run python server.py
```

The server uses stdio transport by default (standard FastMCP). It creates `edmcp.db` in the server directory on first run.

## Teacher Workflow (MCP Tools)

The tools are designed to be called in order. Here's the full pipeline:

### Step 1: Ingest Documents

**From a directory of PDFs:**
```
batch_process_documents(
    directory_path="/path/to/student/pdfs",
    batch_name="Period 3 Essays"
)
```
Returns: `{ batch_id: "batch_20260216_...", students_detected: 23, ... }`

Tries native text extraction first (fast, free). Falls back to Qwen OCR for scanned/handwritten PDFs (requires `QWEN_API_KEY` in `.env`).

**From pre-extracted text (e.g., another server already did OCR):**
```
create_batch(batch_name="Period 3 Essays")
# returns batch_id

add_text_documents(
    batch_id="batch_20260216_...",
    texts=[
        {"text": "Name: Noah Beaudry\nThe industrial revolution...", "metadata": {"source": "page1.pdf"}},
        {"text": "Name: Olivia Brophy\nIn this essay I will...", "metadata": {"source": "page2.pdf"}},
    ]
)
```

Name detection runs automatically on each text — looks for `Name:` headers first, then matches against the roster CSV.

### Step 2: Validate Names

```
validate_student_names(batch_id="batch_20260216_...")
```

Returns:
```json
{
    "status": "needs_corrections",
    "matched_students": [
        {"doc_id": 1, "detected_name": "Noah Beaudry", "roster_name": "Noah Beaudry", "email": "noah.beaudry@csd8.info"}
    ],
    "mismatched_students": [
        {"doc_id": 5, "detected_name": "Kaitlyn J", "document_preview": "Kaitlyn J\nThe civil war was...", "reason": "Name not found in school roster"}
    ],
    "total_matched": 20,
    "total_mismatched": 3,
    "total_missing_from_roster": 0
}
```

### Step 3: Fix Mismatched Names

For each mismatched document, the teacher can preview the raw text:

```
get_document_preview(batch_id="batch_20260216_...", doc_id=5, max_lines=20)
```

Then correct the name:
```
correct_detected_name(
    batch_id="batch_20260216_...",
    doc_id=5,
    corrected_name="Kaytlin Johnson"
)
```

If the name isn't an exact roster match, the tool returns fuzzy suggestions:
```json
{
    "status": "not_in_roster",
    "message": "'Kaitlyn' not found in roster. Did you mean one of these?",
    "possible_matches": [
        {"name": "Kaytlin Johnson", "email": "kaytlin.johnson@csd8.info"}
    ]
}
```

### Step 4: Scrub

```
scrub_batch(batch_id="batch_20260216_...")
```

Three-layer scrub:
1. All name parts from `school_names.csv` (first names, last names)
2. Detected student name parts per document (catches nicknames written on the document)
3. Custom scrub words (if any were added)

All names get replaced with `[STUDENT_NAME]`.

### Step 5 (Optional): Add Custom Words and Re-scrub

If the teacher spots names that weren't caught (teacher names, nicknames not in CSV):

```
add_custom_scrub_words(
    batch_id="batch_20260216_...",
    words=["Mr. Cooper", "Kaitlyn", "Mrs. Smith"]
)

re_scrub_batch(batch_id="batch_20260216_...")
```

Re-scrub runs the full three-layer pipeline again from `raw_text`, so it's safe to call multiple times.

### Step 6: Inspect Results

```
get_batch_statistics(batch_id="batch_20260216_...")
# Returns manifest: doc_id, student_name, page_count, word_count, status for each doc

get_batch_documents(batch_id="batch_20260216_...")
# Returns all docs with scrubbed_text

get_scrubbed_document(doc_id=1)
# Returns a single document
```

## Reading Scrubbed Documents from Another Server

Any server that depends on `edmcp-core` can read scrubbed documents directly from the database. No MCP call needed.

**Setup:** Add `SCRUB_DB_PATH` to your `.env`:
```
SCRUB_DB_PATH=../edmcp-scrub/edmcp.db
```

**Read documents by batch ID:**
```python
from edmcp_core import DatabaseManager, get_env

scrub_db = DatabaseManager(get_env("SCRUB_DB_PATH"))

# Get all documents in a batch
docs = scrub_db.get_batch_documents("batch_20260216_...")
for doc in docs:
    print(doc["student_name"])   # who wrote it
    print(doc["scrubbed_text"])  # clean text, names removed
    print(doc["metadata"])       # source_file, page_count, etc.

# Get a single document
doc = scrub_db.get_scrubbed_document(doc_id=1)
```

**Available DatabaseManager methods for scrub data:**

| Method | Returns |
|--------|---------|
| `get_scrub_batch(batch_id)` | Batch info (id, name, status, created_at) |
| `list_scrub_batches()` | All batches |
| `get_batch_documents(batch_id)` | All docs in a batch |
| `get_scrubbed_document(doc_id)` | Single doc by ID |
| `get_batch_custom_scrub_words(batch_id)` | Custom words list |

## Tool Reference

| Tool | Purpose |
|------|---------|
| `create_batch(batch_name)` | Create an empty batch |
| `batch_process_documents(directory_path, batch_name, batch_id, dpi)` | Ingest PDFs from a directory |
| `add_text_documents(batch_id, texts)` | Add pre-extracted text to a batch |
| `validate_student_names(batch_id)` | Check names against roster |
| `get_document_preview(batch_id, doc_id, max_lines)` | Preview raw text for identification |
| `correct_detected_name(batch_id, doc_id, corrected_name)` | Fix a student name |
| `scrub_batch(batch_id)` | Scrub PII from all docs in batch |
| `add_custom_scrub_words(batch_id, words)` | Add extra words to scrub |
| `get_custom_scrub_words(batch_id)` | List custom scrub words |
| `re_scrub_batch(batch_id)` | Re-run scrubbing (after corrections/custom words) |
| `get_batch_statistics(batch_id)` | Document manifest (names, page counts, word counts) |
| `list_batches()` | List all batches |
| `get_batch_documents(batch_id)` | Get all docs with scrubbed text |
| `get_scrubbed_document(doc_id)` | Get single doc |

## Name CSV Format

The server reads from `edmcp_scrub/data/names/school_names.csv` (symlinked to shared `data/names/`):

```csv
first_name,last_name,email
Noah,Beaudry,noah.beaudry@csd8.info
Olivia,Brophy,olivia.brophy@csd8.info
```

Optional `common_names.csv` (single `name` column) for additional names to scrub.

## Environment Variables

Only needed if processing scanned/handwritten PDFs (OCR fallback):

```
QWEN_API_KEY=your_key
QWEN_API_MODEL=qwen-vl-max
QWEN_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
```
