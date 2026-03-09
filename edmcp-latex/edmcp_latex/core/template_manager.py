"""Template management for LaTeX document generation."""

from pathlib import Path
from typing import Optional


class TemplateManager:
    """Manages LaTeX templates for document generation."""

    # Template metadata for descriptions
    TEMPLATE_DESCRIPTIONS = {
        "academic": "Two-column academic handout with title banner and footnotes section",
        "simple": "Minimal single-column document with clean formatting",
        "quiz": "Quiz/worksheet format with name and date fields",
        "verse": "Two-column poetry layout with preserved line breaks",
    }

    def __init__(self, templates_dir: Optional[Path] = None):
        """Initialize the template manager.

        Args:
            templates_dir: Directory containing .tex templates. Defaults to
                          the templates directory in this package.
        """
        if templates_dir is None:
            templates_dir = Path(__file__).parent.parent / "templates"
        self.templates_dir = Path(templates_dir)

    def list_templates(self) -> list[dict]:
        """List all available templates.

        Returns:
            List of dicts with 'name' and 'description' for each template.
        """
        templates = []
        for path in sorted(self.templates_dir.glob("*.tex")):
            name = path.stem
            description = self.TEMPLATE_DESCRIPTIONS.get(
                name, "No description available"
            )
            templates.append({
                "name": name,
                "description": description,
            })
        return templates

    def get_template(self, name: str) -> Optional[str]:
        """Load a template by name.

        Args:
            name: Template name (without .tex extension).

        Returns:
            Template content as string, or None if not found.
        """
        template_path = self.templates_dir / f"{name}.tex"
        if not template_path.exists():
            return None
        return template_path.read_text(encoding="utf-8")

    def escape_latex(self, text: str) -> str:
        """Escape special LaTeX characters in plain text.

        Args:
            text: Plain text string that should be treated as literal text.

        Returns:
            String safe for insertion into LaTeX source.
        """
        # Process each character position once to avoid double-escaping.
        # Backslash must be handled first; braces must be handled before
        # characters whose replacements introduce braces (textbackslash etc).
        result = []
        for ch in text:
            if ch == "\\":
                result.append("\\textbackslash{}")
            elif ch == "{":
                result.append("\\{")
            elif ch == "}":
                result.append("\\}")
            elif ch == "&":
                result.append("\\&")
            elif ch == "%":
                result.append("\\%")
            elif ch == "$":
                result.append("\\$")
            elif ch == "#":
                result.append("\\#")
            elif ch == "_":
                result.append("\\_")
            elif ch == "~":
                result.append("\\textasciitilde{}")
            elif ch == "^":
                result.append("\\textasciicircum{}")
            else:
                result.append(ch)
        return "".join(result)

    def render(
        self,
        template_name: str,
        title: str = "",
        content: str = "",
        author: str = "",
        footnotes: str = "",
    ) -> str:
        """Render a template with the given parameters.

        Args:
            template_name: Name of the template to use.
            title: Document title (plain text, will be escaped).
            content: Main document content (LaTeX allowed, not escaped).
            author: Author name (plain text, will be escaped).
            footnotes: Footnotes/notes section content (LaTeX allowed, not escaped).

        Returns:
            Rendered LaTeX source code.

        Raises:
            ValueError: If template not found.
        """
        template = self.get_template(template_name)
        if template is None:
            available = [t["name"] for t in self.list_templates()]
            raise ValueError(
                f"Template '{template_name}' not found. "
                f"Available templates: {', '.join(available)}"
            )

        # Perform placeholder substitution
        # Placeholders use {{name}} format
        # All user-supplied text is plain text — escape LaTeX special chars
        rendered = template
        rendered = self._substitute(rendered, "title", self.escape_latex(title))
        rendered = self._substitute(rendered, "content", self.escape_latex(content))
        rendered = self._substitute(rendered, "author", self.escape_latex(author))
        rendered = self._substitute(rendered, "footnotes", self.escape_latex(footnotes))

        return rendered

    def _substitute(self, template: str, key: str, value: str) -> str:
        """Substitute a placeholder in the template.

        Placeholders use {{key}} format. In LaTeX templates, if the placeholder
        is inside LaTeX braces like \\textbf{{{title}}}, the outer braces are
        for LaTeX and the inner {{title}} is the placeholder.

        Args:
            template: The template string.
            key: The placeholder key.
            value: The value to substitute.

        Returns:
            Template with substitution applied.
        """
        # Only replace {{key}} (double braces) - the placeholder format
        # Single braces around it (like {{{key}}}) are LaTeX braces
        template = template.replace("{{" + key + "}}", value)
        return template
