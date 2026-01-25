# edmcp

A modular MCP (Model Context Protocol) server architecture for educational document processing workflows.

## Structure

```
edmcp/
├── edmcp-core/      # Shared library (database, knowledge base, utilities)
└── edmcp-essay/     # Essay grading workflow server
```

## Installation

Requires [uv](https://docs.astral.sh/uv/) for dependency management.

```bash
# Clone the repository
git clone <repo-url> edmcp
cd edmcp

# Install edmcp-core (shared library)
cd edmcp-core && uv sync && cd ..

# Install edmcp-essay (essay grading server)
cd edmcp-essay && uv sync

# Copy and configure environment
cp .env.example .env
# Edit .env with your API keys
```

## Running

```bash
cd edmcp-essay
uv run python server.py
```

For development with the MCP Inspector:
```bash
uv run fastmcp dev server.py
```

## Adding New Workflows

Create a new server directory (e.g., `edmcp-quiz/`) with its own `pyproject.toml` that depends on `edmcp-core`. Follow the pattern established in `edmcp-essay/`.
