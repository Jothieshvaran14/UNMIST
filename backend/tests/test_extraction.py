"""Extraction and passage-splitting tests."""

from __future__ import annotations

import pytest
from app.errors import UpstreamError
from app.services.knowledge.extraction import extract_text, split_passages

HTML = b"""<html><head><title>Test</title>
<script>var ignored = 1;</script>
<style>.x{}</style></head>
<body>
<h1>Python Decorators</h1>
<p>A decorator is a function that modifies another function.
Decorators require an understanding of first-class functions.</p>
<p>First-class functions are functions that can be passed as arguments and returned as values.</p>
</body></html>"""


def test_html_extraction_strips_scripts_and_styles():
    text = extract_text(HTML, "text/html")
    assert "decorator" in text.lower()
    assert "ignored" not in text
    assert ".x{}" not in text


def test_html_extraction_respects_charset():
    content = "<html><body><p>café</p></body></html>".encode("latin-1")
    text = extract_text(content, "text/html", "latin-1")
    assert "café" in text


def test_unsupported_media_type_is_rejected():
    with pytest.raises(UpstreamError):
        extract_text(b"data", "application/zip")


def test_passage_splitting_respects_limits():
    text = "Short sentence one. " * 5 + "\n\n" + "Another paragraph here."
    passages = split_passages(text)
    assert passages
    assert all(len(p) <= 1200 for p in passages)


def test_passage_splitting_deduplicates():
    text = "Duplicate paragraph.\n\nDuplicate paragraph."
    passages = split_passages(text, min_chars=5)
    assert len(passages) == 1


def test_empty_text_produces_no_passages():
    assert split_passages("") == []
