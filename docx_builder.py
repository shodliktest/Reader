"""
docx_builder.py
----------------
Tayyor savol/variant ro'yxatidan (Streamlit'da tahrirlangan yoki bot orqali
to'g'ridan-to'g'ri kelgan) yakuniy Word (.docx) faylni yasaydi.

Format namunadagi kabi:
1). Savol matni
A) Variant
*B) To'g'ri variant
C) Variant
...
"""

from docx import Document

FKEY_TO_LETTER = ['A', 'B', 'C', 'D', 'E', 'F']


def build_docx(questions, output_path, title="Test Savollari"):
    """questions: [{"question": str, "options": [str, ...], "correct_index": int|None}, ...]
    output_path: yakuniy .docx fayl saqlanadigan yo'l."""
    doc = Document()
    doc.add_heading(title, level=1)

    for i, q in enumerate(questions, start=1):
        question_text = q.get("question", "").strip()
        options = q.get("options", [])
        correct_index = q.get("correct_index")

        if not question_text or not options:
            continue

        doc.add_paragraph(f"{i}). {question_text}")
        for j, option_text in enumerate(options):
            letter = FKEY_TO_LETTER[j] if j < len(FKEY_TO_LETTER) else str(j + 1)
            prefix = f"*{letter})" if correct_index == j else f"{letter})"
            doc.add_paragraph(f"{prefix} {option_text.strip()}")
        doc.add_paragraph()

    doc.save(output_path)
    return output_path
