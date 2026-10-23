"""Reproducible OCR evaluation metrics and reporting (Checkpoint D).

This package is deliberately independent of the production API and of any
running server: it operates on ``(reference, hypothesis)`` text pairs, so an
evaluation can be run from a script or a notebook against a lawful dataset
without touching live documents.

Modules
-------
``cer``     Character Error Rate (Levenshtein-based), plus shared edit distance
``wer``     Word Error Rate, with documented whitespace tokenization
``report``  Machine-readable JSON reports with full configuration provenance

Honesty rules
-------------
* Normalization is never applied silently. Any folding/stripping must be done
  by the caller and recorded in the report's ``configuration``.
* Reports built from the ``fake`` provider are stamped ``is_real_model:
  false`` and rendered with an explicit warning, so test-double output is
  never presented as measured accuracy.
* No accuracy or competitor-parity claim is made from these metrics alone;
  they are only meaningful against a lawful, representative ground-truth
  dataset, whose provenance must be recorded in the report.
"""

from __future__ import annotations

from .cer import cer, corpus_cer, edit_distance
from .report import (
    REPORT_SCHEMA_VERSION,
    build_report,
    evaluate_pairs,
    render_summary,
    runtime_environment,
)
from .wer import corpus_wer, tokenize_words, wer

__all__ = [
    "REPORT_SCHEMA_VERSION",
    "build_report",
    "cer",
    "corpus_cer",
    "corpus_wer",
    "edit_distance",
    "evaluate_pairs",
    "render_summary",
    "runtime_environment",
    "tokenize_words",
    "wer",
]
