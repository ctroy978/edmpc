"""LaTeX compilation module for generating PDFs from LaTeX source."""

import base64
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from edmcp_core import DatabaseManager


class CompilationError(Exception):
    """Raised when LaTeX compilation fails."""

    def __init__(self, message: str, log: str):
        super().__init__(message)
        self.log = log


class LatexCompiler:
    """Compiles LaTeX source to PDF using pdflatex."""

    def __init__(self, db_manager: "DatabaseManager"):
        """Initialize the compiler with a database manager.

        Args:
            db_manager: DatabaseManager instance for storing artifacts.
        """
        self.db_manager = db_manager

    def check_installation(self) -> dict:
        """Check if pdflatex is available.

        Returns:
            Dict with 'available' bool and 'version' or 'error' string.
        """
        try:
            result = subprocess.run(
                ["pdflatex", "--version"],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode == 0:
                # Extract first line as version info
                version = result.stdout.split("\n")[0] if result.stdout else "Unknown"
                return {"available": True, "version": version}
            else:
                return {"available": False, "error": "pdflatex returned non-zero exit code"}
        except FileNotFoundError:
            return {"available": False, "error": "pdflatex not found in PATH"}

    def compile(
        self,
        latex_code: str,
        image_assets: Optional[list[dict]] = None,
        output_name: str = "document",
        template_used: Optional[str] = None,
        title: Optional[str] = None,
    ) -> dict:
        """Compile LaTeX source code to PDF.

        Args:
            latex_code: The LaTeX source code to compile.
            image_assets: Optional list of image assets. Each should have:
                         - 'data': base64-encoded image data
                         - 'filename': filename to use in the temp directory
            output_name: Base name for the output file (without extension).
            template_used: Optional template name used to generate the document.
            title: Optional document title for metadata.

        Returns:
            Dict with 'success', 'artifact_name', and 'log'.

        Raises:
            CompilationError: If compilation fails.
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            work_dir = Path(temp_dir)

            # Write image assets to temp directory
            if image_assets:
                for asset in image_assets:
                    if "data" in asset and "filename" in asset:
                        image_path = work_dir / asset["filename"]
                        image_data = base64.b64decode(asset["data"])
                        image_path.write_bytes(image_data)

            # Write LaTeX source
            tex_file = work_dir / "document.tex"
            tex_file.write_text(latex_code, encoding="utf-8")

            # Run pdflatex
            try:
                result = subprocess.run(
                    [
                        "pdflatex",
                        "-interaction=nonstopmode",
                        "-halt-on-error",
                        f"-output-directory={work_dir}",
                        str(tex_file),
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                    cwd=str(work_dir),
                )
            except FileNotFoundError:
                raise CompilationError(
                    "pdflatex not found. Please install a LaTeX distribution.",
                    "",
                )

            log_output = result.stdout + "\n" + result.stderr
            pdf_file = work_dir / "document.pdf"

            if result.returncode != 0:
                errors = self._parse_log(log_output)
                error_summary = "\n".join(errors) if errors else "Unknown compilation error"
                raise CompilationError(
                    f"LaTeX compilation failed:\n{error_summary}",
                    log_output,
                )

            if not pdf_file.exists():
                raise CompilationError(
                    "pdflatex succeeded but PDF was not created.",
                    log_output,
                )

            # Save artifact to database
            artifact_name = self._save_artifact(
                pdf_file, output_name, template_used, title
            )

            return {
                "success": True,
                "artifact_name": artifact_name,
                "log": log_output,
            }

    def get_artifact(self, artifact_name: str) -> Optional[dict]:
        """Retrieve a compiled PDF artifact.

        Args:
            artifact_name: Name of the artifact file.

        Returns:
            Dict with 'data' (base64), 'filename', 'size_bytes', or None if not found.
        """
        result = self.db_manager.get_latex_artifact(artifact_name)
        if result is None:
            return None

        return {
            "data": base64.b64encode(result["content"]).decode("utf-8"),
            "filename": result["artifact_name"],
            "size_bytes": result["size_bytes"],
        }

    def list_artifacts(self) -> list[dict]:
        """List all artifacts in the database.

        Returns:
            List of dicts with 'name', 'size_bytes', 'created_at', 'template_used', 'title'.
        """
        artifacts = self.db_manager.list_latex_artifacts()
        return [
            {
                "name": a["artifact_name"],
                "size_bytes": a["size_bytes"],
                "created_at": a["created_at"],
                "template_used": a["template_used"],
                "title": a["title"],
            }
            for a in artifacts
        ]

    def _save_artifact(
        self,
        pdf_path: Path,
        base_name: str,
        template_used: Optional[str] = None,
        title: Optional[str] = None,
    ) -> str:
        """Save PDF to database with unique name.

        Args:
            pdf_path: Path to the source PDF.
            base_name: Base name for the artifact (without extension).
            template_used: Optional template name used.
            title: Optional document title.

        Returns:
            The artifact name.
        """
        unique_id = uuid.uuid4().hex[:8]
        artifact_name = f"{base_name}_{unique_id}.pdf"
        pdf_bytes = pdf_path.read_bytes()
        self.db_manager.store_latex_artifact(
            artifact_name, pdf_bytes, template_used, title
        )
        return artifact_name

    def _parse_log(self, log: str) -> list[str]:
        """Parse LaTeX log and extract error messages.

        Args:
            log: The full log output from pdflatex.

        Returns:
            List of error message strings.
        """
        errors = []
        lines = log.splitlines()
        current_error = []

        for line in lines:
            if line.startswith("!"):
                if current_error:
                    errors.append("\n".join(current_error))
                current_error = [line]
            elif current_error:
                current_error.append(line)
                # Stop collecting after a few lines
                if len(current_error) > 5:
                    errors.append("\n".join(current_error))
                    current_error = []

        if current_error:
            errors.append("\n".join(current_error))

        return errors
