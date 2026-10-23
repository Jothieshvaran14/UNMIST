"""Reproducible OCR evaluation reports.

Design goals
------------
* **Machine-readable first.** :func:`build_report` returns a plain ``dict``
  that serialises to JSON; :func:`render_summary` turns it into a short human
  text. The JSON is the source of truth.
* **Reproducible.** The report records the exact evaluation configuration —
  normalization choices, provider/model identity, metric definitions, dataset
  identity and version, and dependency/runtime versions — so a number can be
  re-derived and compared later. Without that provenance a metric is folklore.
* **Never conflated.** A report built from the ``fake`` provider is stamped
  ``is_real_model: false``. Real-model accuracy and test-double output must not
  be presented side by side; :func:`render_summary` says so explicitly.

What this deliberately does *not* do
------------------------------------
It does not decide whether a CER is "good". There is no universal threshold,
and this checkpoint ran no lawful, representative benchmark, so no accuracy or
competitor-parity claim is made anywhere in this module.
"""

from __future__ import annotations

import platform
import sys
from datetime import datetime, timezone
from typing import Any, Optional

from .cer import cer, corpus_cer, edit_distance
from .wer import corpus_wer, tokenize_words, wer

# Version tag for the report schema itself, so consumers can branch on format.
REPORT_SCHEMA_VERSION = "ocr-eval-v1"


def _dependency_version(name: str) -> Optional[str]:
    """Best-effort installed version of an optional dependency."""
    try:
        from importlib.metadata import version

        return version(name)
    except Exception:  # noqa: BLE001 - diagnostics only, never fatal
        return None


def runtime_environment() -> dict[str, Any]:
    """Capture enough runtime detail to make a report attributable."""
    return {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "torch": _dependency_version("torch"),
        "transformers": _dependency_version("transformers"),
        "pillow": _dependency_version("pillow"),
        "numpy": _dependency_version("numpy"),
    }


def evaluate_pairs(
    pairs: list[tuple[str, str]],
    *,
    reference_of=None,
    hypothesis_of=None,
) -> dict[str, Any]:
    """Compute CER/WER over ``(reference, hypothesis)`` pairs.

    Returns aggregate metrics plus per-sample detail. Per-sample rows keep the
    edit distances so an aggregate can be audited rather than trusted blindly.
    """
    rows: list[dict[str, Any]] = []
    for reference, hypothesis in pairs:
        rows.append(
            {
                "reference_chars": len(reference or ""),
                "hypothesis_chars": len(hypothesis or ""),
                "char_edit_distance": edit_distance(reference or "", hypothesis or ""),
                "reference_words": len(tokenize_words(reference)),
                "hypothesis_words": len(tokenize_words(hypothesis)),
                "cer": round(cer(reference, hypothesis), 6),
                "wer": round(wer(reference, hypothesis), 6),
            }
        )
    return {
        "samples": len(pairs),
        "corpus_cer": round(corpus_cer(pairs), 6),
        "corpus_wer": round(corpus_wer(pairs), 6),
        # Macro averages are reported alongside micro for transparency; they
        # answer "how does a typical sample look" rather than "how much text
        # was wrong overall".
        "mean_sample_cer": round(
            sum(r["cer"] for r in rows) / len(rows), 6
        )
        if rows
        else 0.0,
        "mean_sample_wer": round(
            sum(r["wer"] for r in rows) / len(rows), 6
        )
        if rows
        else 0.0,
        "empty_references": sum(1 for ref, _ in pairs if not (ref or "").strip()),
        "empty_hypotheses": sum(1 for _, hyp in pairs if not (hyp or "").strip()),
        "detail": rows,
        "reference_of": reference_of,
        "hypothesis_of": hypothesis_of,
    }


def build_report(
    *,
    metrics: dict[str, Any],
    provider: str,
    model_id: str,
    dataset: Optional[dict[str, Any]] = None,
    configuration: Optional[dict[str, Any]] = None,
    operational: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Assemble a full, reproducible evaluation report.

    ``dataset`` should carry name, source, version, license/permissions,
    languages, and known limitations. ``configuration`` must record any text
    normalization applied before scoring, so the numbers can be reproduced.
    """
    is_real_model = provider not in {"fake", "none"}
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "provider": provider,
        "model_id": model_id,
        # Guards against presenting test-double output as model accuracy.
        "is_real_model": is_real_model,
        "metrics": metrics,
        "dataset": dataset or {},
        "configuration": configuration or {},
        "operational": operational or {},
        "runtime": runtime_environment(),
        # Stated so a single aggregate is never read as blanket proof of
        # quality across handwriting styles, languages, or layouts.
        "caveats": [
            "Metrics are only as lawful and representative as the dataset.",
            "A single aggregate does not characterize all handwriting styles.",
            "TrOCR is a line-level recognizer; these metrics do not measure "
            "page layout, question detection, or grading.",
        ],
    }


def render_summary(report: dict[str, Any]) -> str:
    """Short human-readable summary of a report.

    Explicitly flags non-real-model reports so fake-provider numbers can never
    be mistaken for measured accuracy.
    """
    metrics = report.get("metrics", {})
    lines = [
        f"OCR evaluation ({report.get('schema_version')})",
        f"  provider      : {report.get('provider')}",
        f"  model         : {report.get('model_id')}",
        f"  real model    : {report.get('is_real_model')}",
        f"  samples       : {metrics.get('samples')}",
        f"  corpus CER    : {metrics.get('corpus_cer')}",
        f"  corpus WER    : {metrics.get('corpus_wer')}",
        f"  mean sample CER: {metrics.get('mean_sample_cer')}",
        f"  empty refs    : {metrics.get('empty_references')}",
        f"  empty hyps    : {metrics.get('empty_hypotheses')}",
    ]
    if not report.get("is_real_model"):
        lines.append(
            "  NOTE: provider is a test double; these are NOT model accuracy numbers."
        )
    dataset = report.get("dataset") or {}
    if dataset.get("license"):
        lines.append(f"  dataset license: {dataset.get('license')}")
    return "\n".join(lines)
