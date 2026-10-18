"""Pure graph validation logic.

Validation is deterministic and independent of persistence so it can be unit
tested exhaustively. The product rules enforced here:

  * Every relationship must carry supporting evidence.
  * Prerequisite relationships must be evidence-backed; unsupported prerequisites
    are flagged rather than silently trusted.
  * Prerequisite dependencies must be acyclic.
  * Duplicate concepts and alias collisions are detected.
  * Orphaned concepts are reported (information, not failure).
  * Unverified claims are never reported as verified.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Iterable, Optional

from ...models.knowledge import RelationshipType, VerificationStatus

ERROR = "error"
WARNING = "warning"
INFO = "info"


@dataclass(frozen=True)
class ClaimDraft:
    statement: str
    claim_type: str
    passage_id: Optional[str] = None
    source_span: Optional[str] = None
    confidence: float = 0.0
    status: str = VerificationStatus.unverified.value


@dataclass(frozen=True)
class ConceptDraft:
    stable_key: str
    canonical_name: str
    aliases: list[str] = field(default_factory=list)
    definition: Optional[str] = None
    explanation: Optional[str] = None
    misconceptions: list[str] = field(default_factory=list)
    difficulty: Optional[float] = None
    learning_objectives: list[str] = field(default_factory=list)
    claims: list[ClaimDraft] = field(default_factory=list)


@dataclass(frozen=True)
class RelationshipDraft:
    from_key: str
    to_key: str
    relationship_type: str
    evidence: list[dict] = field(default_factory=list)
    confidence: float = 0.0
    status: str = VerificationStatus.unverified.value


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    severity: str
    message: str
    concept_key: Optional[str] = None


@dataclass(frozen=True)
class ValidationReport:
    issues: list[ValidationIssue]
    concept_count: int
    relationship_count: int

    @property
    def errors(self) -> list[ValidationIssue]:
        return [issue for issue in self.issues if issue.severity == ERROR]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [issue for issue in self.issues if issue.severity == WARNING]

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "concept_count": self.concept_count,
            "relationship_count": self.relationship_count,
            "error_count": len(self.errors),
            "warning_count": len(self.warnings),
            "issues": [
                {
                    "code": issue.code,
                    "severity": issue.severity,
                    "message": issue.message,
                    "concept_key": issue.concept_key,
                }
                for issue in self.issues
            ],
        }


def _normalize(text: str) -> str:
    return " ".join(text.strip().lower().split())


def detect_cycles(
    keys: Iterable[str], relationships: Iterable[RelationshipDraft], edge_type: str
) -> list[list[str]]:
    """Return cycles for the given edge type using Kahn's algorithm + DFS fallback."""
    adjacency: dict[str, list[str]] = defaultdict(list)
    nodes = set(keys)
    for rel in relationships:
        if rel.relationship_type == edge_type:
            adjacency[rel.from_key].append(rel.to_key)
            nodes.add(rel.from_key)
            nodes.add(rel.to_key)

    # Kahn's algorithm to find nodes involved in cycles.
    indegree: dict[str, int] = {node: 0 for node in nodes}
    for source, targets in adjacency.items():
        for target in targets:
            indegree[target] = indegree.get(target, 0) + 1

    queue = deque(node for node in nodes if indegree[node] == 0)
    remaining = set(nodes)
    while queue:
        node = queue.popleft()
        remaining.discard(node)
        for target in adjacency.get(node, []):
            indegree[target] -= 1
            if indegree[target] == 0:
                queue.append(target)

    if not remaining:
        return []

    # Extract a representative cycle per strongly-connected component.
    cycles: list[list[str]] = []
    visited: set[str] = set()
    for start in remaining:
        if start in visited:
            continue
        on_stack: set[str] = set()
        path: list[str] = []

        def dfs(node: str) -> bool:
            visited.add(node)
            on_stack.add(node)
            path.append(node)
            for target in adjacency.get(node, []):
                if target not in remaining:
                    continue
                if target in on_stack:
                    cycle_start = path.index(target)
                    cycles.append(path[cycle_start:] + [target])
                    return True
                if target not in visited:
                    if dfs(target):
                        return True
            path.pop()
            on_stack.discard(node)
            return False

        dfs(start)
    return cycles


def validate_graph(
    concepts: Iterable[ConceptDraft], relationships: Iterable[RelationshipDraft]
) -> ValidationReport:
    concepts = list(concepts)
    relationships = list(relationships)
    issues: list[ValidationIssue] = []
    keys = [concept.stable_key for concept in concepts]
    key_set = set(keys)

    # Duplicate stable keys are a structural error.
    seen_keys: set[str] = set()
    for concept in concepts:
        if concept.stable_key in seen_keys:
            issues.append(
                ValidationIssue(
                    "duplicate_concept_key",
                    ERROR,
                    f"Duplicate concept key '{concept.stable_key}'",
                    concept.stable_key,
                )
            )
        seen_keys.add(concept.stable_key)

    # Duplicate canonical names / alias collisions (case-insensitive).
    name_owner: dict[str, str] = {}
    alias_owner: dict[str, str] = {}
    for concept in concepts:
        normalized = _normalize(concept.canonical_name)
        if normalized in name_owner and name_owner[normalized] != concept.stable_key:
            issues.append(
                ValidationIssue(
                    "duplicate_concept",
                    WARNING,
                    f"Concept '{concept.canonical_name}' duplicates '{name_owner[normalized]}'",
                    concept.stable_key,
                )
            )
        name_owner.setdefault(normalized, concept.stable_key)
        for alias in concept.aliases:
            normalized_alias = _normalize(alias)
            if (
                normalized_alias in alias_owner
                and alias_owner[normalized_alias] != concept.stable_key
            ):
                issues.append(
                    ValidationIssue(
                        "alias_collision",
                        WARNING,
                        f"Alias '{alias}' is shared with '{alias_owner[normalized_alias]}'",
                        concept.stable_key,
                    )
                )
            alias_owner.setdefault(normalized_alias, concept.stable_key)

    # Relationship checks.
    connected: set[str] = set()
    for rel in relationships:
        if rel.from_key not in key_set or rel.to_key not in key_set:
            issues.append(
                ValidationIssue(
                    "dangling_relationship",
                    ERROR,
                    f"Relationship {rel.from_key} -> {rel.to_key} references an unknown concept",
                )
            )
            continue
        if rel.from_key == rel.to_key:
            issues.append(
                ValidationIssue(
                    "self_relationship",
                    ERROR,
                    f"Self-relationship on '{rel.from_key}'",
                    rel.from_key,
                )
            )
        if not rel.evidence:
            issues.append(
                ValidationIssue(
                    "missing_evidence",
                    ERROR,
                    f"Relationship {rel.from_key} -> {rel.to_key} ({rel.relationship_type}) "
                    "has no supporting evidence",
                )
            )
        if rel.relationship_type == RelationshipType.prerequisite_of.value:
            if rel.status != VerificationStatus.verified.value:
                issues.append(
                    ValidationIssue(
                        "unverified_prerequisite",
                        WARNING,
                        f"Prerequisite {rel.from_key} -> {rel.to_key} is not verified",
                        rel.from_key,
                    )
                )
            if rel.confidence < 0.5:
                issues.append(
                    ValidationIssue(
                        "low_confidence_prerequisite",
                        WARNING,
                        f"Prerequisite {rel.from_key} -> {rel.to_key} has low confidence "
                        f"({rel.confidence:.2f})",
                        rel.from_key,
                    )
                )
        connected.add(rel.from_key)
        connected.add(rel.to_key)

    # Claim provenance checks.
    for concept in concepts:
        for claim in concept.claims:
            if not claim.passage_id and claim.claim_type == "source_grounded":
                issues.append(
                    ValidationIssue(
                        "missing_evidence",
                        WARNING,
                        f"Source-grounded claim on '{concept.stable_key}' has no passage",
                        concept.stable_key,
                    )
                )

    # Prerequisite cycles.
    cycles = detect_cycles(keys, relationships, RelationshipType.prerequisite_of.value)
    for cycle in cycles:
        issues.append(
            ValidationIssue(
                "prerequisite_cycle",
                ERROR,
                "Prerequisite cycle detected: " + " -> ".join(cycle),
                cycle[0],
            )
        )

    # Orphan detection.
    for concept in concepts:
        if concept.stable_key not in connected:
            issues.append(
                ValidationIssue(
                    "orphan_concept",
                    INFO,
                    f"Concept '{concept.canonical_name}' has no relationships",
                    concept.stable_key,
                )
            )

    return ValidationReport(
        issues=issues, concept_count=len(concepts), relationship_count=len(relationships)
    )


def build_snapshot(
    concepts: Iterable[ConceptDraft], relationships: Iterable[RelationshipDraft]
) -> dict:
    """Build an immutable, JSON-serializable graph snapshot."""
    concepts = list(concepts)
    relationships = list(relationships)
    return {
        "schema_version": 1,
        "concepts": [
            {
                "stable_key": concept.stable_key,
                "canonical_name": concept.canonical_name,
                "aliases": list(concept.aliases),
                "definition": concept.definition,
                "explanation": concept.explanation,
                "misconceptions": list(concept.misconceptions),
                "difficulty": concept.difficulty,
                "learning_objectives": list(concept.learning_objectives),
                "claims": [
                    {
                        "statement": claim.statement,
                        "claim_type": claim.claim_type,
                        "passage_id": claim.passage_id,
                        "source_span": claim.source_span,
                        "confidence": claim.confidence,
                        "status": claim.status,
                    }
                    for claim in concept.claims
                ],
            }
            for concept in concepts
        ],
        "relationships": [
            {
                "from_key": rel.from_key,
                "to_key": rel.to_key,
                "relationship_type": rel.relationship_type,
                "evidence": list(rel.evidence),
                "confidence": rel.confidence,
                "status": rel.status,
            }
            for rel in relationships
        ],
    }
