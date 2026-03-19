"""Unit tests for section parsing/normalization in course authoring."""

from __future__ import annotations

import pytest

from backend.services.documents.course_authoring import (
    _extract_sections_payload,
    _normalize_sections_or_raise,
)


def test_normalize_sections_accepts_alternate_fields():
    raw = [
        {
            "name": "Japanese Writing System",
            "summary": "Introduce hiragana and katakana foundations.",
            "concepts": "hiragana, katakana, stroke order",
            "pageStart": "28",
            "pageEnd": "35",
        }
    ]
    out = _normalize_sections_or_raise(raw)

    assert len(out) == 1
    assert out[0]["title"] == "Japanese Writing System"
    assert out[0]["content"] == "Introduce hiragana and katakana foundations."
    assert out[0]["key_concepts"] == ["hiragana", "katakana", "stroke order"]
    assert out[0]["page_start"] == 28
    assert out[0]["page_end"] == 35


def test_normalize_sections_synthesizes_content_from_key_concepts():
    raw = [{"title": "Lesson 1", "keyConcepts": ["greetings", "self-introductions"], "page_start": 44}]
    out = _normalize_sections_or_raise(raw)

    assert len(out) == 1
    assert out[0]["title"] == "Lesson 1"
    assert out[0]["content"].startswith("Focus topics:")
    assert out[0]["page_start"] == 44
    assert out[0]["page_end"] == 44


def test_extract_sections_payload_supports_nested_shapes():
    payload = {"result": {"sections": [{"title": "A", "content": "B"}]}}
    sections = _extract_sections_payload(payload)
    assert isinstance(sections, list)
    assert sections[0]["title"] == "A"


def test_extract_sections_payload_supports_single_section_object():
    payload = {"title": "Only Section", "summary": "Core points."}
    sections = _extract_sections_payload(payload)
    assert isinstance(sections, list)
    assert len(sections) == 1
    assert sections[0]["title"] == "Only Section"


def test_normalize_sections_still_raises_for_unusable_payload():
    with pytest.raises(ValueError, match="No teachable sections were produced"):
        _normalize_sections_or_raise([{}, {"foo": "bar"}, 123])
