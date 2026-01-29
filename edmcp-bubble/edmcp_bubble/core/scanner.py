"""
Bubble Sheet Scanner

Processes scanned images of bubble sheets using computer vision.
Adapted from bubblexan/scan_bubblesheet.py for integration with edmcp-bubble.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

import cv2
import numpy as np


@dataclass
class BubbleDef:
    """Definition of a single bubble on the sheet."""

    label: str
    x: float
    y: float
    radius: float


@dataclass
class QuestionDef:
    """Definition of a question with its bubble options."""

    number: int
    bubbles: List[BubbleDef]


@dataclass
class StudentIDColumn:
    """Definition of a single digit column in student ID area."""

    digit_index: int
    bubbles: List[BubbleDef]


@dataclass
class LayoutGuide:
    """Complete layout specification for a bubble sheet."""

    width: float
    height: float
    questions: List[QuestionDef]
    student_id_columns: List[StudentIDColumn]
    alignment_markers: List[Dict[str, float]]
    metadata: Dict[str, Any]


@dataclass
class ScanResult:
    """Result of scanning a single bubble sheet image."""

    page_number: int
    student_id: str
    answers: Dict[int, str]
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "page_number": self.page_number,
            "student_id": self.student_id,
            "answers": {str(k): v for k, v in self.answers.items()},
            "warnings": self.warnings,
        }


def load_layout_guide(layout_dict: Dict[str, Any]) -> LayoutGuide:
    """
    Convert stored layout JSON to LayoutGuide dataclass.

    Args:
        layout_dict: Layout dictionary from bubble_sheets.layout_json

    Returns:
        LayoutGuide instance
    """
    questions = [
        QuestionDef(
            number=item["number"],
            bubbles=[
                BubbleDef(
                    label=opt["option"], x=opt["x"], y=opt["y"], radius=opt["radius"]
                )
                for opt in item["bubbles"]
            ],
        )
        for item in layout_dict["questions"]
    ]

    student_columns = [
        StudentIDColumn(
            digit_index=col.get("digit_index") or col.get("digit"),
            bubbles=[
                BubbleDef(
                    label=b["value"], x=b["x"], y=b["y"], radius=b["radius"]
                )
                for b in col["bubbles"]
            ],
        )
        for col in layout_dict["student_id"]
    ]

    markers: List[Dict[str, float]] = []
    for marker in layout_dict.get("alignment_markers", []):
        markers.append(
            {
                "x": float(marker["x"]),
                "y": float(marker["y"]),
                "size": float(marker.get("size", 0)),
                "type": marker.get("type", "square"),
            }
        )

    dimensions = layout_dict["dimensions"]
    metadata = layout_dict.get("metadata", {})

    return LayoutGuide(
        width=float(dimensions["width"]),
        height=float(dimensions["height"]),
        questions=questions,
        student_id_columns=student_columns,
        alignment_markers=markers,
        metadata=metadata,
    )


def _to_top_left_coords(x: float, y: float, layout_height: float) -> Tuple[float, float]:
    """Convert from bottom-left origin (PDF) to top-left origin (image)."""
    return float(x), float(layout_height - y)


def _order_points_clockwise(
    points: Sequence[Tuple[float, float]]
) -> List[Tuple[float, float]]:
    """Order points in clockwise order starting from top-left."""
    pts = np.array(points, dtype=np.float32)
    rect = np.zeros((4, 2), dtype=np.float32)
    s = pts.sum(axis=1)
    rect[0] = pts[np.argmin(s)]  # top-left
    rect[2] = pts[np.argmax(s)]  # bottom-right
    diff = np.diff(pts, axis=1)
    rect[1] = pts[np.argmin(diff)]  # top-right
    rect[3] = pts[np.argmax(diff)]  # bottom-left
    return rect.tolist()


def _detect_alignment_markers(
    gray: np.ndarray, max_markers: int = 4
) -> List[Tuple[float, float]]:
    """Detect square alignment markers in the image."""
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    _, thresh = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    candidates: List[Tuple[float, float, float]] = []
    for contour in contours:
        area = cv2.contourArea(contour)
        if area < 200:
            continue
        perimeter = cv2.arcLength(contour, True)
        approx = cv2.approxPolyDP(contour, 0.04 * perimeter, True)
        if len(approx) != 4:
            continue
        rect = cv2.minAreaRect(contour)
        width, height = rect[1]
        if min(width, height) == 0:
            continue
        aspect_ratio = max(width, height) / min(width, height)
        if aspect_ratio > 1.3:
            continue
        moments = cv2.moments(contour)
        if moments["m00"] == 0:
            continue
        c_x = moments["m10"] / moments["m00"]
        c_y = moments["m01"] / moments["m00"]
        candidates.append((area, c_x, c_y))

    candidates.sort(reverse=True, key=lambda item: item[0])
    return [(c[1], c[2]) for c in candidates[:max_markers]]


def _detect_guided_alignment_markers(
    gray: np.ndarray,
    layout: LayoutGuide,
    window_radius: int,
    max_markers: int = 4,
) -> List[Tuple[float, float]]:
    """Detect alignment markers using layout guide for approximate positions."""
    image_h, image_w = gray.shape[:2]
    scale_x = image_w / layout.width
    scale_y = image_h / layout.height

    guided: List[Tuple[float, float, float]] = []
    for marker in layout.alignment_markers[:max_markers]:
        size = marker.get("size", 0.0)
        center_x = marker["x"] + size / 2.0
        center_y = marker["y"] + size / 2.0
        approx_x = center_x * scale_x
        approx_y = (layout.height - center_y) * scale_y

        x1 = max(0, int(round(approx_x - window_radius)))
        y1 = max(0, int(round(approx_y - window_radius)))
        x2 = min(image_w, int(round(approx_x + window_radius)))
        y2 = min(image_h, int(round(approx_y + window_radius)))

        if x2 <= x1 or y2 <= y1:
            continue

        roi = gray[y1:y2, x1:x2]
        blur = cv2.GaussianBlur(roi, (5, 5), 0)
        _, thresh = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        contours, _ = cv2.findContours(
            thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )

        best_area = 0.0
        best_center: Optional[Tuple[float, float]] = None
        for contour in contours:
            area = cv2.contourArea(contour)
            if area < 50:
                continue
            M = cv2.moments(contour)
            if M["m00"] == 0:
                continue
            cx = M["m10"] / M["m00"] + x1
            cy = M["m01"] / M["m00"] + y1
            if area > best_area:
                best_area = area
                best_center = (cx, cy)

        if best_center is not None:
            guided.append((best_area, best_center[0], best_center[1]))

    guided.sort(reverse=True, key=lambda item: item[0])
    return [(c[1], c[2]) for c in guided[:max_markers]]


def _detect_page_corners(gray: np.ndarray) -> Optional[List[Tuple[float, float]]]:
    """Detect the page/border corners for alignment."""
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    edged = cv2.Canny(blur, 50, 150)
    edged = cv2.dilate(edged, None, iterations=2)
    edged = cv2.erode(edged, None, iterations=1)
    contours, _ = cv2.findContours(edged, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    contours = sorted(contours, key=cv2.contourArea, reverse=True)

    for contour in contours:
        area = cv2.contourArea(contour)
        if area < 1000:
            continue
        peri = cv2.arcLength(contour, True)
        approx = cv2.approxPolyDP(contour, 0.02 * peri, True)
        if len(approx) == 4:
            return [(float(pt[0][0]), float(pt[0][1])) for pt in approx]
    return None


def _build_layout_to_image_transform(
    layout: LayoutGuide, gray_image: np.ndarray
) -> Tuple[np.ndarray, List[str]]:
    """Build perspective transform matrix from layout coords to image coords."""
    warnings: List[str] = []
    image_h, image_w = gray_image.shape[:2]
    layout_markers = layout.alignment_markers[:4]
    detected_markers = _detect_alignment_markers(gray_image) if layout_markers else []

    guided_used = False
    if len(layout_markers) == 4 and len(detected_markers) < 4:
        window = int(max(image_w, image_h) * 0.12)
        guided = _detect_guided_alignment_markers(gray_image, layout, window)
        if len(guided) == 4:
            detected_markers = guided
            guided_used = True

    use_alignment = False
    matrix: Optional[np.ndarray] = None
    page_corners: Optional[List[Tuple[float, float]]] = None

    if len(layout_markers) == 4 and len(detected_markers) == 4:
        layout_points = []
        for marker in layout_markers:
            size = marker.get("size", 0.0)
            center_x = marker["x"] + size / 2.0
            center_y = marker["y"] + size / 2.0
            layout_points.append(
                _to_top_left_coords(center_x, center_y, layout.height)
            )

        layout_points_ordered = _order_points_clockwise(layout_points)
        detected_ordered = _order_points_clockwise(detected_markers)
        detected_arr = np.array(detected_ordered, dtype=np.float32)

        span_w = float(detected_arr[:, 0].max() - detected_arr[:, 0].min())
        span_h = float(detected_arr[:, 1].max() - detected_arr[:, 1].min())
        min_span_w = image_w * 0.25
        min_span_h = image_h * 0.25

        if span_w >= min_span_w and span_h >= min_span_h:
            matrix = cv2.getPerspectiveTransform(
                np.array(layout_points_ordered, dtype=np.float32),
                detected_arr,
            )
            use_alignment = True
            if guided_used:
                warnings.append("Alignment markers recovered via guided search.")
        else:
            warnings.append(
                "Detected markers cover too small an area; using proportional mapping."
            )

    if not use_alignment or matrix is None:
        page_corners = _detect_page_corners(gray_image)
        if page_corners is not None:
            page_ordered = _order_points_clockwise(page_corners)
            layout_page_corners = _order_points_clockwise(
                [
                    _to_top_left_coords(0, layout.height, layout.height),
                    _to_top_left_coords(layout.width, layout.height, layout.height),
                    _to_top_left_coords(layout.width, 0, layout.height),
                    _to_top_left_coords(0, 0, layout.height),
                ]
            )

            use_border_frame = False
            border_offset = float(layout.metadata.get("margin", 0.0)) / 2.0
            if border_offset > 0:
                xs = [pt[0] for pt in page_ordered]
                ys = [pt[1] for pt in page_ordered]
                inset_left = max(0.0, min(xs))
                inset_right = max(0.0, image_w - max(xs))
                inset_top = max(0.0, min(ys))
                inset_bottom = max(0.0, image_h - max(ys))
                sum_inset_x = inset_left + inset_right
                sum_inset_y = inset_top + inset_bottom
                sum_ratio_x = sum_inset_x / max(1.0, image_w)
                sum_ratio_y = sum_inset_y / max(1.0, image_h)
                expected_sum_ratio_x = (2.0 * border_offset) / layout.width
                expected_sum_ratio_y = (2.0 * border_offset) / layout.height
                tol_x = max(0.02, expected_sum_ratio_x * 0.6)
                tol_y = max(0.02, expected_sum_ratio_y * 0.6)
                close_x = abs(sum_ratio_x - expected_sum_ratio_x) <= tol_x
                close_y = abs(sum_ratio_y - expected_sum_ratio_y) <= tol_y
                if close_x and close_y:
                    use_border_frame = True

            if use_border_frame:
                layout_border_corners = _order_points_clockwise(
                    [
                        _to_top_left_coords(
                            border_offset, layout.height - border_offset, layout.height
                        ),
                        _to_top_left_coords(
                            layout.width - border_offset,
                            layout.height - border_offset,
                            layout.height,
                        ),
                        _to_top_left_coords(
                            layout.width - border_offset, border_offset, layout.height
                        ),
                        _to_top_left_coords(border_offset, border_offset, layout.height),
                    ]
                )
                chosen_layout = layout_border_corners
                warnings.append("Detected inner border frame for alignment.")
            else:
                chosen_layout = layout_page_corners
                warnings.append("Using page border for alignment.")

            matrix = cv2.getPerspectiveTransform(
                np.array(chosen_layout, dtype=np.float32),
                np.array(page_ordered, dtype=np.float32),
            )
            use_alignment = True

    if not use_alignment or matrix is None:
        if len(layout_markers) < 4 and page_corners is None:
            warnings.append("Insufficient layout markers; using proportional mapping.")
        elif len(detected_markers) < 4 and page_corners is None:
            warnings.append(
                "Failed to detect alignment markers; using proportional mapping."
            )
        scale_x = image_w / layout.width
        scale_y = image_h / layout.height
        matrix = np.array(
            [
                [scale_x, 0, 0],
                [0, scale_y, 0],
                [0, 0, 1],
            ],
            dtype=np.float32,
        )

    return matrix, warnings


def _transform_points(
    matrix: np.ndarray, layout_height: float, points: Sequence[Tuple[float, float]]
) -> np.ndarray:
    """Transform points from layout coordinates to image coordinates."""
    layout_coords = np.array(
        [[[x, layout_height - y]] for (x, y) in points],
        dtype=np.float32,
    )
    transformed = cv2.perspectiveTransform(layout_coords, matrix)
    return transformed.reshape(-1, 2)


def _measure_bubble_fill(
    gray: np.ndarray, center: Tuple[float, float], radius: float
) -> float:
    """Measure how filled a bubble is (0.0 = empty, 1.0 = completely filled)."""
    h, w = gray.shape[:2]
    mask = np.zeros((h, w), dtype=np.uint8)
    c_x = int(round(center[0]))
    c_y = int(round(center[1]))
    r = max(1, int(round(radius)))

    if c_x + r < 0 or c_x - r > w or c_y + r < 0 or c_y - r > h:
        return 0.0

    cv2.circle(mask, (c_x, c_y), r, 255, -1)
    pixels = gray[mask == 255]
    if pixels.size == 0:
        return 0.0

    return 1.0 - float(pixels.mean()) / 255.0


def _estimate_pixel_radius(
    matrix: np.ndarray, layout_height: float, bubble: BubbleDef
) -> float:
    """Estimate the pixel radius for a bubble after transformation."""
    center = _transform_points(matrix, layout_height, [(bubble.x, bubble.y)])[0]
    sample_points = [
        (bubble.x + bubble.radius, bubble.y),
        (bubble.x, bubble.y + bubble.radius),
    ]
    transformed = _transform_points(matrix, layout_height, sample_points)
    radii = [np.linalg.norm(transformed[i] - center) for i in range(len(transformed))]
    avg_radius = float(np.mean(radii)) if radii else bubble.radius
    return max(1.0, avg_radius)


def _scan_student_id(
    gray: np.ndarray,
    layout: LayoutGuide,
    matrix: np.ndarray,
    threshold: float,
    relative_threshold: float,
    min_darkness: float = 0.08,
) -> Tuple[str, List[str]]:
    """Decode student ID from bubble sheet."""
    warnings: List[str] = []
    digits: List[str] = []

    for column in sorted(layout.student_id_columns, key=lambda c: c.digit_index):
        column_scores: List[Tuple[str, float]] = []
        for bubble in column.bubbles:
            center = _transform_points(matrix, layout.height, [(bubble.x, bubble.y)])[0]
            radius = _estimate_pixel_radius(matrix, layout.height, bubble)
            score = _measure_bubble_fill(gray, center, radius)
            column_scores.append((bubble.label, score))

        hits = [label for (label, score) in column_scores if score >= threshold]
        if len(hits) == 1:
            digits.append(hits[0])
            continue

        best_label, best_score = max(column_scores, key=lambda item: item[1])
        rival_score = (
            sorted([score for _, score in column_scores], reverse=True)[1]
            if len(column_scores) > 1
            else 0.0
        )

        if best_score < min_darkness:
            warnings.append(
                f"Digit {column.digit_index}: no bubble above threshold and best mark "
                f"too light (best {best_label}={best_score:.2f})."
            )
            digits.append("?")
            continue

        if rival_score >= best_score * relative_threshold:
            warnings.append(
                f"Digit {column.digit_index}: ambiguous fill "
                f"({best_label}={best_score:.2f}, next={rival_score:.2f})."
            )
            digits.append("?")
            continue

        digits.append(best_label)

    student_id = "".join(digits)
    if "?" in student_id or not student_id:
        warnings.append("Student ID unresolved.")
        student_id = "ERROR"

    return student_id, warnings


def _scan_answers(
    gray: np.ndarray,
    layout: LayoutGuide,
    matrix: np.ndarray,
    threshold: float,
    relative_threshold: float,
    min_darkness: float = 0.08,
) -> Tuple[Dict[int, str], List[str]]:
    """Decode answers from bubble sheet."""
    answers: Dict[int, str] = {}
    warnings: List[str] = []

    for question in layout.questions:
        option_scores: List[Tuple[str, float]] = []
        for bubble in question.bubbles:
            center = _transform_points(matrix, layout.height, [(bubble.x, bubble.y)])[0]
            radius = _estimate_pixel_radius(matrix, layout.height, bubble)
            score = _measure_bubble_fill(gray, center, radius)
            option_scores.append((bubble.label, score))

        selections = [label for (label, score) in option_scores if score >= threshold]
        if selections:
            answers[question.number] = ",".join(sorted(selections))
            continue

        best_label, best_score = max(option_scores, key=lambda item: item[1])
        if best_score < min_darkness:
            warnings.append(
                f"Question {question.number}: no selection above threshold and best "
                f"mark too light (best {best_label}={best_score:.2f})."
            )
            answers[question.number] = ""
            continue

        cutoff = max(threshold * 0.5, best_score * relative_threshold)
        fallback = [label for (label, score) in option_scores if score >= cutoff]
        if not fallback:
            fallback = [best_label]

        answers[question.number] = ",".join(sorted(fallback))
        warnings.append(
            f"Question {question.number}: using relative threshold fallback "
            f"(best {best_label}={best_score:.2f})."
        )

    return answers, warnings


class BubbleSheetScanner:
    """Scanner for processing bubble sheet images."""

    def __init__(
        self,
        layout: Dict[str, Any],
        threshold: float = 0.35,
        relative_threshold: float = 0.6,
    ):
        """
        Initialize scanner with layout configuration.

        Args:
            layout: Layout dictionary from bubble_sheets.layout_json
            threshold: Absolute fill ratio threshold (0-1) for accepting a bubble
            relative_threshold: Fallback ratio for selecting when no absolute match
        """
        self.layout_guide = load_layout_guide(layout)
        self.threshold = threshold
        self.relative_threshold = relative_threshold

    def scan_image(self, page_number: int, image: np.ndarray) -> ScanResult:
        """
        Scan a single bubble sheet image.

        Args:
            page_number: Page number (1-indexed) for tracking
            image: OpenCV BGR image

        Returns:
            ScanResult with student ID, answers, and any warnings
        """
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        matrix, transform_warnings = _build_layout_to_image_transform(
            self.layout_guide, gray
        )

        student_id, id_warnings = _scan_student_id(
            gray, self.layout_guide, matrix, self.threshold, self.relative_threshold
        )
        answers, answer_warnings = _scan_answers(
            gray, self.layout_guide, matrix, self.threshold, self.relative_threshold
        )

        all_warnings = transform_warnings + id_warnings + answer_warnings

        return ScanResult(
            page_number=page_number,
            student_id=student_id,
            answers=answers,
            warnings=all_warnings,
        )

    def get_question_numbers(self) -> List[int]:
        """Get sorted list of question numbers from layout."""
        return sorted(q.number for q in self.layout_guide.questions)
