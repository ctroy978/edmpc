# edmcp

A modular MCP (Model Context Protocol) server architecture for educational document processing workflows.

## Structure

```
edmcp/
├── edmcp-core/      # Shared library (database, knowledge base, utilities)
├── edmcp-essay/     # Essay grading workflow server
└── edmcp-bubble/    # Bubble sheet test grading server
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

---

## Bubble Sheet Grading

The `edmcp-bubble` server handles bubble sheet test creation, scanning, and grading.

### Multiple-Select Scoring Formula (Canvas-style)

For "select all that apply" questions, partial credit is calculated using this formula:

```
score = max(0, (hits - incorrect) × points_per_option)
```

Where:
- **points_per_option** = total_points / number_of_correct_options
- **hits** = number of correct options the student selected
- **incorrect** = number of incorrect options the student selected

### Scoring Examples

Given a question worth 1 point where the correct answer is **a,d**:

| Student Answer | Hits | Incorrect | Calculation | Score |
|----------------|------|-----------|-------------|-------|
| a,d | 2 | 0 | (2-0) × 0.5 | **1.0** (full credit) |
| a | 1 | 0 | (1-0) × 0.5 | **0.5** (partial) |
| d | 1 | 0 | (1-0) × 0.5 | **0.5** (partial) |
| a,c | 1 | 1 | (1-1) × 0.5 | **0.0** |
| a,d,c | 2 | 1 | (2-1) × 0.5 | **0.5** |
| a,b,c,d | 2 | 2 | (2-2) × 0.5 | **0.0** |
| b,c | 0 | 2 | (0-2) × 0.5 = -1 → max(0,-1) | **0.0** |
| (blank) | 0 | 0 | (0-0) × 0.5 | **0.0** |

### Key Behaviors

1. **Partial credit is possible** - selecting some correct answers without incorrect ones earns partial points
2. **Incorrect selections penalize** - each wrong choice subtracts from the score
3. **Score cannot go negative** - the `max(0, ...)` ensures a floor of 0
4. **Single-select questions** use exact match only (no partial credit)
