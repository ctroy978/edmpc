"""Tests for the template manager module."""

import pytest

from edmcp_latex.core import TemplateManager


class TestTemplateManager:
    """Tests for TemplateManager class."""

    def test_list_templates(self, template_manager):
        """Test that list_templates returns expected templates."""
        templates = template_manager.list_templates()

        assert len(templates) >= 3
        names = [t["name"] for t in templates]
        assert "academic" in names
        assert "simple" in names
        assert "quiz" in names

        # Check structure
        for template in templates:
            assert "name" in template
            assert "description" in template

    def test_get_template_exists(self, template_manager):
        """Test loading an existing template."""
        content = template_manager.get_template("simple")

        assert content is not None
        assert "\\documentclass" in content
        assert "{{title}}" in content or "{{{title}}}" in content

    def test_get_template_not_found(self, template_manager):
        """Test that missing template returns None."""
        content = template_manager.get_template("nonexistent")
        assert content is None

    def test_render_simple(self, template_manager):
        """Test rendering the simple template."""
        rendered = template_manager.render(
            template_name="simple",
            title="Test Title",
            content="This is test content.",
            author="Test Author",
        )

        assert "Test Title" in rendered
        assert "Test Author" in rendered
        assert "This is test content." in rendered
        assert "\\documentclass" in rendered

    def test_render_academic(self, template_manager):
        """Test rendering the academic template."""
        rendered = template_manager.render(
            template_name="academic",
            title="Academic Paper",
            content="Introduction paragraph here.",
            author="Dr. Smith",
            footnotes="Source: Test Source",
        )

        assert "Academic Paper" in rendered
        assert "Dr. Smith" in rendered
        assert "Introduction paragraph here." in rendered
        assert "Source: Test Source" in rendered

    def test_render_quiz(self, template_manager):
        """Test rendering the quiz template."""
        rendered = template_manager.render(
            template_name="quiz",
            title="Chapter 5 Quiz",
            content="1. What is 2+2?",
            author="Mrs. Johnson",
        )

        assert "Chapter 5 Quiz" in rendered
        assert "Mrs. Johnson" in rendered
        assert "1. What is 2+2?" in rendered
        # Quiz template should have name/date fields
        assert "Name:" in rendered
        assert "Date:" in rendered

    def test_render_invalid_template(self, template_manager):
        """Test that rendering invalid template raises ValueError."""
        with pytest.raises(ValueError) as exc_info:
            template_manager.render(
                template_name="nonexistent",
                title="Test",
                content="Test",
            )

        assert "not found" in str(exc_info.value)
        assert "Available templates" in str(exc_info.value)

    def test_render_empty_optional_fields(self, template_manager):
        """Test rendering with empty optional fields."""
        rendered = template_manager.render(
            template_name="simple",
            title="Minimal Doc",
            content="Just content here.",
            author="",
            footnotes="",
        )

        assert "Minimal Doc" in rendered
        assert "Just content here." in rendered
        assert "\\documentclass" in rendered

    def test_render_with_latex_content(self, template_manager):
        """Test rendering with LaTeX formatting in content."""
        content_with_latex = r"""
This is \textbf{bold} and \textit{italic} text.

\begin{itemize}
\item First item
\item Second item
\end{itemize}
"""
        rendered = template_manager.render(
            template_name="simple",
            title="LaTeX Content Test",
            content=content_with_latex,
        )

        assert r"\textbf{bold}" in rendered
        assert r"\begin{itemize}" in rendered
