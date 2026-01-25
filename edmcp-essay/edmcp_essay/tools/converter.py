"""
Document conversion utilities for the OCR-MCP pipeline.
Handles conversions between common document formats to ensure compatibility.
"""

import os
import subprocess
from pathlib import Path
from typing import Union, List, Optional
import tempfile

try:
    from PIL import Image

    HAS_PIL = True
except ImportError:
    HAS_PIL = False
    Image = None  # type: ignore


class DocumentConverter:
    """
    Converts documents between formats to ensure pipeline compatibility.
    Supports: DOCX/DOC -> PDF, PDF -> TXT, Images -> PDF
    """

    def __init__(self):
        self._check_dependencies()

    def _check_dependencies(self):
        """Check if required conversion tools are available."""
        # Check for pdftotext (part of poppler-utils)
        self.has_pdftotext = self._command_exists("pdftotext")

        # Check for PIL/Pillow (for Image -> PDF)
        self.has_pil = HAS_PIL

    def _command_exists(self, command: str) -> bool:
        """Check if a command exists in PATH."""
        return (
            subprocess.run(
                ["which", command], stdout=subprocess.PIPE, stderr=subprocess.PIPE
            ).returncode
            == 0
        )

    def convert_pdf_to_text(
        self,
        input_path: Union[str, Path],
        output_path: Optional[Union[str, Path]] = None,
        use_ocr: bool = False,
    ) -> Path:
        """
        Converts a PDF to plain text.

        Args:
            input_path: Path to the PDF file
            output_path: Path for the output text file (defaults to input_path with .txt extension)
            use_ocr: If True, uses OCR for scanned PDFs (requires OCRTool)

        Returns:
            Path to the generated text file

        Raises:
            RuntimeError: If pdftotext is not available (when use_ocr=False)
            FileNotFoundError: If input file doesn't exist
        """
        input_path = Path(input_path)
        if not input_path.exists():
            raise FileNotFoundError(f"Input file not found: {input_path}")

        # Determine output path
        if output_path is None:
            output_path = input_path.with_suffix(".txt")
        else:
            output_path = Path(output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)

        if use_ocr:
            # Use OCRTool for scanned documents
            from edmcp_essay.tools.ocr import OCRTool

            ocr_tool = OCRTool()
            text = ocr_tool.extract_text_via_ocr(input_path)
            output_path.write_text(text, encoding="utf-8")
        else:
            # Use pdftotext for native text extraction
            if not self.has_pdftotext:
                raise RuntimeError(
                    "pdftotext is not installed. Please install poppler-utils:\n"
                    "  Ubuntu/Debian: sudo apt-get install poppler-utils\n"
                    "  macOS: brew install poppler\n"
                    "  Or set use_ocr=True to extract text via OCR instead."
                )

            cmd = ["pdftotext", "-layout", str(input_path), str(output_path)]

            result = subprocess.run(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
            )

            if result.returncode != 0:
                raise RuntimeError(
                    f"PDF to text conversion failed:\n"
                    f"Command: {' '.join(cmd)}\n"
                    f"Error: {result.stderr}"
                )

        return output_path

    def convert_image_to_pdf(
        self,
        input_path: Union[str, Path],
        output_path: Optional[Union[str, Path]] = None,
    ) -> Path:
        """
        Converts an image file (JPEG, PNG) to PDF.

        Args:
            input_path: Path to the image file
            output_path: Path for the output PDF (defaults to same name with .pdf extension)

        Returns:
            Path to the generated PDF file

        Raises:
            RuntimeError: If PIL/Pillow is not available
            FileNotFoundError: If input file doesn't exist
        """
        if not self.has_pil:
            raise RuntimeError(
                "PIL/Pillow is not installed. Please install it:\n"
                "  pip install Pillow\n"
                "  or: uv add pillow"
            )

        input_path = Path(input_path)
        if not input_path.exists():
            raise FileNotFoundError(f"Input file not found: {input_path}")

        # Verify it's an image file
        valid_extensions = {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".tiff", ".tif"}
        if input_path.suffix.lower() not in valid_extensions:
            raise ValueError(
                f"Unsupported image format: {input_path.suffix}. "
                f"Supported formats: {', '.join(valid_extensions)}"
            )

        # Determine output path
        if output_path is None:
            output_path = input_path.with_suffix(".pdf")
        else:
            output_path = Path(output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            # Open and convert image
            if Image is None:
                raise RuntimeError("PIL/Pillow not available")
            image = Image.open(input_path)

            # Convert to RGB if necessary (PDF doesn't support RGBA or other modes well)
            if image.mode in ("RGBA", "LA", "P"):
                # Create white background
                rgb_image = Image.new("RGB", image.size, (255, 255, 255))
                if image.mode == "P":
                    image = image.convert("RGBA")
                rgb_image.paste(
                    image,
                    mask=image.split()[-1] if image.mode in ("RGBA", "LA") else None,
                )
                image = rgb_image
            elif image.mode != "RGB":
                image = image.convert("RGB")

            # Save as PDF
            image.save(output_path, "PDF", resolution=100.0)

            return output_path

        except Exception as e:
            raise RuntimeError(f"Image to PDF conversion failed: {str(e)}")

    def batch_convert_images_to_pdf(
        self,
        input_dir: Union[str, Path],
        output_dir: Union[str, Path],
        extensions: Optional[List[str]] = None,
    ) -> List[Path]:
        """
        Converts all image files in a directory to individual PDF files.

        Args:
            input_dir: Directory containing image files
            output_dir: Directory to save converted PDFs
            extensions: Image extensions to convert (default: ['.jpg', '.jpeg', '.png'])

        Returns:
            List of paths to generated PDF files
        """
        if extensions is None:
            extensions = [".jpg", ".jpeg", ".png"]

        input_dir = Path(input_dir)
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        converted_files = []
        errors = []

        # Find all matching files
        for ext in extensions:
            # Case-insensitive search
            for pattern in [f"*{ext}", f"*{ext.upper()}"]:
                for file_path in input_dir.glob(pattern):
                    try:
                        pdf_path = self.convert_image_to_pdf(
                            file_path, output_dir / f"{file_path.stem}.pdf"
                        )
                        converted_files.append(pdf_path)
                    except Exception as e:
                        errors.append(f"{file_path.name}: {str(e)}")

        if errors:
            print(f"[Converter] Warnings during batch image conversion: {errors}")

        return converted_files

    def merge_images_to_pdf(
        self, image_paths: List[Union[str, Path]], output_path: Union[str, Path]
    ) -> Path:
        """
        Merges multiple images into a single multi-page PDF.
        Useful for combining scanned essay pages into one document.

        Args:
            image_paths: List of paths to image files (in order)
            output_path: Path for the output PDF file

        Returns:
            Path to the generated PDF file

        Raises:
            RuntimeError: If PIL/Pillow is not available
            FileNotFoundError: If any input file doesn't exist
        """
        if not self.has_pil:
            raise RuntimeError(
                "PIL/Pillow is not installed. Please install it:\n"
                "  pip install Pillow\n"
                "  or: uv add pillow"
            )

        if not image_paths:
            raise ValueError("No image paths provided")

        # Convert all to Path objects and verify existence
        path_objects: List[Path] = [Path(p) for p in image_paths]
        for img_path in path_objects:
            if not img_path.exists():
                raise FileNotFoundError(f"Input file not found: {img_path}")

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            # Open and convert all images
            if Image is None:
                raise RuntimeError("PIL/Pillow not available")
            images = []
            for img_path in path_objects:
                img = Image.open(img_path)

                # Convert to RGB
                if img.mode in ("RGBA", "LA", "P"):
                    rgb_image = Image.new("RGB", img.size, (255, 255, 255))
                    if img.mode == "P":
                        img = img.convert("RGBA")
                    rgb_image.paste(
                        img,
                        mask=img.split()[-1] if img.mode in ("RGBA", "LA") else None,
                    )
                    img = rgb_image
                elif img.mode != "RGB":
                    img = img.convert("RGB")

                images.append(img)

            # Save as multi-page PDF
            if len(images) == 1:
                images[0].save(output_path, "PDF", resolution=100.0)
            else:
                images[0].save(
                    output_path,
                    "PDF",
                    resolution=100.0,
                    save_all=True,
                    append_images=images[1:],
                )

            return output_path

        except Exception as e:
            raise RuntimeError(f"Image merge to PDF failed: {str(e)}")

    def get_conversion_info(self) -> dict:
        """
        Returns information about available conversion capabilities.

        Returns:
            Dictionary with capability flags and installation instructions
        """
        return {
            "pdf_to_text": {
                "available": self.has_pdftotext,
                "tool": "pdftotext (poppler-utils)",
                "install_instructions": {
                    "ubuntu": "sudo apt-get install poppler-utils",
                    "macos": "brew install poppler",
                    "windows": "Download from https://blog.alivate.com.au/poppler-windows/",
                },
                "alternative": "Set use_ocr=True to use OCR instead",
            },
            "image_to_pdf": {
                "available": self.has_pil,
                "tool": "PIL/Pillow",
                "install_instructions": {
                    "ubuntu": "pip install Pillow or uv add pillow",
                    "macos": "pip install Pillow or uv add pillow",
                    "windows": "pip install Pillow",
                },
                "note": "Should already be installed as pdf2image depends on it",
            },
        }
