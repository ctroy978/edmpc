"""
Bubble Sheet Generator - Adapted from bubblexan.

Creates printable PDF answer sheets and JSON layout descriptions for OMR scanning.
Modified to write to BytesIO for database storage instead of files.
"""

from __future__ import annotations

import io
import json
import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple, Any

from reportlab.lib import colors
from reportlab.pdfgen import canvas


MM_TO_POINTS = 72.0 / 25.4
QUESTIONS_RANGE = (1, 50)
ID_LENGTH_RANGE = (4, 10)
DEFAULT_ID_LENGTH = 6
OPTIONS = ["A", "B", "C", "D", "E"]
ID_ORIENTATIONS = ("vertical", "horizontal")

PAPER_SIZES: Dict[str, Tuple[int, int]] = {
    "A4": (595, 842),
    "LETTER": (612, 792),
}


def mm_to_points(value: float) -> float:
    return value * MM_TO_POINTS


@dataclass(frozen=True)
class LayoutSettings:
    margin: float
    bubble_radius: float
    question_label_width: float
    question_row_height: float
    question_column_spacing: float
    question_column_spacing_min: float
    option_step: float
    option_label_gap: float
    id_column_step: float
    id_vertical_step: float
    section_gap: float
    title_block: float
    student_id_header_gap: float
    digit_label_gap: float
    student_id_marker_clearance: float
    alignment_clearance: float


def build_layout_settings() -> LayoutSettings:
    bubble_diameter = mm_to_points(4.0)
    return LayoutSettings(
        margin=36.0,
        bubble_radius=bubble_diameter / 2,
        question_label_width=mm_to_points(12.0),
        question_row_height=bubble_diameter + mm_to_points(4.0),
        question_column_spacing=mm_to_points(14.0),
        question_column_spacing_min=mm_to_points(6.0),
        option_step=bubble_diameter + mm_to_points(4.0),
        option_label_gap=mm_to_points(2.5),
        id_column_step=bubble_diameter + mm_to_points(6.0),
        id_vertical_step=bubble_diameter + mm_to_points(3.0),
        section_gap=mm_to_points(14.0),
        title_block=mm_to_points(18.0),
        student_id_header_gap=mm_to_points(8.0),
        digit_label_gap=mm_to_points(2.5),
        student_id_marker_clearance=mm_to_points(6.0),
        alignment_clearance=mm_to_points(6.0),
    )


def validate_inputs(questions: int, id_length: int) -> None:
    if not (QUESTIONS_RANGE[0] <= questions <= QUESTIONS_RANGE[1]):
        raise ValueError(f"questions must be between {QUESTIONS_RANGE[0]} and {QUESTIONS_RANGE[1]}")
    if not (ID_LENGTH_RANGE[0] <= id_length <= ID_LENGTH_RANGE[1]):
        raise ValueError(f"id-length must be between {ID_LENGTH_RANGE[0]} and {ID_LENGTH_RANGE[1]}")


def generate_layout(
    questions: int, id_length: int, paper_key: str, id_orientation: str, settings: LayoutSettings
) -> Dict[str, Any]:
    paper_name = paper_key.upper()
    if paper_name not in PAPER_SIZES:
        allowed = ", ".join(PAPER_SIZES.keys())
        raise ValueError(f"paper-size must be one of: {allowed}")
    orientation = id_orientation.lower()
    if orientation not in ID_ORIENTATIONS:
        allowed = ", ".join(ID_ORIENTATIONS)
        raise ValueError(f"id-orientation must be one of: {allowed}")

    width, height = PAPER_SIZES[paper_name]
    markers = build_alignment_markers(width, height, settings)
    id_top_y = height - settings.margin - settings.title_block

    safe_left, safe_right = compute_horizontal_safe_area(
        markers=markers,
        page_width=width,
        margin=settings.margin,
        clearance=settings.alignment_clearance,
    )
    usable_width = safe_right - safe_left
    if usable_width <= 0:
        raise ValueError("Unable to place content horizontally; decrease alignment clearance or adjust paper size.")

    clearance_limit = compute_student_id_clearance(markers, height, settings.student_id_marker_clearance)
    if clearance_limit is not None:
        max_center = clearance_limit - (settings.bubble_radius + settings.student_id_header_gap)
        id_top_y = min(id_top_y, max_center)

    student_id_label_y = id_top_y + settings.bubble_radius + settings.student_id_header_gap

    student_id_layout, id_bottom_center_y = build_student_id_layout(
        id_length=id_length,
        area_left=safe_left,
        usable_width=usable_width,
        top_center_y=id_top_y,
        orientation=orientation,
        settings=settings,
    )

    question_area_top = id_bottom_center_y - settings.bubble_radius - settings.section_gap

    question_layout, question_layout_meta = build_question_layout(
        questions=questions,
        area_top=question_area_top,
        start_x=safe_left,
        available_width=usable_width,
        settings=settings,
    )

    return {
        "paper_size": paper_name,
        "dimensions": {"width": width, "height": height},
        "questions": question_layout,
        "student_id": student_id_layout,
        "alignment_markers": markers,
        "metadata": {
            "num_questions": questions,
            "id_length": id_length,
            "student_id_orientation": orientation,
            "bubble_radius": settings.bubble_radius,
            "bubble_diameter": settings.bubble_radius * 2,
            "question_row_height": settings.question_row_height,
            "option_step": settings.option_step,
            "option_label_gap": settings.option_label_gap,
            "id_vertical_step": settings.id_vertical_step,
            "id_column_step": settings.id_column_step,
            "question_column_spacing": question_layout_meta["column_spacing"],
            "question_columns": question_layout_meta["columns"],
            "question_rows": question_layout_meta["rows"],
            "student_id_header_gap": settings.student_id_header_gap,
            "digit_label_gap": settings.digit_label_gap,
            "content_left": safe_left,
            "content_right": safe_right,
            "margin": settings.margin,
            "question_area_top": question_area_top,
            "student_id_label_y": student_id_label_y,
        },
    }


def build_student_id_layout(
    id_length: int,
    area_left: float,
    usable_width: float,
    top_center_y: float,
    orientation: str,
    settings: LayoutSettings,
) -> Tuple[List[Dict[str, Any]], float]:
    if orientation == "horizontal":
        return build_student_id_layout_horizontal(id_length, area_left, usable_width, top_center_y, settings)
    return build_student_id_layout_vertical(id_length, area_left, usable_width, top_center_y, settings)


def build_student_id_layout_vertical(
    id_length: int,
    area_left: float,
    usable_width: float,
    top_center_y: float,
    settings: LayoutSettings,
) -> Tuple[List[Dict[str, Any]], float]:
    columns: List[Dict[str, Any]] = []

    total_width = (id_length - 1) * settings.id_column_step + (2 * settings.bubble_radius)
    if total_width > usable_width:
        raise ValueError("Student ID section does not fit horizontally. Reduce id-length.")
    start_x = area_left + (usable_width - total_width) / 2.0

    for digit_index in range(id_length):
        column_center_x = start_x + settings.bubble_radius + digit_index * settings.id_column_step
        label_y = top_center_y + settings.bubble_radius + settings.digit_label_gap
        column = {
            "digit_index": digit_index + 1,
            "label_position": {"x": column_center_x - settings.bubble_radius, "y": label_y},
            "bubbles": [],
        }
        for value in range(10):
            center_y = top_center_y - (value * settings.id_vertical_step)
            column["bubbles"].append(
                {"value": str(value), "x": column_center_x, "y": center_y, "radius": settings.bubble_radius}
            )
        columns.append(column)

    bottom_center_y = top_center_y - (9 * settings.id_vertical_step)
    return columns, bottom_center_y


def build_student_id_layout_horizontal(
    id_length: int,
    area_left: float,
    usable_width: float,
    top_center_y: float,
    settings: LayoutSettings,
) -> Tuple[List[Dict[str, Any]], float]:
    columns: List[Dict[str, Any]] = []
    num_values = 10
    total_width = (num_values - 1) * settings.id_column_step + (2 * settings.bubble_radius)
    if total_width > usable_width:
        raise ValueError("Student ID section does not fit horizontally. Reduce id-length or paper size.")

    start_x = area_left + (usable_width - total_width) / 2.0
    label_x_offset = settings.question_label_width / 2.0 + settings.option_label_gap

    for digit_index in range(id_length):
        center_y = top_center_y - digit_index * settings.id_vertical_step
        label_x = start_x - label_x_offset
        column = {
            "digit_index": digit_index + 1,
            "label_position": {
                "x": label_x,
                "y": center_y - settings.bubble_radius / 2.0,
            },
            "bubbles": [],
        }
        for value_index in range(num_values):
            center_x = start_x + settings.bubble_radius + value_index * settings.id_column_step
            column["bubbles"].append(
                {
                    "value": str(value_index),
                    "x": center_x,
                    "y": center_y,
                    "radius": settings.bubble_radius,
                }
            )
        columns.append(column)

    bottom_center_y = top_center_y - (max(0, id_length - 1) * settings.id_vertical_step)
    return columns, bottom_center_y


def build_question_layout(
    questions: int,
    area_top: float,
    start_x: float,
    available_width: float,
    settings: LayoutSettings,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    question_block_width = settings.question_label_width + (2 * settings.bubble_radius) + settings.option_step * (
        len(OPTIONS) - 1
    )
    available_height = area_top - (settings.margin + settings.bubble_radius)
    if available_height <= settings.question_row_height:
        raise ValueError("Not enough vertical space for the questions section. Try reducing id-length.")

    max_rows_by_height = max(1, int(available_height // settings.question_row_height))

    def spacing_for_columns(column_count: int) -> Optional[float]:
        if column_count == 1:
            return 0.0 if question_block_width <= available_width else None
        total_block_width = column_count * question_block_width
        if total_block_width > available_width:
            return None
        max_spacing_allowed = (available_width - total_block_width) / (column_count - 1)
        if max_spacing_allowed < settings.question_column_spacing_min:
            return None
        return min(settings.question_column_spacing, max_spacing_allowed)

    max_columns_by_width = int(
        (available_width + settings.question_column_spacing_min)
        // (question_block_width + settings.question_column_spacing_min)
    )
    max_columns_by_width = max(1, max_columns_by_width)
    max_columns = min(questions, max_columns_by_width)

    chosen_columns = None
    chosen_rows = None
    column_spacing = None
    for column_count in range(max_columns, 0, -1):
        rows_needed = math.ceil(questions / column_count)
        spacing = spacing_for_columns(column_count)
        if spacing is None:
            continue
        if rows_needed <= max_rows_by_height:
            chosen_columns = column_count
            chosen_rows = rows_needed
            column_spacing = spacing
            break

    if chosen_columns is None or chosen_rows is None or column_spacing is None:
        raise ValueError(
            "Question grid cannot fit on the selected paper size with the current parameters. "
            "Try reducing questions or student ID length."
        )

    layout: List[Dict[str, Any]] = []
    question_number = 1

    for column_index in range(chosen_columns):
        column_x = start_x + column_index * (question_block_width + column_spacing)
        for row_index in range(chosen_rows):
            if question_number > questions:
                break
            center_y = area_top - row_index * settings.question_row_height
            label_position = {"x": column_x, "y": center_y}
            first_bubble_x = column_x + settings.question_label_width + settings.bubble_radius
            bubbles = []
            for option_index, option in enumerate(OPTIONS):
                bubble_x = first_bubble_x + option_index * settings.option_step
                bubbles.append({"option": option, "x": bubble_x, "y": center_y, "radius": settings.bubble_radius})

            layout.append({"number": question_number, "label_position": label_position, "bubbles": bubbles})
            question_number += 1

    return layout, {"columns": chosen_columns, "rows": chosen_rows, "column_spacing": column_spacing}


def build_alignment_markers(width: float, height: float, settings: LayoutSettings) -> List[Dict[str, float]]:
    size = mm_to_points(12.0)
    offset = settings.margin / 2
    return [
        {"type": "square", "x": offset, "y": offset, "size": size},
        {"type": "square", "x": width - offset - size, "y": offset, "size": size},
        {"type": "square", "x": offset, "y": height - offset - size, "size": size},
        {"type": "square", "x": width - offset - size, "y": height - offset - size, "size": size},
    ]


def compute_student_id_clearance(
    markers: List[Dict[str, float]], page_height: float, clearance: float
) -> Optional[float]:
    if not markers:
        return None
    halfway = page_height / 2.0
    limits: List[float] = []
    for marker in markers:
        y = marker.get("y", 0.0)
        size = marker.get("size", 0.0)
        marker_is_top = (y + size / 2.0) >= halfway
        if marker_is_top:
            limits.append(y - clearance)
    if not limits:
        return None
    return min(limits)


def compute_horizontal_safe_area(
    markers: List[Dict[str, float]],
    page_width: float,
    margin: float,
    clearance: float,
) -> Tuple[float, float]:
    left = margin
    right = page_width - margin
    if not markers:
        return left, right
    halfway = page_width / 2.0
    for marker in markers:
        x = marker.get("x", 0.0)
        size = marker.get("size", 0.0)
        center_x = x + size / 2.0
        if center_x <= halfway:
            left = max(left, x + size + clearance)
        else:
            right = min(right, x - clearance)
    if right <= left:
        return margin, page_width - margin
    return left, right


def render_pdf_to_bytes(
    layout: Dict[str, Any], draw_border: bool = True, title: Optional[str] = None
) -> bytes:
    """Render layout to PDF bytes (for database storage)."""
    buffer = io.BytesIO()

    width = layout["dimensions"]["width"]
    height = layout["dimensions"]["height"]
    metadata = layout["metadata"]
    c = canvas.Canvas(buffer, pagesize=(width, height))
    margin = metadata["margin"]
    bubble_radius = metadata["bubble_radius"]
    option_label_gap = metadata.get("option_label_gap", mm_to_points(2.5))

    title_text = title.strip() if title else ""
    if title_text:
        c.setFont("Helvetica-Bold", 16)
        title_y = height - margin / 2 - mm_to_points(1.0)
        c.drawCentredString(width / 2.0, title_y, title_text)

    c.setFont("Helvetica-Bold", 12)
    student_id_columns = layout["student_id"]
    student_label_x = margin
    if student_id_columns:
        student_label_x = min(col["label_position"]["x"] for col in student_id_columns)
    c.drawString(student_label_x, metadata["student_id_label_y"], "Student ID")

    c.setFont("Helvetica", 10)

    for column in layout["student_id"]:
        label = f"ID {column['digit_index']}"
        label_pos = column["label_position"]
        c.drawString(label_pos["x"], label_pos["y"], label)
        for bubble in column["bubbles"]:
            c.circle(bubble["x"], bubble["y"], bubble_radius)
            c.drawString(bubble["x"] + bubble_radius + 2, bubble["y"] - 3, bubble["value"])

    for question in layout["questions"]:
        label_text = f"Q{question['number']:02d}"
        label_pos = question["label_position"]
        c.drawString(label_pos["x"], label_pos["y"], label_text)
        for bubble in question["bubbles"]:
            c.circle(bubble["x"], bubble["y"], bubble_radius)
            c.setFont("Helvetica", 8)
            c.drawCentredString(bubble["x"], bubble["y"] - bubble_radius - option_label_gap, bubble["option"])
            c.setFont("Helvetica", 10)

    for marker in layout["alignment_markers"]:
        c.setStrokeColor(colors.black)
        c.setFillColor(colors.black)
        c.rect(marker["x"], marker["y"], marker["size"], marker["size"], stroke=0, fill=1)
    if draw_border:
        c.setFillColor(colors.black)
        c.setLineWidth(4)
        border_offset = metadata["margin"] / 2
        c.rect(
            border_offset,
            border_offset,
            width - border_offset * 2,
            height - border_offset * 2,
            stroke=1,
            fill=0,
        )
        c.setLineWidth(1)

    c.showPage()
    c.save()

    return buffer.getvalue()


class BubbleSheetGenerator:
    """High-level generator class for bubble sheets."""

    def __init__(self):
        self.settings = build_layout_settings()

    def generate(
        self,
        num_questions: int,
        paper_size: str = "A4",
        id_length: int = DEFAULT_ID_LENGTH,
        id_orientation: str = "vertical",
        draw_border: bool = False,
        title: Optional[str] = None,
    ) -> Tuple[bytes, Dict[str, Any]]:
        """
        Generate a bubble sheet PDF and layout.

        Args:
            num_questions: Number of questions (1-50)
            paper_size: Paper size ("A4" or "LETTER")
            id_length: Number of digits in student ID (4-10)
            id_orientation: "vertical" or "horizontal"
            draw_border: Whether to draw outer border
            title: Optional title text for the sheet

        Returns:
            Tuple of (pdf_bytes, layout_dict)
        """
        validate_inputs(num_questions, id_length)

        layout = generate_layout(
            questions=num_questions,
            id_length=id_length,
            paper_key=paper_size,
            id_orientation=id_orientation,
            settings=self.settings,
        )

        pdf_bytes = render_pdf_to_bytes(
            layout=layout,
            draw_border=draw_border,
            title=title,
        )

        return pdf_bytes, layout
