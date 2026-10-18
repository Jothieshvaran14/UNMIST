"""Graph validation tests."""

from __future__ import annotations

from app.models.knowledge import RelationshipType, VerificationStatus
from app.services.knowledge.validation import (
    ClaimDraft,
    ConceptDraft,
    RelationshipDraft,
    build_snapshot,
    validate_graph,
)


def _concept(key: str, name: str = "") -> ConceptDraft:
    return ConceptDraft(stable_key=key, canonical_name=name or key)


def test_valid_graph_passes():
    concepts = [_concept("a", "A"), _concept("b", "B")]
    relationships = [
        RelationshipDraft(
            from_key="a",
            to_key="b",
            relationship_type=RelationshipType.prerequisite_of.value,
            evidence=[{"passage_id": "p1"}],
            confidence=0.9,
            status=VerificationStatus.verified.value,
        )
    ]
    report = validate_graph(concepts, relationships)
    assert report.ok, report.to_dict()


def test_missing_evidence_is_an_error():
    concepts = [_concept("a", "A"), _concept("b", "B")]
    relationships = [
        RelationshipDraft(
            from_key="a",
            to_key="b",
            relationship_type=RelationshipType.prerequisite_of.value,
            evidence=[],
            confidence=0.9,
            status=VerificationStatus.verified.value,
        )
    ]
    report = validate_graph(concepts, relationships)
    assert not report.ok
    assert any(issue.code == "missing_evidence" for issue in report.issues)


def test_unverified_prerequisite_is_flagged():
    concepts = [_concept("a", "A"), _concept("b", "B")]
    relationships = [
        RelationshipDraft(
            from_key="a",
            to_key="b",
            relationship_type=RelationshipType.prerequisite_of.value,
            evidence=[{"passage_id": "p1"}],
            confidence=0.2,
            status=VerificationStatus.unverified.value,
        )
    ]
    report = validate_graph(concepts, relationships)
    codes = {issue.code for issue in report.issues}
    assert "unverified_prerequisite" in codes
    assert "low_confidence_prerequisite" in codes


def test_prerequisite_cycle_is_an_error():
    concepts = [_concept("a", "A"), _concept("b", "B"), _concept("c", "C")]
    prereq = RelationshipType.prerequisite_of.value
    relationships = [
        RelationshipDraft("a", "b", prereq, [{"p": 1}], 0.9, "verified"),
        RelationshipDraft("b", "c", prereq, [{"p": 1}], 0.9, "verified"),
        RelationshipDraft("c", "a", prereq, [{"p": 1}], 0.9, "verified"),
    ]
    report = validate_graph(concepts, relationships)
    assert any(issue.code == "prerequisite_cycle" for issue in report.issues)


def test_duplicate_canonical_name_is_a_warning():
    concepts = [_concept("a", "Decorator"), _concept("b", "decorator")]
    report = validate_graph(concepts, [])
    assert any(issue.code == "duplicate_concept" for issue in report.issues)


def test_orphan_concept_is_reported_as_info():
    concepts = [_concept("a", "A"), _concept("b", "B")]
    report = validate_graph(concepts, [])
    assert any(issue.code == "orphan_concept" for issue in report.issues)
    assert report.ok  # orphans are informational only


def test_self_relationship_is_an_error():
    concepts = [_concept("a", "A")]
    related = RelationshipType.related_to.value
    relationships = [
        RelationshipDraft("a", "a", related, [{"p": 1}], 0.5, "unverified")
    ]
    report = validate_graph(concepts, relationships)
    assert any(issue.code == "self_relationship" for issue in report.issues)


def test_dangling_relationship_is_an_error():
    concepts = [_concept("a", "A")]
    related = RelationshipType.related_to.value
    relationships = [
        RelationshipDraft("a", "missing", related, [{"p": 1}], 0.5, "unverified")
    ]
    report = validate_graph(concepts, relationships)
    assert any(issue.code == "dangling_relationship" for issue in report.issues)


def test_source_grounded_claim_without_passage_is_flagged():
    concepts = [
        ConceptDraft(
            stable_key="a",
            canonical_name="A",
            claims=[ClaimDraft("A is a thing", "source_grounded", passage_id=None)],
        )
    ]
    report = validate_graph(concepts, [])
    assert any(issue.code == "missing_evidence" for issue in report.issues)


def test_snapshot_is_deterministic():
    concepts = [_concept("a", "A"), _concept("b", "B")]
    relationships = [RelationshipDraft("a", "b", "related_to", [{"p": 1}], 0.5, "unverified")]
    first = build_snapshot(concepts, relationships)
    second = build_snapshot(concepts, relationships)
    assert first == second
    assert first["schema_version"] == 1
