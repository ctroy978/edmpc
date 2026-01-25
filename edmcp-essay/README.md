# edmcp-essay

FastMCP server for batch essay grading workflows.

## Features

- **OCR Processing** - Batch PDF processing with automatic typed/scanned detection
- **PII Scrubbing** - Automated student name redaction using configurable name lists
- **AI Evaluation** - Rubric-based essay grading with RAG context support
- **Report Generation** - Individual PDF feedback reports and CSV gradebooks
- **Email Delivery** - Automated feedback delivery via SMTP/Brevo

## Tools

| Tool | Description |
|------|-------------|
| `batch_process_documents` | OCR/extract text from PDF directory |
| `scrub_processed_job` | Redact student PII |
| `evaluate_job` | Grade essays against rubric |
| `send_feedback_emails` | Email reports to students |
| `get_job_statistics` | View job manifest |
| `search_past_jobs` | Search archived jobs |
| `export_job_archive` | Export job as ZIP |

## Configuration

Required environment variables in `.env`:
- `QWEN_API_KEY` - For OCR (scanned documents)
- `XAI_API_KEY` - For evaluation
- `BREVO_API_KEY` - For email delivery (optional)

## Running

```bash
uv run python server.py
```
