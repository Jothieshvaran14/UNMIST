"""Deterministic text extraction from permitted source documents.

Only HTML and PDF are supported. Extraction is intentionally deterministic so
that it is reproducible and testable. Model-generated extraction is a separate
integration boundary (see :mod:`app.services.knowledge.llm`) and is never
silently substituted for this baseline.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from bs4 import BeautifulSoup

from ...errors import UpstreamError

SUPPORTED_MEDIA_TYPES = {"text/html", "application/pdf", "text/plain"}


@dataclass(frozen=True)
class ExtractedDocument:
    text: str
    passages: list[str]
    media_type: str


_WHITESPACE_RE = re.compile(r"[ \t\f\v]+")
_BLANK_LINES_RE = re.compile(r"\n{3,}")


def normalize_whitespace(text: str) -> str:
    lines = [_WHITESPACE_RE.sub(" ", line).strip() for line in text.splitlines()]
    cleaned = "\n".join(line for line in lines if line)
    return _BLANK_LINES_RE.sub("\n\n", cleaned).strip()


def extract_text(content: bytes, media_type: str, charset: str = "utf-8") -> str:
    base_type = media_type.split(";", 1)[0].strip().lower()
    if base_type == "text/html":
        return _extract_html(content, charset)
    if base_type == "application/pdf":
        return _extract_pdf(content)
    if base_type == "text/plain":
        return content.decode(charset, errors="replace")
    raise UpstreamError(
        f"Unsupported media type '{base_type}' for extraction", code="unsupported_media_type"
    )


def _extract_html(content: bytes, charset: str) -> str:
    # The charset handed in comes from the upstream response (an authoritative
    # Content-Type header), so it is honoured explicitly by decoding here.
    # Relying on BeautifulSoup's ``from_encoding=`` alone made the result
    # depend on whether the optional ``chardet`` library happened to be
    # installed: without it, bs4 fell back to UTF-8 with replacement and
    # mangled non-ASCII text (that is what CI caught). Unknown or mismatched
    # declarations fall back to bs4's own sniffing, which also honours a
    # <meta charset> inside the document.
    markup: str | bytes = content
    if charset:
        try:
            markup = content.decode(charset)
        except (LookupError, UnicodeDecodeError):
            markup = content
    soup = BeautifulSoup(markup, "lxml")
    for element in soup(["script", "style", "noscript", "template"]):
        element.decompose()
    text = soup.get_text(separator="\n")
    return normalize_whitespace(text)


def _extract_pdf(content: bytes) -> str:
    import io

    from PyPDF2 import PdfReader

    reader = PdfReader(io.BytesIO(content))
    pages: list[str] = []
    for page in reader.pages:
        try:
            pages.append(page.extract_text() or "")
        except Exception:  # pragma: no cover - malformed PDF
            continue
    return normalize_whitespace("\n\n".join(pages))


def split_passages(text: str, *, max_chars: int = 900, min_chars: int = 40) -> list[str]:
    """Split extracted text into citable passages.

    Passage boundaries follow paragraph breaks and, for long paragraphs, sentence
    boundaries. Passages are deduplicated by normalized text.
    """
    if not text:
        return []
    passages: list[str] = []
    seen: set[str] = set()

    def add(candidate: str) -> None:
        normalized = " ".join(candidate.split())
        if len(normalized) < min_chars:
            return
        if normalized in seen:
            return
        seen.add(normalized)
        passages.append(normalized)

    for paragraph in text.split("\n\n"):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        if len(paragraph) <= max_chars:
            add(paragraph)
            continue
        # Split long paragraphs into sentence-ish chunks near the limit.
        current = ""
        for sentence in re.split(r"(?<=[.!?])\s+", paragraph):
            if len(current) + len(sentence) + 1 > max_chars and current:
                add(current)
                current = sentence
            else:
                current = f"{current} {sentence}".strip()
        if current:
            add(current)

    return passages
