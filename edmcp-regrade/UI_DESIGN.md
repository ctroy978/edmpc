# edmcp-regrade UI Design Specification

## Overview

This document describes how a web UI should interact with the `edmcp-regrade` FastMCP server (18 tools). The UI is a private, teacher-controlled essay grading assistant. Teachers create grading jobs, upload essays, run AI grading, then review and adjust grades across multiple sessions before generating final student reports.

The core design principle: **jobs are long-lived workspaces, not one-time processes.** Teachers may grade one class today, return tomorrow for another, or pause mid-essay. Every change persists immediately. The UI must feel like reopening a folder on your desk — everything is exactly where you left it.

---

## Architecture

The UI communicates with `edmcp-regrade` via MCP tool calls. All tools return JSON strings with a consistent shape:

```json
{"status": "success", ...}
{"status": "error", "message": "..."}
{"status": "warning", "message": "..."}
```

Always check `status` before rendering results. Display `message` on errors/warnings.

---

## Job Status Lifecycle

```
PENDING → INDEXING → PENDING → GRADING → READY_FOR_REVIEW → IN_PROGRESS → FINALIZED
                                                                ↑              │
                                                                └──────────────┘
                                                              (teacher can un-finalize
                                                               by setting status back)
```

| Status | Meaning | UI Treatment |
|---|---|---|
| `PENDING` | Job created, essays may be added, not yet graded | Show "Upload" and "Grade" actions |
| `INDEXING` | Source materials being indexed (transient) | Show spinner, disable actions |
| `GRADING` | AI grading in progress (transient) | Show progress, disable editing |
| `READY_FOR_REVIEW` | AI grading complete, awaiting teacher review | Primary action: "Start Review" |
| `IN_PROGRESS` | Teacher is actively reviewing essays | Show review progress bar |
| `FINALIZED` | Teacher has finalized all grades | Show "Generate Reports" action |

Archived jobs (`archived: true`) are hidden by default. Show them only when the user toggles "Show Archived."

---

## Screen 1: Dashboard (Job List)

This is the home screen. Teachers land here on every session.

### Data Source
- **Primary:** `list_jobs(limit, offset, status, class_name, search, include_archived)`
- **Search:** `search_jobs(query, start_date, end_date)` for full-text search across jobs, students, and essay content

### Layout
A sortable table or card grid showing all jobs. Each row/card shows:
- Job name
- Class name and assignment title
- Status badge (color-coded per status)
- Essay count / graded count (e.g., "15/20 graded")
- Last modified date (`updated_at`)

### Actions
- **"New Job" button** → opens job creation form
- **Click a job** → navigates to Job Detail view
- **Filter controls:** status dropdown, class name dropdown (populated from distinct values in the job list), free-text search
- **Pagination:** use `limit` and `offset` params
- **Context menu per job:** Archive, Delete (with confirmation dialog)
  - Archive: `archive_job(job_id)`
  - Delete: `delete_job(job_id)` — warn that this is permanent and deletes all essays

### Polling
If any job has status `GRADING` or `INDEXING`, poll `list_jobs` every 5-10 seconds to update the status badge. Stop polling once no transient statuses remain.

---

## Screen 2: Job Creation

A form/modal for creating a new grading job.

### Required Fields
- **Job name** (text input) — e.g., "Period 3 - Romeo & Juliet Essay"
- **Rubric** (large textarea or file upload) — the full grading rubric as text

### Optional Fields
- **Class name** (text input) — for dashboard filtering
- **Assignment title** (text input)
- **Due date** (date picker)
- **Essay question/prompt** (textarea) — the question students were asked to answer

### Submit Flow
1. Call `create_regrade_job(name, rubric, class_name, assignment_title, due_date, question_text)`
2. On success, navigate to the Job Detail screen for the new `job_id`
3. The job starts in `PENDING` status — the UI should guide the teacher to upload essays next

---

## Screen 3: Job Detail

The central workspace for a single job. This is where the teacher spends most of their time.

### Data Source
- `get_job(job_id)` — job metadata, status, counts
- `get_job_essays(job_id, status, include_text=False)` — essay sidebar list (lightweight, no text)
- `get_job_statistics(job_id)` — grade summary panel

### Layout: Two-Panel Design

**Left panel — Essay list sidebar:**
- Compact list of essays showing: student identifier, AI grade, teacher grade (if set), status indicator
- Color/icon per essay status: PENDING (gray), GRADED (blue), REVIEWED (green), APPROVED (checkmark)
- Click an essay to load it in the right panel
- Show count: "12 of 20 reviewed"

**Right panel — Content area:**
- Changes based on job status and what's selected
- When no essay is selected: show job summary (statistics, metadata, available actions)
- When an essay is selected: show the essay review view (Screen 4)

### Job Summary View (right panel, no essay selected)

**Metadata section** (editable via `update_job`):
- Job name, class name, assignment title, due date, rubric preview
- "Edit" button opens inline editing; save calls `update_job(job_id, ...changed_fields)`

**Statistics panel** (from `get_job_statistics`):
- Average grade, min/max
- Grade distribution (bar chart or histogram)
- Per-criteria average scores (table)

**Action buttons** (conditional on status):

| Job Status | Available Actions |
|---|---|
| `PENDING` | "Upload Essays", "Add Source Material", "Grade All" |
| `READY_FOR_REVIEW` | "Start Review" (sets status to `IN_PROGRESS` via `update_job`) |
| `IN_PROGRESS` | "Finalize Job" |
| `FINALIZED` | "Generate All Reports", "Reopen for Editing" |

### Upload Essays Action
Two options:
1. **Directory upload:** `add_essays_from_directory(job_id, directory_path, file_extension)` — the UI should let the teacher browse/select a folder and file extension
2. **Single essay:** `add_essay(job_id, student_identifier, essay_text)` — paste or type, with a text field for the student identifier

After upload, refresh the essay list.

### Add Source Material Action
- File picker for PDF, TXT, DOCX, MD files
- Calls `add_source_material(job_id, file_paths)`
- Show the ingestion result (number of documents indexed)
- This is optional — jobs work fine without source material, just without RAG context

### Grade All Action
- Calls `grade_job(job_id, model?, system_instructions?)`
- This is a long-running operation. Show a progress indicator.
- Poll `get_job(job_id)` to watch status change from `GRADING` → `READY_FOR_REVIEW`
- Once complete, refresh the essay list to show grades

---

## Screen 4: Essay Review View

This is the core Phase 2 interaction — where the teacher reads an essay, sees AI feedback, and adds their own assessment.

### Data Source
- `get_essay_detail(job_id, essay_id)` — returns full essay text, AI evaluation, teacher fields

### Layout: Three Sections

**Section A — Essay text** (scrollable, main reading area):
- Display the full `essay_text` with paragraph formatting
- Support **text selection** — when the teacher selects text, show a popup/tooltip with a comment input
- Selected text + comment becomes an annotation entry: `{"selected_text": "...", "comment": "..."}`
- Highlight previously annotated passages (from `teacher_annotations`) with a colored background; hover/click reveals the comment

**Section B — AI Evaluation panel** (sidebar or collapsible):
- Show `evaluation.overall_score` prominently
- Show `evaluation.summary`
- For each criterion in `evaluation.criteria`:
  - Criterion name and score
  - Justification text
  - Quoted examples (italic)
  - Advice for improvement
  - Rewritten example (in a subtle box)
- This panel is **read-only** — it's AI output for the teacher's reference

**Section C — Teacher input panel** (bottom or sidebar):
- **Grade override** (text input): pre-populated with `teacher_grade` if set, otherwise shows AI `grade` as placeholder
- **Overall comments** (textarea): pre-populated with `teacher_comments` if any
- **Status buttons:** "Mark Reviewed", "Approve"
- **Save button** (or auto-save)

### Saving Teacher Input

Call `update_essay_review(job_id, essay_id, teacher_grade, teacher_comments, teacher_annotations, status)`:
- `teacher_annotations` is a JSON string: `[{"selected_text": "...", "comment": "..."}, ...]`
- Only send fields that changed (the tool supports partial updates)

**Auto-save strategy:** Save on a debounced timer (e.g., 3 seconds after last keystroke) or when the teacher navigates to another essay. This is critical — teachers expect to close the browser and come back without losing work.

### Navigation
- "Previous / Next Essay" buttons navigate the essay list
- Before navigating, auto-save any unsaved changes
- The essay list sidebar should show which essays have been reviewed (updated status)

### Annotation Data Format

The `teacher_annotations` field stores a JSON array. Each annotation is:

```json
{
  "selected_text": "The exact text the teacher highlighted",
  "comment": "The teacher's note about this passage"
}
```

The UI is responsible for:
1. Capturing text selections from the essay display
2. Presenting a comment input for each selection
3. Managing the array (add, edit, delete annotations)
4. Serializing to JSON string for `update_essay_review`
5. Deserializing from JSON when loading an essay to re-render highlights

---

## Screen 5: Finalization

When all essays are reviewed, the teacher finalizes the job.

### Flow
1. Teacher clicks "Finalize Job" from the Job Detail summary view
2. Show a confirmation dialog:
   - Checkbox: "Refine my comments with AI" (default: checked)
   - Explanation: "AI will polish your comments to be more professional and encouraging, while preserving your intent"
3. Call `finalize_job(job_id, refine_comments=True/False)`
4. This may take time (AI refinement is per-essay). Show progress.
5. On completion, job status becomes `FINALIZED`

### Selective Refinement (Optional)
If the teacher wants to refine comments for specific essays only (not the whole job), use `refine_essay_comments(job_id, essay_ids)`. This could be exposed as a "Refine Comments" button on individual essay review views.

---

## Screen 6: Report Generation

Available once a job is `FINALIZED`.

### Per-Student Reports
- Call `generate_student_report(job_id, essay_id)` for each essay
- Returns `{"status": "success", "html": "...full HTML document..."}`
- Render the HTML in an iframe or new tab for preview
- Offer "Download HTML" button (create a blob download from the HTML string)

### Batch Reports
- Loop through all essay IDs calling `generate_student_report` for each
- Bundle as a ZIP download (client-side) or show a list of downloadable reports
- Consider generating reports in the background and showing progress

### Report Contents (for context — generated server-side)
Each HTML report is a self-contained document with:
- Student identifier, assignment info, final grade
- Rubric breakdown table (criterion name, score, justification, examples, advice, rewritten example)
- Teacher comments section (overall comments + inline annotations)
- Full essay text with highlighted annotation passages (hover reveals comment)

---

## Tool Reference (Quick Lookup)

### Job Lifecycle
| Action | Tool | Key Params |
|---|---|---|
| Create job | `create_regrade_job` | `name`, `rubric`, `class_name?`, `assignment_title?`, `due_date?`, `question_text?` |
| Get job details | `get_job` | `job_id` |
| Update job metadata | `update_job` | `job_id`, any field to change |
| List all jobs | `list_jobs` | `limit?`, `offset?`, `status?`, `class_name?`, `search?`, `include_archived?` |
| Search jobs | `search_jobs` | `query`, `start_date?`, `end_date?` |
| Archive job | `archive_job` | `job_id` |
| Delete job | `delete_job` | `job_id` |

### Essay Ingestion
| Action | Tool | Key Params |
|---|---|---|
| Bulk upload from directory | `add_essays_from_directory` | `job_id`, `directory_path`, `file_extension?` |
| Add single essay | `add_essay` | `job_id`, `student_identifier`, `essay_text` |
| Add source material (RAG) | `add_source_material` | `job_id`, `file_paths[]` |

### AI Grading
| Action | Tool | Key Params |
|---|---|---|
| Grade all pending essays | `grade_job` | `job_id`, `model?`, `system_instructions?` |

### Essay Reading
| Action | Tool | Key Params |
|---|---|---|
| List essays (lightweight) | `get_job_essays` | `job_id`, `status?`, `include_text=False` |
| Get full essay + evaluation | `get_essay_detail` | `job_id`, `essay_id` |
| Get grade statistics | `get_job_statistics` | `job_id` |

### Teacher Review
| Action | Tool | Key Params |
|---|---|---|
| Save review (grade, comments, annotations) | `update_essay_review` | `job_id`, `essay_id`, `teacher_grade?`, `teacher_comments?`, `teacher_annotations?`, `status?` |
| AI-refine teacher comments | `refine_essay_comments` | `job_id`, `essay_ids?` |
| Finalize job | `finalize_job` | `job_id`, `refine_comments?` |

### Reports
| Action | Tool | Key Params |
|---|---|---|
| Generate HTML report | `generate_student_report` | `job_id`, `essay_id` |

---

## Key UX Principles

1. **Persistence is invisible.** The teacher should never think about saving. Auto-save on every change via `update_essay_review`. When they return tomorrow, everything is exactly as they left it.

2. **The dashboard is home base.** Every session starts here. Make it fast to scan — status badges, progress indicators, and last-modified dates tell the story at a glance.

3. **AI is the assistant, not the authority.** Show AI grades and feedback prominently but always as suggestions. The teacher's grade override and comments are the final word. The `teacher_grade` field takes precedence over `grade` in all displays and reports.

4. **Design for interruption.** Teachers get pulled away constantly. Navigation between essays should auto-save. The browser's back button should work. Refreshing the page should reload the current state from the server.

5. **Grading is iterative.** A teacher might review 5 essays, change their mind about the rubric interpretation, go back and adjust earlier grades, then finalize. The UI should make this flow natural — easy navigation, visible review status, no artificial "you must proceed in order" constraints.

6. **Keep payloads light.** Use `get_job_essays(include_text=False)` for the sidebar list. Only load full essay text and evaluation when the teacher actually clicks on an essay via `get_essay_detail`. This keeps the UI responsive even with 100+ essays in a job.
