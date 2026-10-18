"""Deterministic concept and relationship extraction.

This is the baseline extractor. It is fully deterministic and reproducible, and
every produced statement is tied to a source passage. It deliberately does not
claim prerequisite validity: a semantic relationship observed in text is not
automatically a learning dependency, so prerequisite edges are emitted as
``unverified`` and low-confidence until independently reviewed.

Model-based extraction is a separate integration boundary (see
:func:`get_extractor`); it is never silently used in place of this baseline.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

from ...errors import PolicyError
from ...models.knowledge import RelationshipType, VerificationStatus
from .validation import ClaimDraft, ConceptDraft, RelationshipDraft

PassageRef = tuple[str, str]  # (passage_id, text)

_DEFINITION_RE = re.compile(
    r"(?:^|\.\s+|\n)([A-Z][A-Za-z0-9 \-]{2,60}?)\s+"
    r"(?:is|are|refers to|means|describes|denotes)\s*[:\-]?\s*"
    r"([A-Za-z].{15,400})",
    re.MULTILINE,
)

_PREREQ_PATTERNS = [
    # "X requires Y" => Y prerequisite_of X
    re.compile(r"\b([A-Za-z][\w \-]{2,50}?)\s+requires\s+([A-Za-z][\w \-]{2,50})", re.IGNORECASE),
    # "Y is/are a prerequisite for X" => Y prerequisite_of X
    re.compile(
        r"\b([A-Za-z][\w \-]{2,50}?)\s+(?:is|are)\s+(?:a\s+)?prerequisite\s+(?:for|of)\s+"
        r"([A-Za-z][\w \-]{2,50})",
        re.IGNORECASE,
    ),
    # "before learning X, you need Y" => Y prerequisite_of X
    re.compile(
        r"before\s+(?:learning\s+)?([A-Za-z][\w \-]{2,50}),\s*you\s+(?:must\s+|should\s+)?need\s+"
        r"([A-Za-z][\w \-]{2,50})",
        re.IGNORECASE,
    ),
]

_PART_OF_PATTERNS = [
    re.compile(
        r"\b([A-Za-z][\w \-]{2,50}?)\s+(?:is|are)\s+(?:a\s+)?part\s+of\s+([A-Za-z][\w \-]{2,50})",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b([A-Za-z][\w \-]{2,50}?)\s+(?:is|are)\s+(?:a\s+)?component\s+of\s+"
        r"([A-Za-z][\w \-]{2,50})",
        re.IGNORECASE,
    ),
]


@dataclass(frozen=True)
class ExtractionResult:
    concepts: list[ConceptDraft]
    relationships: list[RelationshipDraft]


class ConceptExtractor(Protocol):
    """Integration boundary for concept/relationship extraction."""

    name: str

    def extract(
        self, topic_name: str, passages: list[PassageRef]
    ) -> ExtractionResult:  # pragma: no cover - protocol
        ...


class DeterministicExtractor:
    """Rule-based extractor. Deterministic, reproducible, evidence-preserving."""

    name = "deterministic-v1"

    def extract(
        self, topic_name: str, passages: list[PassageRef]
    ) -> ExtractionResult:
        concepts = self._extract_concepts(topic_name, passages)
        relationships = self._extract_relationships(concepts, passages)
        return ExtractionResult(concepts=concepts, relationships=relationships)

    # -- concepts -------------------------------------------------------
    def _extract_concepts(
        self, topic_name: str, passages: list[PassageRef]
    ) -> list[ConceptDraft]:
        candidates: dict[str, dict] = {}

        def add(
            name: str, definition: str | None, explanation: str | None, passage_id: str
        ) -> None:
            key = _normalize(name)
            if not key or len(key) < 3:
                return
            entry = candidates.setdefault(
                key,
                {"name": name.strip(), "definition": None, "explanation": None, "passages": []},
            )
            if definition and not entry["definition"]:
                entry["definition"] = definition.strip()
            if explanation and not entry["explanation"]:
                entry["explanation"] = explanation.strip()[:1200]
            entry["passages"].append(passage_id)

        # Topic seed concept.
        topic_ref = next((text for _, text in passages if topic_name.lower() in text.lower()), None)
        if topic_ref:
            add(
                topic_name,
                None,
                f"Topic under study. {topic_ref[:600]}",
                next(pid for pid, text in passages if topic_name.lower() in text.lower()),
            )

        for passage_id, text in passages:
            for match in _DEFINITION_RE.finditer(text):
                name = match.group(1).strip(" .,:;")
                definition = match.group(2).strip()
                add(name, definition, text, passage_id)

        concepts: list[ConceptDraft] = []
        for key, entry in candidates.items():
            claims = [
                ClaimDraft(
                    statement=(entry["definition"] or entry["explanation"] or entry["name"]),
                    claim_type="source_grounded",
                    passage_id=pid,
                    source_span=f"passage:{pid}",
                    confidence=0.8,
                    status=VerificationStatus.unverified.value,
                )
                for pid in entry["passages"][:5]
            ]
            concepts.append(
                ConceptDraft(
                    stable_key=_slug(entry["name"]),
                    canonical_name=entry["name"],
                    definition=entry["definition"],
                    explanation=entry["explanation"],
                    learning_objectives=[],
                    claims=claims,
                )
            )
        return concepts

    # -- relationships --------------------------------------------------
    def _extract_relationships(
        self, concepts: list[ConceptDraft], passages: list[PassageRef]
    ) -> list[RelationshipDraft]:
        key_by_name = {
            _normalize(concept.canonical_name): concept.stable_key for concept in concepts
        }
        relationships: list[RelationshipDraft] = []
        seen: set[tuple[str, str, str]] = set()

        def add(
            from_key: str,
            to_key: str,
            rel_type: str,
            evidence: list[dict],
            confidence: float,
        ) -> None:
            if from_key == to_key:
                return
            marker = (from_key, to_key, rel_type)
            if marker in seen:
                return
            seen.add(marker)
            relationships.append(
                RelationshipDraft(
                    from_key=from_key,
                    to_key=to_key,
                    relationship_type=rel_type,
                    evidence=evidence,
                    confidence=confidence,
                    status=VerificationStatus.unverified.value,
                )
            )

        for passage_id, text in passages:
            evidence = [{"passage_id": passage_id, "kind": "text"}]
            for pattern in _PREREQ_PATTERNS:
                for match in pattern.finditer(text):
                    dependent = _match_key(match.group(1), key_by_name)
                    required = _match_key(match.group(2), key_by_name)
                    if dependent and required:
                        add(
                            required,
                            dependent,
                            RelationshipType.prerequisite_of.value,
                            evidence,
                            0.3,
                        )
            for pattern in _PART_OF_PATTERNS:
                for match in pattern.finditer(text):
                    part = _match_key(match.group(1), key_by_name)
                    whole = _match_key(match.group(2), key_by_name)
                    if part and whole:
                        add(part, whole, RelationshipType.part_of.value, evidence, 0.5)

        # Co-occurrence related_to (explicitly unverified).
        for passage_id, text in passages:
            present = [
                concept.stable_key
                for concept in concepts
                if concept.canonical_name.lower() in text.lower()
            ]
            for i in range(len(present)):
                for j in range(len(present)):
                    if i == j:
                        continue
                    marker = (present[i], present[j], RelationshipType.related_to.value)
                    if marker in seen:
                        continue
                    seen.add(marker)
                    relationships.append(
                        RelationshipDraft(
                            from_key=present[i],
                            to_key=present[j],
                            relationship_type=RelationshipType.related_to.value,
                            evidence=[{"passage_id": passage_id, "kind": "co_occurrence"}],
                            confidence=0.2,
                            status=VerificationStatus.unverified.value,
                        )
                    )
        return relationships


def _normalize(text: str) -> str:
    return " ".join(text.strip().lower().split())


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:180] or "concept"


def _match_key(phrase: str, key_by_name: dict[str, str]) -> str | None:
    normalized = _normalize(phrase)
    if normalized in key_by_name:
        return key_by_name[normalized]
    # Fallback: substring match for multi-word mentions.
    for name, key in key_by_name.items():
        if normalized and normalized in name:
            return key
    return None


class UnconfiguredLLMExtractor:
    """Placeholder that fails loudly rather than fabricating model output.

    The platform does not call any model without an explicit provider
    configuration. This boundary exists so that wiring a real provider is a
    deliberate, reviewed change.
    """

    name = "llm-unconfigured"

    def extract(self, topic_name: str, passages: list[PassageRef]) -> ExtractionResult:
        raise PolicyError(
            "No LLM extraction provider is configured. The deterministic extractor is "
            "used by default. Configure LLM_PROVIDER and credentials to enable "
            "model-based extraction.",
            code="llm_not_configured",
        )


def get_extractor(provider: str | None = None) -> ConceptExtractor:
    """Return the configured extractor.

    Only ``none``/``deterministic`` is implemented. Any other provider raises so
    that an unconfigured integration cannot silently produce unverified output.
    """
    if provider is None:
        from ...config import get_settings

        provider = get_settings().llm_provider
    normalized = (provider or "none").strip().lower()
    if normalized in {"none", "deterministic", ""}:
        return DeterministicExtractor()
    raise PolicyError(
        f"LLM extraction provider '{normalized}' is not configured. "
        "Set LLM_PROVIDER=none to use the deterministic extractor.",
        code="llm_not_configured",
    )
