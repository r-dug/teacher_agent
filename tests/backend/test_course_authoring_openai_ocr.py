"""Tests for chapter decomposition OCR fallback on image-only pages."""

from __future__ import annotations

import fitz
import pytest

import backend.services.agents.decompose as decompose_mod
import backend.services.documents.course_authoring as course_authoring


def _write_image_only_pdf(path) -> None:
    doc = fitz.open()
    try:
        # Blank page has no text layer; mirrors scanned/image-only extraction behavior.
        doc.new_page(width=612, height=792)
        doc.save(path)
    finally:
        doc.close()


def test_decompose_chapter_uses_ocr_fallback_when_no_text(tmp_path, monkeypatch):
    rel = "u/course_sources/image_only.pdf"
    full = tmp_path / rel
    full.parent.mkdir(parents=True, exist_ok=True)
    _write_image_only_pdf(full)

    monkeypatch.setattr(course_authoring.settings, "STORAGE_DIR", tmp_path)
    monkeypatch.setattr(course_authoring.settings, "OPENAI_DECOMPOSE_ENABLE_VISION_OCR", True)

    ocr_called = {"value": False}
    from_text_called = {"value": False}

    def _fake_ocr(**_kwargs):
        ocr_called["value"] = True
        return ["[Page 1]\nScanned textbook content."]

    def _fake_from_text(**kwargs):
        from_text_called["value"] = True
        # Verify the OCR'd text reached the decompose helper.
        assert "Scanned textbook content." in kwargs["text"]
        return None, [
            {
                "title": "OCR Section",
                "content": "Scanned textbook content.",
                "key_concepts": ["kana"],
                "page_start": 1,
                "page_end": 1,
            }
        ]

    monkeypatch.setattr(course_authoring, "_openai_ocr_page_texts_sync", _fake_ocr)
    # B3: course_authoring delegates to decompose_from_text for OCR-extracted text.
    monkeypatch.setattr(decompose_mod, "decompose_from_text", _fake_from_text)

    sections = course_authoring._decompose_chapter_sync(
        source_pdf_rel=rel,
        page_start=1,
        page_end=1,
        total_pages=1,
        objectives_prompt="",
        decompose_provider="openai",
        decompose_model="gpt-4o-mini",
    )

    assert ocr_called["value"] is True
    assert from_text_called["value"] is True
    assert len(sections) == 1
    assert sections[0]["title"] == "OCR Section"


def test_decompose_chapter_raises_without_ocr_fallback(tmp_path, monkeypatch):
    rel = "u/course_sources/image_only.pdf"
    full = tmp_path / rel
    full.parent.mkdir(parents=True, exist_ok=True)
    _write_image_only_pdf(full)

    monkeypatch.setattr(course_authoring.settings, "STORAGE_DIR", tmp_path)
    monkeypatch.setattr(course_authoring.settings, "OPENAI_DECOMPOSE_ENABLE_VISION_OCR", False)

    with pytest.raises(ValueError, match="No extractable text found"):
        course_authoring._decompose_chapter_sync(
            source_pdf_rel=rel,
            page_start=1,
            page_end=1,
            total_pages=1,
            objectives_prompt="",
            decompose_provider="openai",
            decompose_model="gpt-4o-mini",
        )

