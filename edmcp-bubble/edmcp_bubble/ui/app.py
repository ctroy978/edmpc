"""
Gradio UI for Bubble Test Management.

Provides a web interface for creating bubble tests, generating sheets,
managing answer keys, and downloading artifacts.
"""

import base64
import json
import tempfile
from pathlib import Path
from typing import Optional, Tuple

import gradio as gr

from edmcp_bubble.core import BubbleTestManager, BubbleSheetGenerator


# Global instances
manager: Optional[BubbleTestManager] = None
generator: Optional[BubbleSheetGenerator] = None


def get_manager() -> BubbleTestManager:
    """Get or create the test manager."""
    global manager
    if manager is None:
        manager = BubbleTestManager()
    return manager


def get_generator() -> BubbleSheetGenerator:
    """Get or create the bubble sheet generator."""
    global generator
    if generator is None:
        generator = BubbleSheetGenerator()
    return generator


# === Create Test Tab ===
def create_test(name: str, description: str) -> str:
    """Create a new bubble test."""
    if not name.strip():
        return "Error: Test name is required"

    mgr = get_manager()
    test_id = mgr.create_test(
        name=name.strip(),
        description=description.strip() or None
    )
    return f"Success! Created test: {test_id}"


# === Generate Sheet Tab ===
def generate_sheet(
    test_id: str,
    num_questions: int,
    paper_size: str,
    id_length: int,
    id_orientation: str,
    draw_border: bool,
) -> Tuple[str, Optional[str]]:
    """Generate a bubble sheet for a test."""
    if not test_id.strip():
        return "Error: Test ID is required", None

    mgr = get_manager()
    gen = get_generator()

    # Verify test exists
    test = mgr.get_test(test_id.strip())
    if not test:
        return f"Error: Test not found: {test_id}", None

    try:
        # Generate sheet
        pdf_bytes, layout = gen.generate(
            num_questions=int(num_questions),
            paper_size=paper_size,
            id_length=int(id_length),
            id_orientation=id_orientation,
            draw_border=draw_border,
            title=test["name"],
        )

        # Store in database
        sheet_id = mgr.store_sheet(
            test_id=test_id.strip(),
            pdf_bytes=pdf_bytes,
            layout=layout,
            num_questions=int(num_questions),
            paper_size=paper_size,
            id_length=int(id_length),
            id_orientation=id_orientation,
            draw_border=draw_border,
        )

        # Save to temp file for preview
        temp_dir = Path(tempfile.gettempdir())
        pdf_path = temp_dir / f"{test_id}_sheet.pdf"
        pdf_path.write_bytes(pdf_bytes)

        return (
            f"Success! Generated sheet (ID: {sheet_id}) with {num_questions} questions",
            str(pdf_path),
        )

    except ValueError as e:
        return f"Error: {str(e)}", None


# === Answer Key Tab ===
def save_answer_key(test_id: str, answers_json: str) -> str:
    """Save answer key for a test."""
    if not test_id.strip():
        return "Error: Test ID is required"

    mgr = get_manager()

    # Verify test exists
    test = mgr.get_test(test_id.strip())
    if not test:
        return f"Error: Test not found: {test_id}"

    try:
        answers = json.loads(answers_json)
        if not isinstance(answers, list):
            return "Error: Answers must be a JSON array"

        # Validate and set defaults
        for i, ans in enumerate(answers):
            if "question" not in ans or "answer" not in ans:
                return f"Error: Answer {i+1} missing 'question' or 'answer' field"
            if "points" not in ans:
                ans["points"] = 1.0

        key_id = mgr.set_answer_key(test_id=test_id.strip(), answers=answers)
        total_points = sum(a.get("points", 1.0) for a in answers)

        return f"Success! Saved answer key (ID: {key_id}) with {len(answers)} questions, {total_points} total points"

    except json.JSONDecodeError as e:
        return f"Error: Invalid JSON - {str(e)}"


def load_answer_key(test_id: str) -> str:
    """Load existing answer key for a test."""
    if not test_id.strip():
        return "[]"

    mgr = get_manager()
    key = mgr.get_answer_key(test_id.strip())
    if not key:
        return "[]"

    return json.dumps(key["answers"], indent=2)


# === Download Tab ===
def download_pdf(test_id: str) -> Optional[str]:
    """Download PDF for a test."""
    if not test_id.strip():
        return None

    mgr = get_manager()
    pdf_bytes = mgr.get_sheet_pdf(test_id.strip())
    if not pdf_bytes:
        return None

    # Save to temp file
    temp_dir = Path(tempfile.gettempdir())
    pdf_path = temp_dir / f"{test_id.strip()}_bubble_sheet.pdf"
    pdf_path.write_bytes(pdf_bytes)
    return str(pdf_path)


def download_layout(test_id: str) -> Optional[str]:
    """Download layout JSON for a test."""
    if not test_id.strip():
        return None

    mgr = get_manager()
    layout = mgr.get_sheet_layout(test_id.strip())
    if not layout:
        return None

    # Save to temp file
    temp_dir = Path(tempfile.gettempdir())
    json_path = temp_dir / f"{test_id.strip()}_layout.json"
    json_path.write_text(json.dumps(layout, indent=2))
    return str(json_path)


# === List Tests ===
def list_tests() -> str:
    """List all bubble tests."""
    mgr = get_manager()
    tests = mgr.list_tests(limit=50)

    if not tests:
        return "No tests found"

    lines = ["| ID | Name | Status | Sheet | Key |", "|---|---|---|---|---|"]
    for test in tests:
        sheet = "Yes" if test["has_sheet"] else "No"
        key = "Yes" if test["has_answer_key"] else "No"
        lines.append(f"| {test['id']} | {test['name']} | {test['status']} | {sheet} | {key} |")

    return "\n".join(lines)


def get_test_info(test_id: str) -> str:
    """Get detailed test info."""
    if not test_id.strip():
        return "Enter a test ID to view details"

    mgr = get_manager()
    test = mgr.get_test(test_id.strip())
    if not test:
        return f"Test not found: {test_id}"

    lines = [
        f"**Test ID:** {test['id']}",
        f"**Name:** {test['name']}",
        f"**Status:** {test['status']}",
        f"**Created:** {test['created_at']}",
        f"**Description:** {test.get('description') or 'N/A'}",
        "",
    ]

    sheet = mgr.get_sheet(test_id.strip())
    if sheet:
        lines.extend([
            "**Sheet Info:**",
            f"- Questions: {sheet['num_questions']}",
            f"- Paper: {sheet['paper_size']}",
            f"- ID Length: {sheet['id_length']}",
            f"- Orientation: {sheet['id_orientation']}",
        ])
    else:
        lines.append("**Sheet:** Not generated")

    key = mgr.get_answer_key(test_id.strip())
    if key:
        lines.extend([
            "",
            "**Answer Key:**",
            f"- Questions: {len(key['answers'])}",
            f"- Total Points: {key['total_points']}",
        ])
    else:
        lines.append("\n**Answer Key:** Not set")

    return "\n".join(lines)


# === Build Gradio Interface ===
def build_ui() -> gr.Blocks:
    """Build the Gradio interface."""

    with gr.Blocks(title="Bubble Test Manager") as app:
        gr.Markdown("# Bubble Test Manager")
        gr.Markdown("Create and manage fill-in-bubble style tests")

        with gr.Tabs():
            # === Create Test Tab ===
            with gr.TabItem("Create Test"):
                gr.Markdown("### Create a New Test")
                with gr.Row():
                    with gr.Column():
                        create_name = gr.Textbox(label="Test Name", placeholder="e.g., Week 5 Quiz")
                        create_desc = gr.Textbox(label="Description (optional)", placeholder="Optional description")
                        create_btn = gr.Button("Create Test", variant="primary")
                    with gr.Column():
                        create_result = gr.Textbox(label="Result", interactive=False)

                create_btn.click(
                    fn=create_test,
                    inputs=[create_name, create_desc],
                    outputs=[create_result],
                )

            # === Generate Sheet Tab ===
            with gr.TabItem("Generate Sheet"):
                gr.Markdown("### Generate Bubble Sheet")
                with gr.Row():
                    with gr.Column():
                        gen_test_id = gr.Textbox(label="Test ID", placeholder="bt_20260125_...")
                        gen_questions = gr.Slider(minimum=1, maximum=50, value=25, step=1, label="Number of Questions")
                        gen_paper = gr.Dropdown(choices=["A4", "LETTER"], value="A4", label="Paper Size")
                        gen_id_len = gr.Slider(minimum=4, maximum=10, value=6, step=1, label="Student ID Length")
                        gen_orient = gr.Dropdown(choices=["vertical", "horizontal"], value="vertical", label="ID Orientation")
                        gen_border = gr.Checkbox(label="Draw Border", value=False)
                        gen_btn = gr.Button("Generate Sheet", variant="primary")
                    with gr.Column():
                        gen_result = gr.Textbox(label="Result", interactive=False)
                        gen_pdf = gr.File(label="Generated PDF")

                gen_btn.click(
                    fn=generate_sheet,
                    inputs=[gen_test_id, gen_questions, gen_paper, gen_id_len, gen_orient, gen_border],
                    outputs=[gen_result, gen_pdf],
                )

            # === Answer Key Tab ===
            with gr.TabItem("Answer Key"):
                gr.Markdown("### Manage Answer Key")
                gr.Markdown("""
                Enter answers as JSON array:
                ```json
                [
                  {"question": "Q1", "answer": "a", "points": 1.0},
                  {"question": "Q2", "answer": "b,c", "points": 2.0}
                ]
                ```
                """)
                with gr.Row():
                    with gr.Column():
                        key_test_id = gr.Textbox(label="Test ID", placeholder="bt_20260125_...")
                        key_load_btn = gr.Button("Load Existing Key")
                        key_json = gr.Code(
                            label="Answer Key JSON",
                            language="json",
                            value='[\n  {"question": "Q1", "answer": "a", "points": 1.0}\n]',
                        )
                        key_save_btn = gr.Button("Save Answer Key", variant="primary")
                    with gr.Column():
                        key_result = gr.Textbox(label="Result", interactive=False)

                key_load_btn.click(
                    fn=load_answer_key,
                    inputs=[key_test_id],
                    outputs=[key_json],
                )
                key_save_btn.click(
                    fn=save_answer_key,
                    inputs=[key_test_id, key_json],
                    outputs=[key_result],
                )

            # === Download Tab ===
            with gr.TabItem("Download"):
                gr.Markdown("### Download Test Files")
                with gr.Row():
                    with gr.Column():
                        dl_test_id = gr.Textbox(label="Test ID", placeholder="bt_20260125_...")
                        dl_pdf_btn = gr.Button("Download PDF")
                        dl_layout_btn = gr.Button("Download Layout JSON")
                    with gr.Column():
                        dl_pdf_file = gr.File(label="PDF File")
                        dl_layout_file = gr.File(label="Layout JSON")

                dl_pdf_btn.click(
                    fn=download_pdf,
                    inputs=[dl_test_id],
                    outputs=[dl_pdf_file],
                )
                dl_layout_btn.click(
                    fn=download_layout,
                    inputs=[dl_test_id],
                    outputs=[dl_layout_file],
                )

            # === List Tests Tab ===
            with gr.TabItem("List Tests"):
                gr.Markdown("### All Bubble Tests")
                list_btn = gr.Button("Refresh List")
                list_output = gr.Markdown()

                gr.Markdown("---")
                gr.Markdown("### Test Details")
                detail_test_id = gr.Textbox(label="Test ID", placeholder="Enter test ID for details")
                detail_btn = gr.Button("Get Details")
                detail_output = gr.Markdown()

                list_btn.click(fn=list_tests, outputs=[list_output])
                detail_btn.click(fn=get_test_info, inputs=[detail_test_id], outputs=[detail_output])

    return app


def main():
    """Run the Gradio app."""
    app = build_ui()
    app.launch()


if __name__ == "__main__":
    main()
