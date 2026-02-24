# edmcp-email Integration Guide

## Overview

`edmcp-email` is a FastMCP server that sends student reports via email. It is a **delivery layer only** — it does not generate reports. Reports must already exist in the central `edmcp.db` database (stored by `edmcp-regrade`, `edmcp-essay`, or any other server that calls `store_report`).

Any application that can call MCP tools can use this server: web UIs, automation scripts, other MCP servers, or Claude directly.

---

## Configuration

The server reads all credentials from environment variables. Set these before starting.

| Variable | Required | Default | Description |
|---|---|---|---|
| `SMTP_HOST` | yes | `smtp-relay.brevo.com` | SMTP relay hostname |
| `SMTP_PORT` | yes | `587` | SMTP port (587 = STARTTLS) |
| `SMTP_USER` | yes | — | SMTP authentication username |
| `SMTP_PASS` | yes | — | SMTP authentication password |
| `FROM_EMAIL` | yes | — | Sender email address |
| `FROM_NAME` | no | `Grade Reports` | Sender display name shown to students |
| `SMTP_USE_TLS` | no | `true` | Set to `false` to disable STARTTLS |
| `EDMCP_DB_PATH` | no | `../data/edmcp.db` | Path to the shared SQLite database |

`EDMCP_DB_PATH` must point to the same database file used by the server that generated the reports. All servers in the `edmcp` ecosystem share one database.

### Starting the server

```bash
cd edmcp-email
uv sync
uv run python server.py
```

For development with the MCP Inspector:

```bash
uv run fastmcp dev server.py
```

---

## The Roster File

Every tool that looks up student emails requires a `roster_path` — the path to a **directory** containing `school_names.csv`. The file must have these columns:

```
id,first_name,last_name,grade,email
1,Jane,Smith,10,jsmith@school.edu
2,John,Doe,11,jdoe@school.edu
```

- **`email` is required** for any student you intend to email. Students without an email are logged as FAILED with reason `"No email address found in roster"`.
- The roster is loaded fresh on each tool call — no server restart is needed when the file changes.
- Name matching is **fuzzy by default** (threshold: 0.80 similarity). This handles OCR artifacts in student names extracted from scanned documents. The exact name stored in the database is matched against the roster; the first match above the threshold is used.

---

## Idempotency

All send operations are **idempotent by design**. The server records every send attempt in the `email_logs` table. Before sending to any student, it checks whether that student already has a `SENT` entry for this `job_id` + `report_type` combination. If so, the student is skipped silently.

**This means you can call `send_reports` multiple times safely.** It will never double-send to a student who was already successfully reached.

Students are only skipped based on `SENT` status. `FAILED`, `SKIPPED`, and `DRY_RUN` entries do not block a future send attempt. Use `resend_failed_emails` to retry failures explicitly.

---

## Report Types

Reports are stored in the database with a `report_type` string. The most common values:

| `report_type` | Description | File extension |
|---|---|---|
| `student_html` | Per-student HTML feedback report | `.html` |
| `student_pdf` | Per-student PDF feedback report | `.pdf` |

Use `list_available_reports(job_id)` to discover what types exist for a given job before committing to a send campaign.

---

## Standard Send Workflow

This is the expected sequence for a typical email campaign:

### 1. Confirm reports exist

```
list_available_reports(job_id)
```

Check `by_type` to confirm the expected `report_type` is present and `total` is non-zero. If zero, the generating server has not stored reports yet — do not proceed.

### 2. Preview before sending

```
preview_email_campaign(job_id, report_type, roster_path)
```

Returns four lists:
- `ready` — students who have a report, a matching email, and have not been sent yet
- `already_sent` — students with an existing `SENT` log entry (will be skipped)
- `missing_email` — students with a report but no email in the roster
- `missing_report` — students in the roster but with no stored report

Review `missing_email` and `missing_report` before sending. Add emails to the roster CSV or investigate the report generation step as needed.

### 3. Dry run (optional but recommended)

```
send_reports(job_id, report_type, roster_path, dry_run=True)
```

Logs `DRY_RUN` entries for every student who would receive an email. Check the result's `details` array and verify the counts match expectations. No emails are sent.

### 4. Send

```
send_reports(job_id, report_type, roster_path)
```

Sends to all eligible students. Returns per-student `details` with status and reason. Check `failed` count — a non-zero value means some students did not receive their report.

### 5. Check results

```
get_email_log(job_id, report_type)
```

Returns the full `email_logs` history, split into `sent`, `failed`, `skipped`, and `dry_run` lists. The `failed` list includes a `reason` for each failure. Use this to decide whether to retry or investigate.

### 6. Retry failures (if any)

```
resend_failed_emails(job_id, report_type, roster_path)
```

Retries only students with `status=FAILED` in the log. Students with `status=SENT` are never retried.

---

## Tool Reference

### `send_reports`

The primary send tool.

**Parameters:**

| Parameter | Type | Required | Description |
|---|---|---|---|
| `job_id` | string | yes | Job ID whose reports to send |
| `report_type` | string | yes | Report type (e.g., `student_html`) |
| `roster_path` | string | yes | Path to directory with `school_names.csv` |
| `subject` | string | no | Email subject line. Auto-generated if omitted. |
| `body_template` | string | no | Template name. Default: `default_feedback` |
| `dry_run` | bool | no | If true, no emails sent. Default: `false` |
| `filter_students` | list[str] | no | If provided, only send to these students |
| `skip_students` | list[str] | no | Students to skip even if otherwise eligible |

**Returns:**

```json
{
  "sent": 12,
  "failed": 1,
  "skipped": 2,
  "dry_run": 0,
  "details": [
    {"student": "Jane Smith", "status": "SENT", "email": "jsmith@school.edu", "filename": "Jane_Smith_feedback.html"},
    {"student": "John Doe",   "status": "FAILED", "reason": "No email address found in roster"},
    {"student": "Sam Lee",    "status": "SKIPPED", "reason": "Already sent"}
  ]
}
```

**Directives:**
- Always check `failed` count after the call. A non-zero value requires follow-up.
- Use `filter_students` when testing with a single student before a full campaign.
- `filter_students` and `skip_students` matching is **case-insensitive**.
- The auto-generated subject format is: `"Your Feedback Report: {assignment_name}"` when an assignment name is known, otherwise `"Your Feedback Report"`.

---

### `preview_email_campaign`

Read-only preview. No emails sent, nothing logged.

**Parameters:** `job_id`, `report_type`, `roster_path`

**Returns:**

```json
{
  "job_id": "regrade_20250210_abc123",
  "report_type": "student_html",
  "summary": {
    "ready": 18,
    "already_sent": 2,
    "missing_email": 1,
    "missing_report": 0
  },
  "ready": [
    {"student": "Jane Smith", "email": "jsmith@school.edu", "filename": "Jane_Smith_feedback.html"}
  ],
  "already_sent": ["Bob Jones"],
  "missing_email": ["Unknown Student"],
  "missing_report": []
}
```

**Directives:**
- Call this before every campaign. The `missing_email` list tells you exactly which roster entries need updating before you can reach everyone.
- `already_sent` students are not re-sent by `send_reports`. This is informational only.

---

### `get_email_log`

**Parameters:** `job_id`, `report_type` (optional filter)

**Returns:**

```json
{
  "job_id": "regrade_20250210_abc123",
  "report_type": "student_html",
  "total": 20,
  "sent": [...],
  "failed": [...],
  "skipped": [...],
  "dry_run": [...]
}
```

Each entry has: `id`, `job_id`, `report_type`, `student_name`, `email_address`, `status`, `reason`, `subject`, `template_used`, `sent_at`.

**Directives:**
- Omit `report_type` to see all email activity across all report types for a job.
- The `reason` field in `failed` entries is the primary diagnostic. Common values: `"No email address found in roster"`, `"No report found for type 'student_html'"`, `"SMTP send failed"`.
- `sent_at` is an ISO 8601 timestamp in local server time.

---

### `send_report_from_file`

One-off send from a file on the server's filesystem. Bypasses the database and roster lookup entirely.

**Parameters:**

| Parameter | Type | Required | Description |
|---|---|---|---|
| `file_path` | string | yes | Absolute path to the file to attach |
| `student_name` | string | yes | Student's name (used in template) |
| `to_email` | string | yes | Recipient email address |
| `subject` | string | yes | Email subject line |
| `body_template` | string | no | Template name. Default: `default_feedback` |
| `assignment_name` | string | no | For template context |
| `grade` | string | no | For template context |
| `job_id` | string | no | If provided, logs the send to `email_logs` |
| `report_type` | string | no | Label for logging. Default: `manual` |

**Returns:**

```json
{"success": true, "student": "Jane Smith", "email": "jsmith@school.edu", "file": "/path/to/file.html"}
```

**Directives:**
- Use this for resending a manually corrected file, sending test emails, or delivering reports that were generated outside the database pipeline.
- If you provide `job_id`, the send is logged. If not, no log entry is created.
- This tool does **not** check for existing `SENT` entries — it always sends.

---

### `resend_failed_emails`

**Parameters:** `job_id`, `report_type`, `roster_path`, `subject` (optional), `body_template` (optional), `dry_run` (optional)

**Returns:** Same shape as `send_reports`, plus `retried_count`.

**Directives:**
- Only students with at least one `FAILED` entry are attempted. Students who were never attempted are not included.
- Students who have since been marked `SENT` (e.g., by a manual `send_report_from_file`) are skipped via the normal idempotency check.

---

### `test_smtp_connection`

**Parameters:** none

**Returns:**

```json
{
  "success": true,
  "smtp_host": "smtp-relay.brevo.com",
  "smtp_port": 587,
  "from_email": "grades@school.edu",
  "from_name": "Grade Reports",
  "use_tls": true
}
```

**Directives:**
- Call this during setup and after any environment variable change before running a live campaign.
- Credentials (`SMTP_USER`, `SMTP_PASS`) are **never** included in the response.

---

### `list_available_reports`

**Parameters:** `job_id`

**Returns:**

```json
{
  "job_id": "regrade_20250210_abc123",
  "total": 40,
  "by_type": {
    "student_html": 20,
    "student_pdf": 20
  },
  "reports": [
    {
      "report_id": 101,
      "report_type": "student_html",
      "essay_id": 5,
      "filename": "Jane_Smith_feedback.html",
      "student_name": "Jane Smith",
      "created_at": "2025-02-10T14:22:00"
    }
  ]
}
```

**Directives:**
- File content is not returned — only metadata. This is safe to call on jobs with large binary reports.
- Use `by_type` to discover which `report_type` strings are valid for a given job before calling `send_reports`.

---

## Email Templates

Templates are Jinja2 files stored in `edmcp_email/data/email_templates/`. Each template requires both `.html.j2` and `.txt.j2` variants.

### Built-in templates

| Name | Use case |
|---|---|
| `default_feedback` | Per-student feedback report (HTML or PDF attachment) |
| `gradebook_notice` | Class-level summary sent to a teacher or administrator |

### Template variables

All templates receive these variables:

| Variable | Source | Description |
|---|---|---|
| `student_name` | Database essay record | Student's full name |
| `grade` | Database essay record | The student's grade (may be empty) |
| `assignment_name` | Job metadata | Assignment or class name |
| `report_type` | Tool parameter | The `report_type` string (e.g., `student_html`) |
| `from_name` | `FROM_NAME` env var | Sender display name |

### Adding a custom template

1. Create `edmcp_email/data/email_templates/your_template_name.html.j2` and `your_template_name.txt.j2`.
2. Pass `body_template="your_template_name"` to any send tool.
3. No server restart is required — templates are loaded at render time.

The `report_type` variable is available in templates for conditional attachment instructions:

```jinja2
{% if report_type == 'student_html' %}Open the attached HTML file in any web browser.
{% elif report_type == 'student_pdf' %}Open the attached PDF to review your feedback.
{% else %}Open the attached file to review your report.{% endif %}
```

---

## Error Handling

This server does not raise exceptions at the tool level. All errors are returned in the response body. Check the following fields after every call:

| Tool | How to detect failure |
|---|---|
| `send_reports` | Check `results["failed"] > 0` and inspect `details` |
| `preview_email_campaign` | Check `missing_email` and `missing_report` lists |
| `get_email_log` | Check `failed` list; each entry has a `reason` field |
| `send_report_from_file` | Check `result["success"] == false` and read `error` |
| `test_smtp_connection` | Check `result["success"] == false` |

SMTP failures (`"SMTP send failed"`) indicate a network or credential issue. Roster failures (`"No email address found in roster"`) indicate a data gap in the CSV. Report-not-found failures indicate the generating server has not yet stored a report for that essay.

---

## Integration Checklist

Before running a live campaign:

- [ ] `test_smtp_connection` returns `success: true`
- [ ] `list_available_reports` shows the expected `report_type` with the expected student count
- [ ] `roster_path` directory contains `school_names.csv` with `email` column populated
- [ ] `preview_email_campaign` shows zero students in `missing_email` (or you have accepted that those students will not receive an email)
- [ ] `send_reports(..., dry_run=True)` count matches expectation
- [ ] `send_reports(..., filter_students=["One Student"])` confirms a single real email arrives and looks correct
- [ ] Full send completed; `get_email_log` shows `failed` list is empty or handled
