"""Explicit, versioned text normalization for OCR output.

Policy: normalization is *narrow and reversible-in-spirit*. It fixes whitespace
artifacts that no human reader intends, and nothing else.

Never removed here, because each can change meaning:

  * minus signs, decimal points, mathematical symbols
  * units and superscripts/subscripts expressed in text
  * meaningful punctuation
  * line breaks *within* a region's text (kept, since a region is a line)

The rules are versioned (``NORMALIZATION_VERSION``) and persisted with each
region, so a change in normalization semantics makes every prior result
distinguishable from new ones rather than silently comparable.

``raw_text`` in the database always keeps the provider's verbatim output; this
module only produces the value stored in ``text``.
"""

from __future__ import annotations

import re
import unicodedata

# Bump when the rules below change semantics.
NORMALIZATION_VERSION = "ocr-norm-v1"

# Variants of whitespace that OCR decoders emit and that carry no meaning.
_WS_RUN = re.compile(r"[^\S\n]+")
_TRAILING = re.compile(r"[ \t]+$", re.MULTILINE)
_BLANK_LINES = re.compile(r"\n{3,}")


def normalize_ocr_text(raw: str | None) -> str | None:
    """Normalize provider output for display/comparison.

    Returns ``None`` for ``None`` so "provider said nothing" stays distinct
    from "provider said an empty string" as long as the caller preserves that
    distinction upstream (the pipeline records an ``empty_reason``).

    Steps, all documented and versioned:
      1. Unicode NFC — canonical composition only; no transliteration, no
         case folding, no character deletion.
      2. Tabs and carriage returns -> spaces / newlines (line breaks kept).
      3. Collapse horizontal whitespace runs to a single space.
      4. Trim trailing horizontal whitespace per line and the outer edges.
      5. Collapse 3+ consecutive newlines to two.
    """
    if raw is None:
        return None
    text = unicodedata.normalize("NFC", raw)
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\t", " ")
    text = _WS_RUN.sub(" ", text)
    text = _TRAILING.sub("", text)
    text = text.strip("\n")
    text = _BLANK_LINES.sub("\n\n", text)
    text = text.strip()
    return text


def is_effectively_empty(text: str | None) -> bool:
    """True when normalized text contains no visible characters.

    Used to distinguish a genuinely empty prediction from one containing only
    whitespace/padding — both are "empty" for reporting purposes, and neither
    is ever promoted to a transcript.
    """
    if text is None:
        return True
    return not any(not ch.isspace() for ch in text)
