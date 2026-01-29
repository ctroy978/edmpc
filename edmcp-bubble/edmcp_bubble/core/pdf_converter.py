"""
PDF to Image Converter for bubble sheet grading.

Converts PDF bytes to OpenCV images for processing by the scanner.
"""

from __future__ import annotations

from typing import Iterator, Tuple

import cv2
import numpy as np
from pdf2image import convert_from_bytes


def pdf_bytes_to_images(
    pdf_bytes: bytes, dpi: int = 300
) -> Iterator[Tuple[int, np.ndarray]]:
    """
    Convert PDF bytes to OpenCV BGR images.

    Args:
        pdf_bytes: Raw PDF content as bytes
        dpi: Resolution for rendering (higher = larger images, better accuracy)

    Yields:
        Tuples of (page_number, cv2_image) where page_number is 1-indexed
    """
    pil_images = convert_from_bytes(pdf_bytes, dpi=dpi)

    for page_num, pil_image in enumerate(pil_images, start=1):
        # Convert PIL RGB to OpenCV BGR
        rgb_array = np.array(pil_image)
        bgr_array = cv2.cvtColor(rgb_array, cv2.COLOR_RGB2BGR)
        yield page_num, bgr_array


def count_pdf_pages(pdf_bytes: bytes) -> int:
    """
    Count the number of pages in a PDF without fully rendering them.

    Args:
        pdf_bytes: Raw PDF content as bytes

    Returns:
        Number of pages in the PDF
    """
    # Use low DPI just to count pages quickly
    pil_images = convert_from_bytes(pdf_bytes, dpi=72)
    return len(pil_images)
