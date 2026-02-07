"""
LaTeX MCP Server - FastMCP server for educational document generation.

Tools for compiling LaTeX documents, managing templates, and retrieving artifacts.
"""

import json
from pathlib import Path
from typing import Optional

from fastmcp import FastMCP
from edmcp_core import DatabaseManager

from edmcp_latex.core import LatexCompiler, TemplateManager, CompilationError


# Initialize MCP server
mcp = FastMCP("LaTeX Document Server")

# Database setup
SERVER_DIR = Path(__file__).parent
DB_PATH = SERVER_DIR / "edmcp.db"

# Lazy initialization of database, compiler and template manager
_db_manager: Optional[DatabaseManager] = None
_compiler: Optional[LatexCompiler] = None
_template_manager: Optional[TemplateManager] = None


def get_db_manager() -> DatabaseManager:
    """Get or create the database manager."""
    global _db_manager
    if _db_manager is None:
        _db_manager = DatabaseManager(DB_PATH)
    return _db_manager


def get_compiler() -> LatexCompiler:
    """Get or create the LaTeX compiler."""
    global _compiler
    if _compiler is None:
        _compiler = LatexCompiler(db_manager=get_db_manager())
    return _compiler


def get_template_manager() -> TemplateManager:
    """Get or create the template manager."""
    global _template_manager
    if _template_manager is None:
        _template_manager = TemplateManager()
    return _template_manager


@mcp.tool()
def ping() -> str:
    """
    Health check endpoint.

    Returns:
        "pong" if the server is running
    """
    return json.dumps({
        "status": "success",
        "message": "pong",
    })


@mcp.tool()
def list_templates() -> str:
    """
    List available LaTeX templates.

    Returns:
        JSON with list of templates, each having name and description
    """
    manager = get_template_manager()
    templates = manager.list_templates()

    return json.dumps({
        "status": "success",
        "count": len(templates),
        "templates": templates,
    })


@mcp.tool()
def check_latex_installation() -> str:
    """
    Check if pdflatex is available on the system.

    Returns:
        JSON with availability status and version info
    """
    compiler = get_compiler()
    result = compiler.check_installation()

    if result["available"]:
        return json.dumps({
            "status": "success",
            "available": True,
            "version": result["version"],
        })
    else:
        return json.dumps({
            "status": "error",
            "available": False,
            "message": result["error"],
        })


@mcp.tool()
def compile_latex(latex_code: str, image_assets: str = "[]") -> str:
    """
    Compile raw LaTeX source code to PDF.

    This is a power-user tool for compiling arbitrary LaTeX code.
    For most use cases, use generate_document() with a template instead.

    Args:
        latex_code: Complete LaTeX source code to compile
        image_assets: JSON array of image assets, each with:
                     - "data": base64-encoded image data
                     - "filename": filename to use (e.g., "figure1.png")

    Returns:
        JSON with artifact name and path on success, or error details
    """
    compiler = get_compiler()

    # Parse image assets
    try:
        assets = json.loads(image_assets) if image_assets else []
        if not isinstance(assets, list):
            return json.dumps({
                "status": "error",
                "message": "image_assets must be a JSON array",
            })
    except json.JSONDecodeError as e:
        return json.dumps({
            "status": "error",
            "message": f"Invalid image_assets JSON: {e}",
        })

    try:
        result = compiler.compile(
            latex_code=latex_code,
            image_assets=assets if assets else None,
            output_name="document",
        )
        return json.dumps({
            "status": "success",
            "artifact_name": result["artifact_name"],
            "message": f"Compiled successfully: {result['artifact_name']}",
        })
    except CompilationError as e:
        return json.dumps({
            "status": "error",
            "message": str(e),
            "log": e.log[:2000] if e.log else None,  # Truncate long logs
        })


@mcp.tool()
def generate_document(
    template_name: str,
    title: str,
    content: str,
    author: str = "",
    footnotes: str = "",
    image_assets: str = "[]",
) -> str:
    """
    Generate a PDF document using a template.

    This is the preferred method for creating documents. It handles
    template rendering and compilation in one step.

    Args:
        template_name: Name of the template (e.g., "simple", "academic", "quiz")
        title: Document title
        content: Main document content (can include LaTeX formatting)
        author: Author name (optional)
        footnotes: Notes/footnotes section content (optional)
        image_assets: JSON array of image assets (optional), each with:
                     - "data": base64-encoded image data
                     - "filename": filename to use (e.g., "figure1.png")

    Returns:
        JSON with artifact name and path on success, or error details
    """
    template_manager = get_template_manager()
    compiler = get_compiler()

    # Parse image assets
    try:
        assets = json.loads(image_assets) if image_assets else []
        if not isinstance(assets, list):
            return json.dumps({
                "status": "error",
                "message": "image_assets must be a JSON array",
            })
    except json.JSONDecodeError as e:
        return json.dumps({
            "status": "error",
            "message": f"Invalid image_assets JSON: {e}",
        })

    # Render template
    try:
        latex_code = template_manager.render(
            template_name=template_name,
            title=title,
            content=content,
            author=author,
            footnotes=footnotes,
        )
    except ValueError as e:
        return json.dumps({
            "status": "error",
            "message": str(e),
        })

    # Compile to PDF
    try:
        # Use sanitized title for output name
        safe_title = "".join(c for c in title if c.isalnum() or c in " _-")[:30]
        output_name = safe_title.strip().replace(" ", "_") or "document"

        result = compiler.compile(
            latex_code=latex_code,
            image_assets=assets if assets else None,
            output_name=output_name,
            template_used=template_name,
            title=title,
        )
        return json.dumps({
            "status": "success",
            "artifact_name": result["artifact_name"],
            "template": template_name,
            "message": f"Generated document: {result['artifact_name']}",
        })
    except CompilationError as e:
        return json.dumps({
            "status": "error",
            "message": str(e),
            "log": e.log[:2000] if e.log else None,
        })


@mcp.tool()
def get_artifact(artifact_name: str) -> str:
    """
    Retrieve a compiled PDF artifact.

    Args:
        artifact_name: Name of the artifact file (e.g., "document_abc123.pdf")

    Returns:
        JSON with base64-encoded PDF data, or error if not found
    """
    compiler = get_compiler()
    result = compiler.get_artifact(artifact_name)

    if result is None:
        # List available artifacts for help
        artifacts = compiler.list_artifacts()
        available = [a["name"] for a in artifacts[:10]]

        return json.dumps({
            "status": "error",
            "message": f"Artifact not found: {artifact_name}",
            "available_artifacts": available,
        })

    return json.dumps({
        "status": "success",
        "artifact_name": result["filename"],
        "size_bytes": result["size_bytes"],
        "content_type": "application/pdf",
        "encoding": "base64",
        "data": result["data"],
    })


if __name__ == "__main__":
    mcp.run()
