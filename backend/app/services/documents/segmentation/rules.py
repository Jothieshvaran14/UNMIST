"""Versioned, configurable rules for the deterministic baseline segmenter.

All structural patterns and thresholds live here — never as scattered
``re`` literals across the app — so a rule change is reviewable in one place
and is signalled by :attr:`SegmentationRules.version` (surfaced on every
segment's ``algorithm_version``).

The patterns are deliberately conservative: they describe *observable* text
markers (numbering, answer keywords, figure/table captions, page furniture).
Anything they cannot place becomes an explicit ``unclassified`` segment rather
than being dropped or guessed at.

These are heuristics over OCR output, not a document-understanding model. A
match is evidence for structure, never proof of correctness.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cached_property
from typing import Optional

from ....config import Settings, get_settings


@dataclass(frozen=True)
class SegmentationRules:
    """Compiled, versioned segmentation rules.

    ``version`` is the human-visible algorithm version; ``from_settings`` picks
    up ``SEGMENT_ALGORITHM_VERSION`` so operators can bump it without a code
    change (and so reprocessing under a new version is observable per segment).
    """

    version: str

    @classmethod
    def from_settings(cls, settings: Optional[Settings] = None) -> "SegmentationRules":
        cfg = settings or get_settings()
        return cls(version=cfg.segment_algorithm_version)

    # -- numbering ---------------------------------------------------------

    @cached_property
    def question_pattern(self) -> re.Pattern[str]:
        """A line that opens a new question: ``1.`` ``Q2)`` ``(3)`` ``4-`` etc."""
        return re.compile(
            r"""^
            (?P<prefix>\s*\(?\s*)          # optional leading bracket
            (?:Q(?:uestion)?\s*\.?\s*)?      # optional leading "Q"/"Question"
            (?P<num>\d{1,3})                 # the question number
            (?P<sep>[.)\]:-])                # separator
            \s*
            (?P<after>\S|$)                  # content, or the bare marker
            """,
            re.VERBOSE,
        )

    @cached_property
    def subpart_pattern(self) -> re.Pattern[str]:
        """A line that opens a subpart: ``(a)`` ``(ii)`` ``a)`` ``i)`` etc.

        Parenthesised forms are accepted at any indent (they are unambiguous);
        bare ``a)`` / ``i)`` forms require leading whitespace so an ordinary
        sentence that happens to start with a letter is not misread as one.
        """
        return re.compile(
            r"""^
            [ \t]*
            (?:
                \(\s*(?P<alpha>[a-z])\s*\)                      # (a) (b) …
              | \(\s*(?P<romanp>i{1,3}|iv|v|vi{0,3}|ix|x{1,3})\s*\)   # (i) (ii) …
              | (?P<indent>[ \t]+)(?P<letter>[a-z])[.)]         # _a) _b.
              | (?P<indent2>[ \t]+)(?P<roman>i{1,3}|iv|v|vi{0,3}|ix|x{1,3})[.)]  # _i)
            )
            \s*
            (?P<after>\S|$)
            """,
            re.VERBOSE | re.IGNORECASE,
        )

    @cached_property
    def answer_pattern(self) -> re.Pattern[str]:
        """Explicit answer marker: ``Ans:``, ``Answer:``, ``Solution:`` …

        The separator (``:`` ``.`` ``-``) is required unless the marker is the
        whole line, so prose that merely starts with a keyword — ``Answers are
        provided …`` — is not mistaken for an answer.
        """
        return re.compile(
            r"""^\s*
            (?:ans(?:wer)?|sol(?:ution)?|resp(?:onse)?|reply)
            \s*
            (?:
                [:\.-]\s*(?P<after>\S)     # "Ans: 4"
              | [:\.-]\s*$                 # lone "Answer:"
              | $                          # lone "Answer"
            )
            """,
            re.VERBOSE | re.IGNORECASE,
        )

    @cached_property
    def page_number_pattern(self) -> re.Pattern[str]:
        """A line that is (almost) just a page number: ``3``, ``Page 3``."""
        return re.compile(
            r"""^\s*
            (?:page\s*)?
            (?P<num>\d{1,4})
            (?:\s*/\s*\d{1,4})?
            \s*$
            """,
            re.VERBOSE | re.IGNORECASE,
        )

    @cached_property
    def figure_pattern(self) -> re.Pattern[str]:
        """A caption/label for a diagram or image: ``Fig. 2: …``, ``Diagram: …``.

        Requires a following colon/dash so a sentence that merely *mentions* a
        figure (``Figure 2 shows …``) stays prose instead of becoming a
        diagram segment.
        """
        return re.compile(
            r"""^\s*
            (?:fig(?:ure)?|diagram|chart|graph|image|illustration|sketch)
            (?:\s*\.?\s*\d{1,3})?
            \s*[:.\-]\s*
            \S
            """,
            re.VERBOSE | re.IGNORECASE,
        )

    @cached_property
    def table_marker_pattern(self) -> re.Pattern[str]:
        """Pipe-delimited rows, or a captioned ``Table N: …``.

        Requires either two pipes (actual cell structure) or a numbered caption
        with a separator. A bare ``Table 1 shows …`` reference stays prose, and
        space-aligned tables without pipes are intentionally *not* detected (a
        documented limitation rather than a risky guess).
        """
        return re.compile(
            r"^\s*(?:table\s*\d{1,3}\b\s*[:.\-]\s*\S|.*\|.+\|.*)",
            re.IGNORECASE,
        )

    @cached_property
    def math_symbol_pattern(self) -> re.Pattern[str]:
        """Unambiguous math operators that almost never appear in prose."""
        return re.compile(r"[∑∫√≈≠≤≥±∞∆∇]")

    @cached_property
    def latex_pattern(self) -> re.Pattern[str]:
        """LaTeX math commands (``\\frac``, ``\\sqrt`` …)."""
        return re.compile(r"\\(?:frac|sqrt|sum|int|lim|prod|cdot|times)")

    # -- thresholds --------------------------------------------------------

    # Vertical gap (as a multiple of the region's own height) above which two
    # consecutive regions on the same page are treated as different blocks.
    paragraph_gap_factor: float = 1.6
    # How many pages a repeated top/bottom line must appear on to count as a
    # header/footer (guards against a real first line of body text).
    furniture_repeat_pages: int = 2
    # Minimum normalized-text length for cross-page header/footer comparison.
    furniture_min_chars: int = 3
    # Max pages a single run may span before continuation is not extended.
    # (No hard limit; multi-page answers are legitimate. Kept for clarity.)
    max_segment_pages: int = 64
    # Equation heuristic: max line length for an "=" / "^" formula line.
    equation_max_length: int = 120
    # Equation heuristic: ignore lines containing a lowercase English-looking
    # word this long or longer ("then", "what", "compute" … keep it prose).
    equation_min_word_length: int = 4

    @staticmethod
    def normalize_for_compare(text: str) -> str:
        """Whitespace/case-folded text used only for repetition comparison."""
        return re.sub(r"\s+", " ", (text or "")).strip().lower()
