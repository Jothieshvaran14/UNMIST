"""RubricLens grading and sandbox tests."""

from __future__ import annotations

from app.models.learning import AssessmentAttempt, CriterionResult, Question, Rubric
from app.services.learning import checks as checks_service
from app.services.learning import grading, sandbox


def _question(session, prompt="What is 2+2?"):
    from _db_helpers import make_user
    from app.models.knowledge import Topic

    user = make_user(session, email="grading@example.com")
    topic = Topic(name="Arithmetic", slug="arithmetic")
    session.add(topic)
    session.flush()
    question = Question(
        topic_id=topic.id, prompt=prompt, question_type="short_answer", created_by=user.id
    )
    session.add(question)
    session.flush()
    return question


def _rubric(session, question, criteria):
    rubric = Rubric(question_id=question.id, version=1, criteria=criteria)
    session.add(rubric)
    session.flush()
    return rubric


def test_numeric_check_pass_and_fail():
    ok = checks_service.run_check("The answer is 4.", {"kind": "numeric", "expected": 4})
    assert ok.passed
    bad = checks_service.run_check("The answer is 5.", {"kind": "numeric", "expected": 4})
    assert not bad.passed


def test_exact_check_is_case_insensitive():
    ok = checks_service.run_check("  Paris ", {"kind": "exact", "expected": "paris"})
    assert ok.passed
    assert ok.evidence


def test_contains_all_and_any():
    assert checks_service.run_check(
        "It uses caching and indexing", {"kind": "contains_all", "all": ["caching", "indexing"]}
    ).passed
    assert not checks_service.run_check(
        "It uses caching only", {"kind": "contains_all", "all": ["caching", "indexing"]}
    ).passed
    assert checks_service.run_check(
        "It uses caching only", {"kind": "contains_any", "any": ["indexing", "caching"]}
    ).passed


def test_regex_check():
    assert checks_service.run_check(
        "Value: abc123", {"kind": "regex", "pattern": r"abc\d+"}
    ).passed
    assert not checks_service.run_check(
        "Value: xyz", {"kind": "regex", "pattern": r"abc\d+"}
    ).passed


def test_code_check_executes_sandbox():
    result = checks_service.run_check(
        "", {"kind": "code", "code": "print(6 * 7)", "expected_stdout": "42"}
    )
    assert result.passed, result.detail
    assert result.evidence.get("stdout", "").strip() == "42"


def test_code_check_detects_failure():
    result = checks_service.run_check("", {"kind": "code", "code": "print(1/0)"})
    assert not result.passed


def test_sandbox_rejects_dangerous_construct():
    result = sandbox.run_python("import os\nos.system('echo hi')")
    assert result.rejected


def test_prose_evidence_requires_review():
    result = checks_service.run_check(
        "Because of X and Y", {"kind": "prose_evidence", "required_evidence": ["X", "Y"]}
    )
    assert result.requires_review
    assert result.passed

    missing = checks_service.run_check(
        "Because of X", {"kind": "prose_evidence", "required_evidence": ["X", "Y"]}
    )
    assert not missing.passed
    assert missing.requires_review


def test_grade_response_awards_and_withholds_marks(db_session):
    question = _question(db_session)
    rubric = _rubric(
        db_session,
        question,
        [
            {"id": "c1", "description": "Correct value", "max_points": 3,
             "check": {"kind": "numeric", "expected": 4}},
            {"id": "c2", "description": "Mentions caching", "max_points": 2,
             "check": {"kind": "contains_all", "all": ["caching"]}},
        ],
    )
    from _db_helpers import make_user
    user = make_user(db_session, email="attempt@example.com")
    attempt = AssessmentAttempt(question_id=question.id, learner_id=user.id, status="in_progress")
    db_session.add(attempt)
    db_session.flush()

    grading.grade_response(
        db_session, attempt=attempt, rubric=rubric, response_text="It is 4."
    )
    assert attempt.score == 3
    assert attempt.max_score == 5
    assert attempt.status == "graded"
    assert attempt.evaluator_version == grading.EVALUATOR_VERSION
    results = {
        r.criterion_id: r
        for r in db_session.query(CriterionResult)
        .where(CriterionResult.attempt_id == attempt.id)
        .all()
    }
    assert results["c1"].points == 3
    assert results["c2"].points == 0
    assert results["c2"].explanation
