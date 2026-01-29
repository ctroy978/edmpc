# edmcp-bubble Developer Guide

A guide for building UIs (Gradio, web, etc.) that interact with the bubble sheet MCP server.

## Overview

edmcp-bubble is an MCP server for creating, managing, and grading bubble sheet tests. It provides a complete workflow from test creation through automated grading of scanned student responses.

**Core Entities:**
- **Bubble Test** - A test record with name, description, and status
- **Bubble Sheet** - A printable PDF with the layout metadata for scanning
- **Answer Key** - The correct answers and point values for grading
- **Grading Job** - A batch grading session for processing scanned responses

---

## Test Lifecycle

A bubble test progresses through these statuses:

```
CREATED → SHEET_GENERATED → KEY_ADDED → [Ready for Grading]
```

| Status | Description | Next Actions |
|--------|-------------|--------------|
| `CREATED` | Test record exists, no sheet yet | Generate bubble sheet |
| `SHEET_GENERATED` | PDF and layout created | Set answer key |
| `KEY_ADDED` | Ready for grading | Create grading jobs |

### Workflow Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                        TEST SETUP                                │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  create_bubble_test(name, description)                          │
│         │                                                        │
│         ▼                                                        │
│  ┌──────────────┐                                               │
│  │   CREATED    │                                               │
│  └──────┬───────┘                                               │
│         │                                                        │
│  generate_bubble_sheet(test_id, num_questions, ...)             │
│         │                                                        │
│         ▼                                                        │
│  ┌──────────────────┐                                           │
│  │ SHEET_GENERATED  │ ◄── download_bubble_sheet_pdf()           │
│  └──────┬───────────┘     download_bubble_sheet_layout()        │
│         │                                                        │
│  set_answer_key(test_id, answers_json)                          │
│         │                                                        │
│         ▼                                                        │
│  ┌──────────────┐                                               │
│  │  KEY_ADDED   │ ◄── Test is now ready for grading             │
│  └──────────────┘                                               │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## Grading Job Lifecycle

Once a test has status `KEY_ADDED`, you can create grading jobs:

```
CREATED → UPLOADED → SCANNING → SCANNED → GRADING → COMPLETED
                                                  ↘ ERROR
```

| Status | Description | Next Action |
|--------|-------------|-------------|
| `CREATED` | Job created, awaiting PDF upload | `upload_scans()` |
| `UPLOADED` | PDF stored, ready for processing | `process_scans()` |
| `SCANNING` | CV processing in progress | Wait |
| `SCANNED` | Responses extracted | `grade_job()` |
| `GRADING` | Applying answer key | Wait |
| `COMPLETED` | Gradebook ready | `download_gradebook()` |
| `ERROR` | Processing failed | Check `error_message` |

### Workflow Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                      GRADING WORKFLOW                            │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  create_grading_job(test_id)                                    │
│         │                                                        │
│         ▼                                                        │
│  ┌──────────────┐                                               │
│  │   CREATED    │                                               │
│  └──────┬───────┘                                               │
│         │                                                        │
│  upload_scans(job_id, pdf_base64)                               │
│         │                                                        │
│         ▼                                                        │
│  ┌──────────────┐                                               │
│  │   UPLOADED   │                                               │
│  └──────┬───────┘                                               │
│         │                                                        │
│  process_scans(job_id)  ◄── Computer vision extracts answers    │
│         │                                                        │
│         ▼                                                        │
│  ┌──────────────┐                                               │
│  │   SCANNED    │ ◄── Student responses now in database        │
│  └──────┬───────┘                                               │
│         │                                                        │
│  grade_job(job_id)  ◄── Apply answer key, calculate scores      │
│         │                                                        │
│         ▼                                                        │
│  ┌──────────────┐                                               │
│  │  COMPLETED   │ ◄── download_gradebook() for CSV              │
│  └──────────────┘                                               │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## MCP Tools Reference

### Test Management

#### `create_bubble_test`
Create a new test record.

```python
# Input
create_bubble_test(name="Week 5 Quiz", description="Chapters 10-12")

# Output
{
    "status": "success",
    "test_id": "bt_20260126_143052_abc12345",
    "message": "Created bubble test 'Week 5 Quiz' with ID: bt_20260126_143052_abc12345"
}
```

#### `generate_bubble_sheet`
Generate a printable PDF bubble sheet.

```python
# Input
generate_bubble_sheet(
    test_id="bt_...",
    num_questions=25,        # 1-50
    paper_size="A4",         # "A4" or "LETTER"
    id_length=6,             # 4-10 digits for student ID
    id_orientation="vertical",  # "vertical" or "horizontal"
    draw_border=False        # Draw alignment border
)

# Output
{
    "status": "success",
    "test_id": "bt_...",
    "sheet_id": 1,
    "num_questions": 25,
    "paper_size": "A4",
    "message": "Generated bubble sheet with 25 questions"
}
```

#### `list_bubble_tests`
List all tests with status info.

```python
# Input
list_bubble_tests(limit=20)

# Output
{
    "status": "success",
    "count": 3,
    "tests": [
        {
            "id": "bt_...",
            "name": "Week 5 Quiz",
            "created_at": "2026-01-26T14:30:52",
            "status": "KEY_ADDED",
            "has_sheet": true,
            "has_answer_key": true
        },
        ...
    ]
}
```

#### `get_bubble_test`
Get detailed test info.

```python
# Input
get_bubble_test(test_id="bt_...")

# Output
{
    "status": "success",
    "test": {
        "id": "bt_...",
        "name": "Week 5 Quiz",
        "description": "Chapters 10-12",
        "created_at": "2026-01-26T14:30:52",
        "status": "KEY_ADDED"
    },
    "sheet": {
        "num_questions": 25,
        "paper_size": "A4",
        "id_length": 6,
        "id_orientation": "vertical",
        "created_at": "2026-01-26T14:31:00"
    },
    "answer_key": {
        "total_questions": 25,
        "total_points": 25.0,
        "created_at": "2026-01-26T14:32:00"
    }
}
```

#### `download_bubble_sheet_pdf`
Download the printable PDF.

```python
# Input
download_bubble_sheet_pdf(test_id="bt_...")

# Output
{
    "status": "success",
    "test_id": "bt_...",
    "content_type": "application/pdf",
    "encoding": "base64",
    "data": "<base64 PDF content>"
}
```

**UI Tip:** Decode base64 and offer as file download or render in PDF viewer.

#### `download_bubble_sheet_layout`
Get the layout JSON (for debugging/visualization).

```python
# Output contains layout with bubble coordinates
{
    "status": "success",
    "test_id": "bt_...",
    "layout": {
        "dimensions": {"width": 595.0, "height": 842.0},
        "questions": [...],
        "student_id": [...],
        "alignment_markers": [...]
    }
}
```

#### `set_answer_key`
Set the correct answers for grading.

```python
# Input - answers is a JSON string
set_answer_key(
    test_id="bt_...",
    answers='[
        {"question": "Q1", "answer": "a", "points": 1.0},
        {"question": "Q2", "answer": "b", "points": 1.0},
        {"question": "Q3", "answer": "a,c", "points": 2.0}
    ]'
)

# Output
{
    "status": "success",
    "test_id": "bt_...",
    "key_id": 1,
    "total_questions": 3,
    "total_points": 4.0,
    "message": "Answer key saved with 3 questions"
}
```

**Answer Key Format:**
- `question`: Question identifier (e.g., "Q1", "Q01", "1")
- `answer`: Correct answer(s), comma-separated for multiple-select (e.g., "a" or "a,c")
- `points`: Point value (default 1.0)

#### `get_answer_key`
Retrieve the current answer key.

```python
# Output
{
    "status": "success",
    "test_id": "bt_...",
    "created_at": "2026-01-26T14:32:00",
    "total_points": 25.0,
    "answers": [
        {"question": "Q1", "answer": "a", "points": 1.0},
        ...
    ]
}
```

#### `delete_bubble_test`
Delete a test and all associated data.

```python
# Input
delete_bubble_test(test_id="bt_...")

# Output
{
    "status": "success",
    "message": "Deleted test bt_... and all associated data"
}
```

---

### Grading Tools

#### `create_grading_job`
Start a new grading session.

```python
# Input
create_grading_job(test_id="bt_...")

# Output
{
    "status": "success",
    "job_id": "gj_20260126_150000_xyz98765",
    "message": "Created grading job gj_... for test bt_..."
}
```

**Prerequisite:** Test must have status `KEY_ADDED`.

#### `upload_scans`
Upload scanned bubble sheets.

```python
# Input - pdf_base64 is base64-encoded PDF
upload_scans(
    job_id="gj_...",
    pdf_base64="<base64 PDF content>"
)

# Output
{
    "status": "success",
    "job_id": "gj_...",
    "num_pages": 52,
    "message": "Uploaded PDF with 52 pages"
}
```

**UI Tip:** Accept PDF file upload, encode to base64 before calling.

#### `process_scans`
Run computer vision to extract responses.

```python
# Input
process_scans(job_id="gj_...")

# Output
{
    "status": "success",
    "job_id": "gj_...",
    "num_students": 50,
    "num_errors": 2,
    "message": "Processed 50 students, 2 errors"
}
```

**Note:** This is CPU-intensive. Consider showing a progress indicator.

#### `grade_job`
Apply answer key and calculate scores.

```python
# Input
grade_job(job_id="gj_...")

# Output
{
    "status": "success",
    "job_id": "gj_...",
    "mean_score": 21.5,
    "min_score": 12.0,
    "max_score": 25.0,
    "mean_percent": 86.0,
    "message": "Grading complete. Mean: 86.0%"
}
```

#### `get_grading_job`
Get job status and details.

```python
# Input
get_grading_job(job_id="gj_...")

# Output
{
    "status": "success",
    "job": {
        "id": "gj_...",
        "test_id": "bt_...",
        "created_at": "2026-01-26T15:00:00",
        "status": "COMPLETED",
        "num_pages": 52,
        "num_students": 50,
        "error_message": null,
        "response_counts": {
            "OK": 50,
            "ERROR": 2
        }
    }
}
```

#### `list_grading_jobs`
List all grading jobs for a test.

```python
# Input
list_grading_jobs(test_id="bt_...", limit=20)

# Output
{
    "status": "success",
    "test_id": "bt_...",
    "count": 2,
    "jobs": [
        {
            "id": "gj_...",
            "test_id": "bt_...",
            "created_at": "2026-01-26T15:00:00",
            "status": "COMPLETED",
            "num_pages": 52,
            "num_students": 50,
            "error_message": null
        },
        ...
    ]
}
```

#### `download_gradebook`
Download the gradebook CSV.

```python
# Input
download_gradebook(job_id="gj_...")

# Output
{
    "status": "success",
    "job_id": "gj_...",
    "content_type": "text/csv",
    "encoding": "base64",
    "data": "<base64 CSV content>"
}
```

**Gradebook CSV Format:**
```csv
Student_ID,Q1,Q2,Q3,...,Total_Score,Total_Possible,Percent_Grade
123456,A,B,A,...,23.0,25.0,92.0
234567,A,C,B,...,21.0,25.0,84.0
```

---

## Gradio UI Recommendations

### Suggested Page Structure

```
┌─────────────────────────────────────────────────────────────────┐
│  edmcp-bubble - Bubble Sheet Test Grading                       │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐              │
│  │ Tests List  │  │ Create Test │  │ Grade Tests │              │
│  └─────────────┘  └─────────────┘  └─────────────┘              │
│                                                                  │
│  [Tab Content Area]                                              │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### Tab 1: Tests List

- Table showing all tests with columns: Name, Status, Created, Actions
- Status badges: CREATED (yellow), SHEET_GENERATED (blue), KEY_ADDED (green)
- Action buttons based on status:
  - CREATED → "Generate Sheet"
  - SHEET_GENERATED → "Set Answer Key"
  - KEY_ADDED → "Grade" + "Download PDF"

### Tab 2: Create/Edit Test

**Section 1: Test Details**
- Text input: Test name
- Text area: Description

**Section 2: Sheet Configuration** (shown after test created)
- Number input: Questions (1-50)
- Dropdown: Paper size (A4, Letter)
- Number input: Student ID digits (4-10)
- Radio: ID orientation (Vertical, Horizontal)
- Checkbox: Draw border
- Button: "Generate Sheet"
- PDF preview/download when generated

**Section 3: Answer Key** (shown after sheet generated)
- Dynamic form with rows for each question:
  - Question label (auto: Q1, Q2, ...)
  - Checkboxes for answers (A, B, C, D, E)
  - Points input (default 1.0)
- Or: File upload for CSV/JSON answer key
- Button: "Save Answer Key"

### Tab 3: Grading

**Section 1: Select Test**
- Dropdown of tests with status KEY_ADDED

**Section 2: Upload Scans**
- File upload for PDF (multiple pages)
- Button: "Upload & Process"
- Progress indicator during processing

**Section 3: Results**
- Statistics display: Mean, Min, Max, % Pass
- Table of student results
- Download button for gradebook CSV
- List of scan errors/warnings

### State Management Tips

1. **Refresh test list** after any mutation (create, generate, set key, delete)
2. **Poll job status** during processing (every 2-3 seconds)
3. **Disable buttons** based on status (can't grade without KEY_ADDED)
4. **Show warnings** for scan errors (some students may have unreadable sheets)

### Base64 Encoding/Decoding

```python
import base64

# Encoding file for upload
def encode_file(file_path):
    with open(file_path, 'rb') as f:
        return base64.b64encode(f.read()).decode('utf-8')

# Decoding response for download
def decode_to_file(base64_data, output_path):
    with open(output_path, 'wb') as f:
        f.write(base64.b64decode(base64_data))
```

---

## Scoring Algorithm

The grading uses Canvas-style scoring:

### Single-Select Questions
- Exact match required
- Full points or zero

### Multiple-Select Questions
- Partial credit using formula:
  ```
  score = max(0, (correct_selected - incorrect_selected) × points_per_option)
  where points_per_option = total_points / num_correct_options
  ```

**Example:** Q3 worth 2 points, correct answers are A and C
- Student selects A, C → 2.0 points (perfect)
- Student selects A → 1.0 points (1 correct, 0 wrong)
- Student selects A, B → 0.5 points (1 correct, 1 wrong)
- Student selects A, B, D → 0.0 points (1 correct, 2 wrong, floored at 0)

---

## Error Handling

All tools return JSON with a `status` field:
- `"success"` - Operation completed
- `"error"` - Operation failed, check `message` field

```python
result = json.loads(tool_response)
if result["status"] == "error":
    show_error(result["message"])
else:
    # Handle success
```

Common error scenarios:
- Test not found
- Wrong status for operation (e.g., grading a test without answer key)
- Invalid PDF format
- Scan processing failures (individual pages may fail)

---

## System Requirements

The grading server requires:
- **poppler-utils** - For PDF to image conversion
  - Linux: `sudo apt install poppler-utils`
  - macOS: `brew install poppler`

---

## Quick Reference Card

| Want to... | Use this tool | Prerequisites |
|------------|---------------|---------------|
| Create a test | `create_bubble_test` | None |
| Generate printable sheet | `generate_bubble_sheet` | Test exists |
| Download PDF | `download_bubble_sheet_pdf` | Sheet generated |
| Set answer key | `set_answer_key` | Sheet generated |
| Start grading | `create_grading_job` | Answer key set |
| Upload scans | `upload_scans` | Job created |
| Process scans | `process_scans` | Scans uploaded |
| Calculate grades | `grade_job` | Scans processed |
| Download grades | `download_gradebook` | Job completed |
