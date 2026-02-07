"""Pytest configuration and fixtures for edmcp-latex tests."""

import sys
from pathlib import Path

import pytest
from edmcp_core import DatabaseManager

from edmcp_latex.core import LatexCompiler, TemplateManager


@pytest.fixture
def test_db_manager(tmp_path):
    """Create a DatabaseManager with a temporary database."""
    db_path = tmp_path / "test_latex.db"
    return DatabaseManager(db_path)


@pytest.fixture
def compiler(test_db_manager):
    """Create a LatexCompiler with a test database manager."""
    return LatexCompiler(db_manager=test_db_manager)


@pytest.fixture(autouse=True)
def reset_server_state(tmp_path):
    """Reset server global state before each test to ensure isolation."""
    # Add parent directory to path so we can import server module
    sys.path.insert(0, str(Path(__file__).parent.parent))

    import server

    # Reset global state
    server._db_manager = None
    server._compiler = None
    server._template_manager = None

    # Point to a temp database for server tests
    server.DB_PATH = tmp_path / "test_server.db"

    yield

    # Clean up after test
    if server._db_manager is not None:
        server._db_manager.close()
    server._db_manager = None
    server._compiler = None
    server._template_manager = None


@pytest.fixture
def template_manager():
    """Create a TemplateManager with the default templates directory."""
    return TemplateManager()


@pytest.fixture
def simple_latex():
    """Return minimal valid LaTeX document."""
    return r"""\documentclass{article}
\begin{document}
Hello, World!
\end{document}
"""
