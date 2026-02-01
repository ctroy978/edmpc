"""Tests for the MCP server tools."""

import json
import sys
from pathlib import Path

import pytest

# Add parent directory to path so we can import server module
sys.path.insert(0, str(Path(__file__).parent.parent))


class TestServerTools:
    """Tests for MCP server tool functions."""

    def test_ping(self):
        """Test the ping tool."""
        from server import ping

        # FastMCP wraps functions as FunctionTool, access via .fn
        result = json.loads(ping.fn())

        assert result["status"] == "success"
        assert result["message"] == "pong"

    def test_list_templates(self):
        """Test the list_templates tool."""
        from server import list_templates

        result = json.loads(list_templates.fn())

        assert result["status"] == "success"
        assert result["count"] >= 3
        assert "templates" in result

        names = [t["name"] for t in result["templates"]]
        assert "simple" in names
        assert "academic" in names
        assert "quiz" in names

    def test_check_latex_installation(self):
        """Test the check_latex_installation tool."""
        from server import check_latex_installation

        result = json.loads(check_latex_installation.fn())

        assert "available" in result
        if result["available"]:
            assert result["status"] == "success"
            assert "version" in result
        else:
            assert result["status"] == "error"
            assert "message" in result

    def test_compile_latex_simple(self):
        """Test compiling simple LaTeX code."""
        from server import compile_latex, check_latex_installation

        # Skip if pdflatex not available
        check = json.loads(check_latex_installation.fn())
        if not check["available"]:
            pytest.skip("pdflatex not available")

        latex_code = r"""\documentclass{article}
\begin{document}
Hello from test!
\end{document}
"""
        result = json.loads(compile_latex.fn(latex_code))

        assert result["status"] == "success"
        assert "artifact_name" in result
        assert result["artifact_name"].endswith(".pdf")

    def test_compile_latex_invalid(self):
        """Test compiling invalid LaTeX code."""
        from server import compile_latex, check_latex_installation

        check = json.loads(check_latex_installation.fn())
        if not check["available"]:
            pytest.skip("pdflatex not available")

        invalid_latex = r"""\documentclass{article}
\begin{document}
\badcommand
\end{document}
"""
        result = json.loads(compile_latex.fn(invalid_latex))

        assert result["status"] == "error"
        assert "message" in result

    def test_compile_latex_invalid_assets_json(self):
        """Test compile_latex with invalid image_assets JSON."""
        from server import compile_latex

        result = json.loads(compile_latex.fn(
            latex_code=r"\documentclass{article}\begin{document}x\end{document}",
            image_assets="not valid json",
        ))

        assert result["status"] == "error"
        assert "Invalid image_assets JSON" in result["message"]

    def test_generate_document_simple(self):
        """Test generating a document with simple template."""
        from server import generate_document, check_latex_installation

        check = json.loads(check_latex_installation.fn())
        if not check["available"]:
            pytest.skip("pdflatex not available")

        result = json.loads(generate_document.fn(
            template_name="simple",
            title="Test Document",
            content="This is test content.",
            author="Test Author",
        ))

        assert result["status"] == "success"
        assert "artifact_name" in result
        assert result["template"] == "simple"

    def test_generate_document_academic(self):
        """Test generating a document with academic template."""
        from server import generate_document, check_latex_installation

        check = json.loads(check_latex_installation.fn())
        if not check["available"]:
            pytest.skip("pdflatex not available")

        result = json.loads(generate_document.fn(
            template_name="academic",
            title="Research Summary",
            content="This study examines the effects of testing.",
            author="Dr. Researcher",
            footnotes="References available upon request.",
        ))

        assert result["status"] == "success"
        assert result["template"] == "academic"

    def test_generate_document_invalid_template(self):
        """Test generating document with invalid template."""
        from server import generate_document

        result = json.loads(generate_document.fn(
            template_name="nonexistent",
            title="Test",
            content="Test",
        ))

        assert result["status"] == "error"
        assert "not found" in result["message"]

    def test_get_artifact_not_found(self):
        """Test getting a non-existent artifact."""
        from server import get_artifact

        result = json.loads(get_artifact.fn("nonexistent_12345.pdf"))

        assert result["status"] == "error"
        assert "not found" in result["message"]

    def test_get_artifact_after_compile(self):
        """Test retrieving an artifact after compilation."""
        from server import compile_latex, get_artifact, check_latex_installation

        check = json.loads(check_latex_installation.fn())
        if not check["available"]:
            pytest.skip("pdflatex not available")

        # First compile something
        latex_code = r"""\documentclass{article}
\begin{document}
Artifact test document.
\end{document}
"""
        compile_result = json.loads(compile_latex.fn(latex_code))
        assert compile_result["status"] == "success"

        # Now retrieve it
        artifact_result = json.loads(get_artifact.fn(compile_result["artifact_name"]))

        assert artifact_result["status"] == "success"
        assert "data" in artifact_result
        assert artifact_result["content_type"] == "application/pdf"
        assert artifact_result["size_bytes"] > 0
