"""Deterministic baseline segmenter: OCR regions -> structural candidates.

Pure, provider-independent, and reproducible: the same regions, pages, and
:class:`~.rules.SegmentationRules` always yield the same segments in the same
reading order, with no randomness, no network, no model, and no LLM.

What it does
------------
1. Normalizes and sorts input (so *out-of-order* region lists are handled).
2. Detects page furniture — headers, footers, page numbers — from repetition
   across pages and page-number patterns, removing them from body parsing.
3. Walks body regions in reading order, opening/closing blocks from evidence:
   numbering, indentation/vertical gaps, answer markers, figure/table/equation
   markers, and page boundaries.
4. Assigns a **categorical** uncertainty to every block, never a numeric score.
5. Guarantees every input region lands in exactly one segment (an explicit
   ``unclassified`` fallback catches anything no rule places — nothing is
   silently dropped).

What it is not
--------------
It is a heuristic over OCR output, not a document-understanding model. It
never mutates OCR evidence, never asserts answer correctness, and preserves
uncertain boundaries (``ambiguous_boundary`` / ``suspected_continuation``)
rather than forcing a confident classification.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Sequence

from ....models.documents import SegmentType, SegmentUncertainty
from .base import (
    PageInput,
    RegionInput,
    SegmentationResult,
    SegmentCandidate,
    derive_segment_uid,
)
from .rules import SegmentationRules

# Sentence-final punctuation. A block whose text does NOT end with one of
# these is treated as unfinished, which is the conservative signal used to
# continue it across a page break. This must be a *tuple*: passing a plain
# string to ``str.endswith`` tests for the literal suffix ".?!" rather than
# for any one of the three characters, which would silently disable the rule.
_SENTENCE_FINAL = (".", "?", "!")


@dataclass
class _ContentBlock:
    """In-progress body block before it becomes a :class:`SegmentCandidate`."""

    segment_type: SegmentType
    uncertainty: SegmentUncertainty
    # Global (sorted) indices of contributing regions, in order.
    region_gidx: list[int] = field(default_factory=list)
    label: Optional[str] = None
    # Position (index) of the parent block within the content list, if any.
    parent_pos: Optional[int] = None
    provenance: dict = field(default_factory=dict)


class BaselineSegmenter:
    """Applies versioned rules to turn regions into structural candidates."""

    def __init__(self, rules: SegmentationRules) -> None:
        self.rules = rules

    # -- public ------------------------------------------------------------

    def segment(
        self,
        regions: Sequence[RegionInput],
        pages: Sequence[PageInput],
    ) -> SegmentationResult:
        ordered, warnings = self._normalize(regions)
        if not ordered:
            return SegmentationResult(
                segments=(), covered_region_ids=(), warnings=("no_regions", *warnings)
            )

        page_geom = {p.page_number: p for p in pages}
        furniture = self._detect_furniture(ordered, page_geom)

        content, furniture_pairs = self._walk_body(ordered, furniture)
        records, covered = self._build_candidates(ordered, content, furniture_pairs)
        return SegmentationResult(
            segments=tuple(records),
            covered_region_ids=tuple(covered),
            warnings=tuple(warnings),
        )

    # -- step 1: normalize -------------------------------------------------

    def _normalize(
        self, regions: Sequence[RegionInput]
    ) -> tuple[list[RegionInput], list[str]]:
        """Sanitize and sort into reading order; never raises on bad input."""
        warnings: list[str] = []
        cleaned: list[tuple[int, RegionInput]] = []
        for idx, r in enumerate(regions):
            if r.text is None or r.text.strip() == "":
                text = ""
            else:
                text = r.text
            cleaned.append(
                (
                    idx,
                    RegionInput(
                        region_id=r.region_id,
                        page_number=max(1, int(r.page_number or 1)),
                        reading_order=int(r.reading_order or 0),
                        bbox_left=max(0, int(r.bbox_left or 0)),
                        bbox_top=max(0, int(r.bbox_top or 0)),
                        bbox_width=max(0, int(r.bbox_width or 0)),
                        bbox_height=max(0, int(r.bbox_height or 0)),
                        text=text,
                        region_type=r.region_type or "text_line",
                    ),
                )
            )
        # Deterministic ordering: page, then declared reading order, then
        # vertical position, then original index as the stable tiebreaker.
        cleaned.sort(
            key=lambda pair: (
                pair[1].page_number,
                pair[1].reading_order,
                pair[1].bbox_top,
                pair[0],
            )
        )
        ordered = [r for _, r in cleaned]
        if any(r.text == "" for r in ordered):
            warnings.append("empty_region_text_present")
        return ordered, warnings

    # -- step 2: page furniture -------------------------------------------

    def _detect_furniture(
        self,
        ordered: list[RegionInput],
        page_geom: dict[int, PageInput],
    ) -> dict[int, SegmentType]:
        """Mark headers/footers/page numbers; returns global-index -> type."""
        by_page: dict[int, list[int]] = {}
        for i, r in enumerate(ordered):
            by_page.setdefault(r.page_number, []).append(i)

        furniture: dict[int, SegmentType] = {}

        # Repeated top / bottom lines across pages = header / footer.
        top_counts: dict[str, set[int]] = {}
        bottom_counts: dict[str, set[int]] = {}
        top_idx: dict[tuple[int, str], list[int]] = {}
        bottom_idx: dict[tuple[int, str], list[int]] = {}
        for page_no, idxs in by_page.items():
            for i in idxs[:2]:
                key = self.rules.normalize_for_compare(ordered[i].text)
                if len(key) >= self.rules.furniture_min_chars:
                    top_counts.setdefault(key, set()).add(page_no)
                    top_idx.setdefault((page_no, key), []).append(i)
            for i in idxs[-2:]:
                key = self.rules.normalize_for_compare(ordered[i].text)
                if len(key) >= self.rules.furniture_min_chars:
                    bottom_counts.setdefault(key, set()).add(page_no)
                    bottom_idx.setdefault((page_no, key), []).append(i)

        for key, pages_seen in top_counts.items():
            if len(pages_seen) >= self.rules.furniture_repeat_pages:
                for page_no in pages_seen:
                    for i in top_idx[(page_no, key)]:
                        if self._is_structural(ordered[i]):
                            continue
                        furniture[i] = SegmentType.page_header
        for key, pages_seen in bottom_counts.items():
            if len(pages_seen) >= self.rules.furniture_repeat_pages:
                for page_no in pages_seen:
                    for i in bottom_idx[(page_no, key)]:
                        # Header wins if a line is both (top+bottom repeat).
                        if self._is_structural(ordered[i]):
                            continue
                        furniture.setdefault(i, SegmentType.page_footer)

        # Explicit page numbers. Require positional or value evidence so a
        # bare number in the body is not misread as a page number.
        for page_no, idxs in by_page.items():
            geom = page_geom.get(page_no)
            height = geom.height_px if geom else None
            edge = set(idxs[:2]) | set(idxs[-2:])
            for i in idxs:
                if i in furniture:
                    continue
                m = self.rules.page_number_pattern.match(ordered[i].text)
                if not m:
                    continue
                value = int(m.group("num"))
                positional = i in edge
                if height and ordered[i].bbox_height:
                    frac = ordered[i].bbox_top / max(1, height)
                    positional = positional or frac < 0.18 or frac > 0.75
                if value == page_no or positional:
                    furniture[i] = SegmentType.page_number

        return furniture

    # -- step 3: body walk -------------------------------------------------

    def _walk_body(
        self,
        ordered: list[RegionInput],
        furniture: dict[int, SegmentType],
    ) -> tuple[list[_ContentBlock], list[tuple[int, SegmentType]]]:
        """Assemble body blocks and one-segment-per-region furniture blocks."""
        content: list[_ContentBlock] = []
        furniture_pairs: list[tuple[int, SegmentType]] = [
            (i, t) for i, t in sorted(furniture.items())
        ]

        open_pos: Optional[int] = None      # currently open content block
        cur_question: Optional[int] = None  # last opened question (content pos)
        cur_subpart: Optional[int] = None   # last opened subpart
        last_qnum: Optional[int] = None     # last question number seen

        for gidx, region in enumerate(ordered):
            if gidx in furniture:
                # A furniture region closes any open body block: headers and
                # footers interrupt reading order.
                open_pos = None
                continue

            role, qnum, label, marker_only = self._classify(region)
            same_page, gap = self._gap_to_open(ordered, content, open_pos, region)

            if role == "question":
                uncertainty = SegmentUncertainty.clear
                if marker_only:
                    # "1." with no readable content after the marker.
                    uncertainty = SegmentUncertainty.missing_ocr
                elif (
                    qnum is not None
                    and last_qnum is not None
                    and qnum <= last_qnum
                ):
                    uncertainty = SegmentUncertainty.conflicting_numbering
                if qnum is not None:
                    last_qnum = qnum if last_qnum is None else max(last_qnum, qnum)
                open_pos = self._open(
                    content,
                    SegmentType.question,
                    uncertainty,
                    gidx,
                    label=label,
                    parent=None,
                )
                cur_question = open_pos
                cur_subpart = None
                continue

            if role == "subpart":
                open_pos = self._open(
                    content,
                    SegmentType.subpart,
                    (
                        SegmentUncertainty.missing_ocr
                        if marker_only
                        else SegmentUncertainty.clear
                    ),
                    gidx,
                    label=label,
                    parent=cur_question,
                )
                cur_subpart = open_pos
                continue

            if role == "answer":
                open_pos = self._open(
                    content,
                    SegmentType.answer,
                    SegmentUncertainty.clear,
                    gidx,
                    label=None,
                    parent=cur_subpart if cur_subpart is not None else cur_question,
                )
                continue

            if role in {"figure", "equation", "table"}:
                # A marker only starts its own block when it is separated from
                # the open block (paragraph/page break or nothing open); when it
                # flows directly after body text it stays part of that text.
                if (
                    open_pos is not None
                    and same_page
                    and gap <= self._para_gap(ordered, content, open_pos, region)
                ):
                    self._append(ordered, content, open_pos, gidx)
                else:
                    stype = {
                        "figure": SegmentType.diagram,
                        "equation": SegmentType.equation,
                        "table": SegmentType.table,
                    }[role]
                    open_pos = self._open(
                        content, stype, SegmentUncertainty.clear, gidx, parent=None
                    )
                continue

            # Plain text: continuation logic.
            if open_pos is None:
                open_pos = self._open(
                    content,
                    SegmentType.unclassified,
                    SegmentUncertainty.unclassified,
                    gidx,
                )
                continue

            block = content[open_pos]
            if not same_page:
                if self._can_continue_across_pages(ordered, block, region):
                    self._append(ordered, content, open_pos, gidx)
                else:
                    # Conservative: content at the top of a new page after a
                    # finished sentence is left unclassified rather than
                    # guessed to be a continuation.
                    open_pos = self._open(
                        content,
                        SegmentType.unclassified,
                        SegmentUncertainty.unclassified,
                        gidx,
                    )
                    block.provenance["declined_continuation"] = True
            elif gap <= self._para_gap(ordered, content, open_pos, region):
                self._append(ordered, content, open_pos, gidx)
            else:
                # Paragraph break: text below a question becomes an *inferred*
                # answer — explicitly not a verified one.
                if block.segment_type in {SegmentType.question, SegmentType.subpart}:
                    open_pos = self._open(
                        content,
                        SegmentType.answer,
                        SegmentUncertainty.ambiguous_boundary,
                        gidx,
                        parent=cur_subpart if cur_subpart is not None else cur_question,
                        provenance={"inferred_from_position": True},
                    )
                else:
                    open_pos = self._open(
                        content,
                        SegmentType.unclassified,
                        SegmentUncertainty.unclassified,
                        gidx,
                    )

        return content, furniture_pairs

    # -- block helpers -----------------------------------------------------

    def _open(
        self,
        content: list[_ContentBlock],
        segment_type: SegmentType,
        uncertainty: SegmentUncertainty,
        gidx: int,
        *,
        label: Optional[str] = None,
        parent: Optional[int] = None,
        provenance: Optional[dict] = None,
    ) -> int:
        content.append(
            _ContentBlock(
                segment_type=segment_type,
                uncertainty=uncertainty,
                region_gidx=[gidx],
                label=label,
                parent_pos=parent,
                provenance=dict(provenance or {}),
            )
        )
        return len(content) - 1

    def _append(
        self,
        ordered: list[RegionInput],
        content: list[_ContentBlock],
        open_pos: int,
        gidx: int,
    ) -> None:
        content[open_pos].region_gidx.append(gidx)

    # -- gap / continuation ------------------------------------------------

    def _last_region(
        self, ordered: list[RegionInput], block: _ContentBlock
    ) -> Optional[RegionInput]:
        if not block.region_gidx:
            return None
        return ordered[block.region_gidx[-1]]

    def _gap_to_open(
        self,
        ordered: list[RegionInput],
        content: list[_ContentBlock],
        open_pos: Optional[int],
        region: RegionInput,
    ) -> tuple[bool, float]:
        """``(same_page, vertical_gap)`` between the open block and region."""
        if open_pos is None:
            return False, float("inf")
        last = self._last_region(ordered, content[open_pos])
        if last is None:
            return False, float("inf")
        if last.page_number != region.page_number:
            return False, float("inf")
        gap = region.bbox_top - (last.bbox_top + last.bbox_height)
        return True, float(gap)

    def _para_gap(
        self,
        ordered: list[RegionInput],
        content: list[_ContentBlock],
        open_pos: int,
        region: RegionInput,
    ) -> float:
        ref = max(region.bbox_height, 1)
        last = self._last_region(ordered, content[open_pos])
        if last is not None and last.bbox_height:
            ref = max(last.bbox_height, ref)
        return self.rules.paragraph_gap_factor * ref

    def _can_continue_across_pages(
        self,
        ordered: list[RegionInput],
        block: _ContentBlock,
        region: RegionInput,
    ) -> bool:
        last = self._last_region(ordered, block)
        if last is None:
            return False
        start_page = ordered[block.region_gidx[0]].page_number
        if region.page_number - start_page >= self.rules.max_segment_pages:
            return False
        text = self._assemble(ordered, block.region_gidx)
        # Only continue when the block reads as unfinished. Merging content
        # purely because it sits at the top/bottom of adjacent pages is
        # explicitly avoided.
        return bool(text) and not text.endswith(_SENTENCE_FINAL)

    # -- classification ----------------------------------------------------

    def _is_structural(self, region: RegionInput) -> bool:
        """True when a repeated line is really a question/subpart/answer.

        Guards header/footer detection from swallowing real content: an exam
        paper legitimately repeats an identical ``Answer:`` line at the bottom
        of several pages, and that must stay an answer, not become furniture.
        """
        role, _, _, _ = self._classify(region)
        return role in {"question", "subpart", "answer"}

    def _classify(
        self, region: RegionInput
    ) -> tuple[str, Optional[int], Optional[str], bool]:
        """Return ``(role, question_number, label, marker_only)``.

        ``marker_only`` is True when the line is just a structural marker with
        no content after it (``1.`` alone) — the evidence a human would need to
        see flagged as :data:`SegmentUncertainty.missing_ocr`.
        """
        text = region.text or ""
        m = self.rules.question_pattern.match(text)
        if m:
            after = m.group("after")
            return "question", int(m.group("num")), m.group("num"), not after
        m = self.rules.subpart_pattern.match(text)
        if m:
            label = next(
                (
                    m.group(g)
                    for g in ("alpha", "romanp", "letter", "roman")
                    if m.groupdict().get(g)
                ),
                None,
            )
            after = m.group("after")
            return "subpart", None, (label or "").strip() or None, not after
        if self.rules.answer_pattern.match(text):
            return "answer", None, None, False
        if self.rules.figure_pattern.match(text):
            return "figure", None, None, False
        if self.rules.table_marker_pattern.match(text):
            return "table", None, None, False
        if self._looks_equation(text):
            return "equation", None, None, False
        return "plain", None, None, False

    def _looks_equation(self, text: str) -> bool:
        """Conservative equation detection over OCR'd text.

        Strong cues (``$…$``, LaTeX, real math operators) always qualify. A
        plain ``=`` / ``^`` line only qualifies when it is short, is not phrased
        as a question, and contains no long lowercase word — which keeps
        ordinary sentences out of the equation bucket. This is a heuristic, not
        a formula parser, and is reported as such in the docs.
        """
        stripped = (text or "").strip()
        if not stripped:
            return False
        if "$" in stripped:
            return True
        if self.rules.latex_pattern.search(stripped):
            return True
        if self.rules.math_symbol_pattern.search(stripped):
            return True
        if stripped.endswith(("?", "!")):
            return False
        if ("=" in stripped or "^" in stripped) and len(stripped) <= self.rules.equation_max_length:
            words = [w for w in stripped.split() if w]
            long_words = [
                w
                for w in words
                if w.isalpha()
                and w.islower()
                and len(w) >= self.rules.equation_min_word_length
            ]
            if not long_words:
                return True
        return False

    # -- step 4: candidates ------------------------------------------------

    def _build_candidates(
        self,
        ordered: list[RegionInput],
        content: list[_ContentBlock],
        furniture_pairs: list[tuple[int, SegmentType]],
    ) -> tuple[list[SegmentCandidate], list[str]]:
        content_cands: list[SegmentCandidate] = []
        records: list[tuple[int, SegmentCandidate, Optional[int]]] = []

        for pos, blk in enumerate(content):
            first = blk.region_gidx[0]
            start_page = ordered[first].page_number
            end_page = ordered[blk.region_gidx[-1]].page_number
            spans = end_page > start_page
            uncertainty = blk.uncertainty
            if (
                spans
                and uncertainty
                in {
                    SegmentUncertainty.clear,
                    SegmentUncertainty.ambiguous_boundary,
                    SegmentUncertainty.unclassified,
                }
            ):
                uncertainty = SegmentUncertainty.suspected_continuation
            if not self._assemble(ordered, blk.region_gidx):
                uncertainty = SegmentUncertainty.missing_ocr

            prov = dict(blk.provenance)
            prov["spans_pages"] = spans
            prov["base_uncertainty"] = blk.uncertainty.value

            cand = self._make_candidate(
                ordered,
                blk.region_gidx,
                segment_type=blk.segment_type,
                uncertainty=uncertainty,
                start_page=start_page,
                end_page=end_page,
                label=blk.label,
                provenance=prov,
            )
            content_cands.append(cand)
            records.append((first, cand, blk.parent_pos))

        for gidx, ftype in furniture_pairs:
            cand = self._make_candidate(
                ordered,
                [gidx],
                segment_type=ftype,
                uncertainty=SegmentUncertainty.clear,
                start_page=ordered[gidx].page_number,
                end_page=ordered[gidx].page_number,
                provenance={"furniture": True},
            )
            records.append((gidx, cand, None))

        # Global reading order by first contributing region, then uid + parents.
        records.sort(key=lambda rec: rec[0])
        for order, (_, cand, _) in enumerate(records):
            cand.reading_order = order
        covered: list[str] = []
        for gidx, cand, _ in records:
            cand.segment_uid = derive_segment_uid(
                first_region_id=ordered[gidx].region_id,
                segment_type=cand.segment_type,
            )
        for _, cand, parent_pos in records:
            if parent_pos is not None and 0 <= parent_pos < len(content_cands):
                parent = content_cands[parent_pos]
                if parent.segment_uid:
                    cand.parent_uid = parent.segment_uid
        for _, cand, _ in records:
            covered.extend(cand.region_ids)
        return [cand for _, cand, _ in records], covered

    def _make_candidate(
        self,
        ordered: list[RegionInput],
        region_gidx: list[int],
        *,
        segment_type: SegmentType,
        uncertainty: SegmentUncertainty,
        start_page: int,
        end_page: int,
        label: Optional[str] = None,
        provenance: Optional[dict] = None,
    ) -> SegmentCandidate:
        chosen = [ordered[i] for i in region_gidx if ordered[i].page_number == start_page]
        if not chosen:
            chosen = [ordered[region_gidx[0]]]
        left = min(r.bbox_left for r in chosen)
        top = min(r.bbox_top for r in chosen)
        right = max(r.bbox_left + r.bbox_width for r in chosen)
        bottom = max(r.bbox_top + r.bbox_height for r in chosen)
        prov = dict(provenance or {})
        prov["algorithm"] = "rule_baseline"
        return SegmentCandidate(
            segment_type=segment_type,
            uncertainty=uncertainty,
            start_page=start_page,
            end_page=end_page,
            reading_order=0,
            bbox_left=left,
            bbox_top=top,
            bbox_width=max(0, right - left),
            bbox_height=max(0, bottom - top),
            text=self._assemble(ordered, region_gidx),
            region_ids=[ordered[i].region_id for i in region_gidx],
            label=label,
            provenance=prov,
        )

    @staticmethod
    def _assemble(ordered: list[RegionInput], region_gidx: list[int]) -> str:
        parts = [(ordered[i].text or "").strip() for i in region_gidx]
        return "\n".join(p for p in parts if p)
