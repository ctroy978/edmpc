"""Pytest configuration and fixtures for edmcp-latex tests."""

import tempfile
from pathlib import Path

import pytest

from edmcp_latex.core import LatexCompiler, TemplateManager


@pytest.fixture
def temp_artifacts_dir():
    """Create a temporary directory for test artifacts."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def compiler(temp_artifacts_dir):
    """Create a LatexCompiler with a temporary artifacts directory."""
    return LatexCompiler(artifacts_dir=temp_artifacts_dir)


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
