# edmcp-core

Shared core library for edmcp workflow servers.

## Components

- **DatabaseManager** - SQLite database operations for job tracking, student records, and report storage
- **KnowledgeBaseManager** - RAG-based knowledge base using ChromaDB and LlamaIndex for context retrieval
- **Utilities** - Retry logic, JSON extraction, OpenAI client factory, JSONL file operations

## Usage

```python
from edmcp_core import DatabaseManager, KnowledgeBaseManager, get_openai_client, retry_with_backoff
```

## Installation

This package is installed as a local dependency by workflow servers:

```toml
# In pyproject.toml
[tool.uv.sources]
edmcp-core = { path = "../edmcp-core", editable = true }
```
