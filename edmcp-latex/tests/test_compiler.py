"""Tests for the LaTeX compiler module."""

import pytest

from edmcp_latex.core import LatexCompiler, CompilationError


class TestLatexCompiler:
    """Tests for LatexCompiler class."""

    def test_check_installation(self, compiler):
        """Test that check_installation returns proper structure."""
        result = compiler.check_installation()
        assert "available" in result
        assert isinstance(result["available"], bool)
        if result["available"]:
            assert "version" in result
        else:
            assert "error" in result

    def test_compile_simple_document(self, compiler, simple_latex):
        """Test compiling a simple LaTeX document."""
        # Skip if pdflatex not available
        if not compiler.check_installation()["available"]:
            pytest.skip("pdflatex not available")

        result = compiler.compile(simple_latex)

        assert result["success"] is True
        assert "artifact_name" in result
        assert result["artifact_name"].endswith(".pdf")

    def test_compile_with_output_name(self, compiler, simple_latex):
        """Test compiling with a custom output name."""
        if not compiler.check_installation()["available"]:
            pytest.skip("pdflatex not available")

        result = compiler.compile(simple_latex, output_name="my_document")

        assert result["success"] is True
        assert "my_document_" in result["artifact_name"]

    def test_compile_invalid_latex(self, compiler):
        """Test that invalid LaTeX raises CompilationError."""
        if not compiler.check_installation()["available"]:
            pytest.skip("pdflatex not available")

        invalid_latex = r"""\documentclass{article}
\begin{document}
\undefined_command
\end{document}
"""
        with pytest.raises(CompilationError) as exc_info:
            compiler.compile(invalid_latex)

        assert exc_info.value.log is not None

    def test_get_artifact_not_found(self, compiler):
        """Test that get_artifact returns None for missing files."""
        result = compiler.get_artifact("nonexistent_file.pdf")
        assert result is None

    def test_get_artifact_after_compile(self, compiler, simple_latex):
        """Test retrieving an artifact after compilation."""
        if not compiler.check_installation()["available"]:
            pytest.skip("pdflatex not available")

        compile_result = compiler.compile(simple_latex)
        artifact = compiler.get_artifact(compile_result["artifact_name"])

        assert artifact is not None
        assert "data" in artifact
        assert artifact["filename"] == compile_result["artifact_name"]
        assert artifact["size_bytes"] > 0

    def test_list_artifacts_empty(self, compiler):
        """Test list_artifacts on empty directory."""
        artifacts = compiler.list_artifacts()
        assert artifacts == []

    def test_list_artifacts_after_compile(self, compiler, simple_latex):
        """Test list_artifacts after compiling a document."""
        if not compiler.check_installation()["available"]:
            pytest.skip("pdflatex not available")

        compiler.compile(simple_latex, template_used="test_template", title="Test Doc")
        artifacts = compiler.list_artifacts()

        assert len(artifacts) == 1
        assert artifacts[0]["name"].endswith(".pdf")
        assert artifacts[0]["size_bytes"] > 0
        assert artifacts[0]["template_used"] == "test_template"
        assert artifacts[0]["title"] == "Test Doc"
        assert "created_at" in artifacts[0]

    def test_parse_log_extracts_errors(self, compiler):
        """Test that _parse_log extracts error lines."""
        log = """This is pdfTeX
! Undefined control sequence.
l.5 \\badcommand
?
"""
        errors = compiler._parse_log(log)
        assert len(errors) > 0
        assert "Undefined control sequence" in errors[0]
