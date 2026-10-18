"""Deterministic, criterion-level answer checking.

Every check returns a structured result with an evidence span so that grading
decisions are explainable and auditable. Model-based judgment is not used here;
where a criterion requires subjective judgment, the checker flags the criterion
for human review rather than guessing.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from . import sandbox


@dataclass(frozen=True)
class CheckResult:
    passed: bool
    detail: str
    evidence: dict = field(default_factory=dict)
    requires_review: bool = False
    confidence: float = 1.0


def _normalize(text: str) -> str:
    return " ".join(text.split()).strip()


def _find_span(response: str, needle: str) -> dict:
    index = response.lower().find(needle.lower())
    if index < 0:
        return {}
    return {"start": index, "end": index + len(needle), "text": response[index:index + len(needle)]}


def run_check(response: str, check: dict) -> CheckResult:
    kind = check.get("kind")
    handler = _CHECKERS.get(kind) if isinstance(kind, str) else None
    if handler is None:
        return CheckResult(
            passed=False,
            detail=f"Unknown check kind '{kind}'",
            requires_review=True,
            confidence=0.0,
        )
    return handler(response, check)


def _check_numeric(response: str, check: dict) -> CheckResult:
    expected = check.get("expected")
    tolerance = float(check.get("tolerance", 1e-6))
    if expected is None:
        return CheckResult(False, "Numeric check missing 'expected'", requires_review=True)
    numbers = [
        float(value)
        for value in re.findall(r"-?\d+(?:\.\d+)?", response)
    ]
    if not numbers:
        return CheckResult(False, "No numeric value found in the response", confidence=0.6)
    closest = min(numbers, key=lambda value: abs(value - float(expected)))
    passed = math.isclose(closest, float(expected), rel_tol=tolerance, abs_tol=tolerance)
    return CheckResult(
        passed=passed,
        detail=f"Closest value {closest} vs expected {expected}",
        evidence=_find_span(response, str(closest)) if passed else {},
        confidence=0.9,
    )


def _check_exact(response: str, check: dict) -> CheckResult:
    expected = check.get("expected")
    if expected is None:
        return CheckResult(False, "Exact check missing 'expected'", requires_review=True)
    passed = _normalize(response).lower() == _normalize(str(expected)).lower()
    return CheckResult(
        passed=passed,
        detail="Exact match" if passed else "Response does not exactly match",
        evidence=_find_span(response, str(expected)) if passed else {},
    )


def _check_contains_all(response: str, check: dict) -> CheckResult:
    required = check.get("all", []) or []
    missing = [item for item in required if item.lower() not in response.lower()]
    evidence = {}
    for item in required:
        span = _find_span(response, item)
        if span:
            evidence[item] = span
    passed = not missing
    return CheckResult(
        passed=passed,
        detail=f"Missing: {missing}" if missing else "All required elements present",
        evidence=evidence,
    )


def _check_contains_any(response: str, check: dict) -> CheckResult:
    options = check.get("any", []) or []
    for option in options:
        span = _find_span(response, option)
        if span:
            return CheckResult(True, f"Matched alternative '{option}'", evidence={option: span})
    return CheckResult(False, "None of the acceptable alternatives were found", confidence=0.7)


def _check_regex(response: str, check: dict) -> CheckResult:
    pattern = check.get("pattern")
    if not pattern:
        return CheckResult(False, "Regex check missing 'pattern'", requires_review=True)
    try:
        match = re.search(pattern, response, re.MULTILINE)
    except re.error:
        return CheckResult(False, "Invalid regex pattern", requires_review=True, confidence=0.0)
    if match:
        return CheckResult(
            True,
            f"Matched pattern: {match.group(0)!r}",
            evidence={"start": match.start(), "end": match.end(), "text": match.group(0)},
        )
    return CheckResult(False, "Response did not match the required pattern", confidence=0.8)


def _check_code(response: str, check: dict) -> CheckResult:
    expected_stdout = check.get("expected_stdout")
    code = check.get("code") or response
    result = sandbox.run_python(code, timeout_seconds=float(check.get("timeout_seconds", 2)))
    if result.rejected:
        return CheckResult(
            False,
            f"Code rejected: {result.rejection_reason}",
            requires_review=True,
            confidence=0.0,
        )
    if result.timed_out:
        return CheckResult(False, "Code execution timed out", requires_review=True, confidence=0.5)
    if result.exit_code != 0:
        return CheckResult(
            False,
            f"Code exited with {result.exit_code}: {result.stderr[:200]}",
            confidence=0.7,
        )
    if expected_stdout is not None:
        passed = _normalize(result.stdout) == _normalize(str(expected_stdout))
        return CheckResult(
            passed=passed,
            detail=f"stdout={result.stdout!r}",
            evidence={"stdout": result.stdout[:500]} if passed else {},
            confidence=0.9,
        )
    return CheckResult(True, "Code executed successfully", evidence={"stdout": result.stdout[:500]})


def _check_prose_evidence(response: str, check: dict) -> CheckResult:
    """Deterministic evidence-presence check for essay-style answers.

    This does not judge correctness of reasoning; it verifies that required
    evidence markers are present and always flags the criterion for review.
    """
    required = check.get("required_evidence", []) or []
    present = [item for item in required if item.lower() in response.lower()]
    missing = [item for item in required if item not in present]
    evidence = {item: _find_span(response, item) for item in present}
    if required and missing:
        return CheckResult(
            False,
            f"Missing required evidence: {missing}",
            evidence=evidence,
            requires_review=True,
            confidence=0.6,
        )
    return CheckResult(
        True,
        "Required evidence markers present; human review recommended",
        evidence=evidence,
        requires_review=True,
        confidence=0.7,
    )


_CHECKERS = {
    "numeric": _check_numeric,
    "exact": _check_exact,
    "contains_all": _check_contains_all,
    "contains_any": _check_contains_any,
    "regex": _check_regex,
    "code": _check_code,
    "prose_evidence": _check_prose_evidence,
}
