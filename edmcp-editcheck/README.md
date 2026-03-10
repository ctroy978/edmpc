# edmcp-editcheck — Gradio UI Build Guide

**Audience:** An AI coding agent building a Gradio frontend for this FastMCP server.

This document describes everything the UI needs to know: what the server does, how to call each tool, what the response shapes look like, and how to wire the UI state machine correctly.

---

## What This Server Does

`edmcp-editcheck` is a FastMCP server that audits Google Classroom assignment submissions for signs of AI generation or copy-paste cheating. It:

1. Authenticates the teacher via Google OAuth (browser-based consent flow)
2. Reads the teacher's Google Classroom courses and assignments (no student data)
3. Fetches each student's Drive document revision history via the Drive API
4. Analyzes edit patterns locally with Python difflib (no student text sent externally)
5. Returns an anonymized report: "Submission A", "Submission B", etc. with flags and clickable Drive links

**FERPA note:** No student names, emails, or IDs ever appear in tool outputs. The Gradio UI must not add them either.

---

## Running the Server

```bash
cd edmcp-editcheck
uv sync
uv run fastmcp dev server.py        # development (MCP Inspector)
uv run python server.py             # production
```

The server exposes **5 MCP tools** over stdio transport.

---

## Environment Variables

The server reads these from the project-level `.env` (one directory above `edmcp-editcheck/`):

| Variable | Required | Description |
|----------|----------|-------------|
| `GOOGLE_CLIENT_SECRETS` | Yes | Path to `client_secrets.json` downloaded from Google Cloud Console |
| `EDITCHECK_TOKEN_PATH` | No | Where to persist the OAuth token. Default: `~/.edmcp/editcheck_token.json` |
| `EDITCHECK_USE_AI_SUMMARY` | No | Set `true` to add an AI narrative paragraph per flagged submission (default: `false`) |
| `EVALUATION_API_KEY` | Only if AI summary enabled | xAI/Grok API key |
| `EVALUATION_BASE_URL` | Only if AI summary enabled | API base URL (default: `https://api.x.ai/v1`) |
| `EVALUATION_API_MODEL` | Only if AI summary enabled | Model name (default: `grok-3-mini`) |

The teacher must obtain `client_secrets.json` from the Google Cloud Console (OAuth 2.0 Desktop App credential) and set `GOOGLE_CLIENT_SECRETS` to its path.

---

## Tool Reference

### `start_auth()`

Launches the Google OAuth browser consent flow in a background thread. The browser opens automatically on the machine running the server (i.e., the teacher's desktop).

**Arguments:** none

**Returns:**
```json
{
  "status": "success",
  "message": "OAuth flow started. A browser window should open...",
  "auth_initiated": true
}
```

If already authenticated:
```json
{
  "status": "success",
  "message": "Already authenticated. No action needed.",
  "auth_initiated": false
}
```

**UI behavior:** Call this when the teacher clicks a "Connect Google Account" button. After calling, start polling `check_auth_status()` every 2–3 seconds until `authenticated: true`. Do not call `start_auth()` again while a flow is in progress.

---

### `check_auth_status()`

Polls whether the teacher has completed the browser consent.

**Arguments:** none

**Returns (not yet authenticated):**
```json
{
  "status": "success",
  "authenticated": false,
  "message": "OAuth flow is in progress. Complete the consent in the browser, then poll again."
}
```

**Returns (authenticated):**
```json
{
  "status": "success",
  "authenticated": true,
  "message": "Authenticated and credentials are valid."
}
```

**Returns (flow failed):**
```json
{
  "status": "error",
  "authenticated": false,
  "message": "OAuth flow failed: <error detail>"
}
```

**UI behavior:** Poll this on a timer after `start_auth()`. When `authenticated: true`, stop polling, update the auth status indicator, and enable the rest of the UI. On `status: error`, show the message and offer a retry button.

---

### `revoke_auth()`

Deletes the stored OAuth token. The teacher will need to re-authenticate next time.

**Arguments:** none

**Returns:**
```json
{
  "status": "success",
  "message": "Credentials revoked. Call start_auth() to re-authenticate."
}
```

**UI behavior:** Wire to a "Sign Out" or "Disconnect" button. After revoking, reset the UI to the unauthenticated state.

---

### `list_courses_and_assignments()`

Returns all active courses the teacher owns, with their assignments. No student data.

**Arguments:** none

**Returns:**
```json
{
  "status": "success",
  "courses": [
    {
      "course_id": "123456789",
      "name": "AP English Literature",
      "assignments": [
        {
          "coursework_id": "987654321",
          "title": "Essay 1: The Great Gatsby",
          "due_date": "2025-03-15"
        }
      ]
    }
  ]
}
```

**On error:**
```json
{"status": "error", "message": "Not authenticated. Call start_auth() first."}
```

**UI behavior:** Call this once after authentication completes. Populate a two-level selector: first a dropdown of course names, then a dropdown of assignment titles filtered to that course. Store `course_id` and `coursework_id` in state for the audit call — these IDs are not displayed to the teacher.

---

### `audit_assignment(course_id, coursework_id)`

Runs the full edit-history audit for all submissions on an assignment. This is the slow call — it fetches revision history for every student document. Expect 5–60 seconds depending on class size and document length.

**Arguments:**
- `course_id` (string): From `list_courses_and_assignments`
- `coursework_id` (string): From `list_courses_and_assignments`

**Returns (success):**
```json
{
  "status": "success",
  "course_id": "123456789",
  "coursework_id": "987654321",
  "summary": {
    "total_submissions": 24,
    "total_flags": 11,
    "high": 4,
    "medium": 5,
    "low": 2,
    "flagged_submissions": 7
  },
  "submissions": [
    {
      "label": "Submission A",
      "revision_count": 2,
      "final_word_count": 847,
      "flag_count": 3,
      "max_severity": "high",
      "drive_url": "https://docs.google.com/document/d/1BxiMVs0.../edit",
      "flags": [
        {
          "flag_type": "bulk_insertion",
          "severity": "high",
          "description": "Revision 2 added 1243 characters (89% of final document) in one step.",
          "snippet": "This is a very suspicious paste…"
        },
        {
          "flag_type": "few_revisions",
          "severity": "medium",
          "description": "Document has 847 words but only 2 revision(s).",
          "snippet": ""
        }
      ],
      "ai_summary": "The submission pattern shows..."
    }
  ]
}
```

**Returns (no submissions):**
```json
{
  "status": "success",
  "message": "No Google Doc submissions found for this assignment.",
  "summary": {"total_submissions": 0, "total_flags": 0},
  "submissions": []
}
```

**On error:**
```json
{"status": "error", "message": "<error detail>"}
```

**Submissions are pre-sorted** from highest to lowest severity, so the most suspicious submissions appear first.

**UI behavior:** Show a spinner/progress indicator while this runs. On completion, render the report as described in the UI layout section below.

---

## Flag Types

| `flag_type` | Severity | What it means |
|-------------|----------|---------------|
| `bulk_insertion` | high | A single revision added >20% of the final document length |
| `burst_editing` | high | >60% of content added within any 10-minute window |
| `few_revisions` | medium | Final doc >300 words but only 1–3 total revisions |
| `cold_start` | medium | First revision already contained >80% of the final document |
| `deadline_crunch` | medium | All edits occurred within 2 hours of the assignment deadline |
| `timing_off_hours` | low | All edits occurred outside 6:00 AM–11:00 PM |

Severity values: `"high"`, `"medium"`, `"low"`, `"none"` (no flags).

---

## Recommended UI Layout

### Tab 1 — Authentication

```
[ Google Account Status: Not Connected ]

[ Connect Google Account ]   ← calls start_auth(), begins polling check_auth_status()

Status message updates in place while polling.
Once authenticated, show: "Connected ✓" and enable Tab 2.

[ Sign Out ]  ← calls revoke_auth(), resets to unauthenticated state
```

### Tab 2 — Run Audit (enabled only when authenticated)

```
Course:      [ dropdown populated from list_courses_and_assignments ]
Assignment:  [ dropdown filtered by selected course               ]

[ Run Audit ]  ← calls audit_assignment(), shows spinner

─── Summary ──────────────────────────────────────────────────────
24 submissions · 7 flagged · 4 high · 5 medium · 2 low

─── Results ──────────────────────────────────────────────────────
Each submission rendered as an expandable card (sorted high→low):

┌─ Submission A  ●●● HIGH  [Open in Drive ↗] ─────────────────────┐
│ 847 words · 2 revisions · 3 flags                               │
│                                                                  │
│ [HIGH] bulk_insertion                                            │
│ Revision 2 added 89% of final document in one step.             │
│ Snippet: "This is a very suspicious paste…"                     │
│                                                                  │
│ [MEDIUM] few_revisions                                           │
│ Document has 847 words but only 2 revision(s).                  │
└──────────────────────────────────────────────────────────────────┘
```

- The "Open in Drive ↗" link is `submission["drive_url"]`. Open in a new tab. This link uses the teacher's own browser Google session — no special handling needed.
- If `ai_summary` is present and non-empty, render it below the flags as an italicized paragraph.
- Unflagged submissions (`flag_count == 0`) can be shown collapsed or in a separate "No flags" section.

---

## UI State Machine

```
UNAUTHENTICATED
    │
    ├─ click "Connect" → call start_auth()
    │       │
    │       └─ poll check_auth_status() every 2s
    │               │
    │               ├─ authenticated: false, flow in progress → keep polling
    │               ├─ status: error → show error, offer retry
    │               └─ authenticated: true → AUTHENTICATED
    │
AUTHENTICATED
    │
    ├─ on entry: call list_courses_and_assignments() → populate dropdowns
    │
    ├─ select course → filter assignment dropdown
    │
    ├─ click "Run Audit" → call audit_assignment() → LOADING
    │       │
    │       └─ on response → RESULTS
    │
    ├─ click "Sign Out" → call revoke_auth() → UNAUTHENTICATED
    │
LOADING
    │  (show spinner, disable controls)
    │
    └─ response received → RESULTS

RESULTS
    │  (render report cards)
    │
    ├─ click "Run Audit" again → LOADING (re-audits same assignment)
    └─ change assignment → LOADING
```

---

## Calling the Server from Python

The Gradio app calls these tools via the FastMCP Python client:

```python
from fastmcp import Client

async with Client("path/to/edmcp-editcheck/server.py") as client:
    result = await client.call_tool("check_auth_status", {})
    result = await client.call_tool("list_courses_and_assignments", {})
    result = await client.call_tool("audit_assignment", {
        "course_id": "123456789",
        "coursework_id": "987654321",
    })
```

All tools return dicts. Always check `result["status"] == "success"` before reading other fields. On `"error"`, display `result["message"]` to the teacher.

For the polling loop, use `asyncio` with `await asyncio.sleep(2)` between calls. Do not block Gradio's event loop with synchronous polling.

---

## What Not to Do

- **Do not display student names or emails.** The server strips them, and the UI must not reintroduce them.
- **Do not call `audit_assignment()` on every dropdown change** — it is slow and makes many Drive API calls. Only run on explicit button click.
- **Do not re-implement auth logic** in the UI. The server owns the full OAuth lifecycle. The UI only calls `start_auth()`, `check_auth_status()`, and `revoke_auth()`.
- **Do not cache `list_courses_and_assignments()` across sessions** — the teacher may add courses between uses.
