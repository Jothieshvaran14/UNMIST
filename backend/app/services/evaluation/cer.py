"""Character Error Rate (CER) and Word Error Rate (WER).

Both are edit-distance ratios computed with Levenshtein distance, which counts
insertions, deletions, and substitutions — the standard OCR/HTR error metrics.

Empty-reference policy (explicit, because it materially changes reported
numbers and must never be hidden):

  * reference empty **and** hypothesis empty -> rate ``0.0`` (both agree there
    was nothing to read).
  * reference empty, hypothesis non-empty   -> rate ``1.0`` (every hypothesis
    character/word is an insertion; the reference had none).
  * reference non-empty                     -> ``distance / len(reference)``,
    which can exceed 1.0 when the hypothesis is much longer. That is correct
    and is reported as-is rather than clipped, so an over-generating model is
    visibly penalized.

Normalisation is deliberately *not* applied here. Callers that want case
folding, punctuation stripping, or whitespace squashing must do it explicitly
before calling, and record that choice in the evaluation configuration — see
``report.py``. Silent normalisation makes two reported numbers incomparable.
"""

from __future__ import annotations


def edit_distance(reference: str, hypothesis: str) -> int:
    """Levenshtein distance between two strings.

    Uses a single rolling row of the DP table: O(min(len(ref), len(hyp)))
    space, O(len(ref) * len(hyp)) time. Reference and hypothesis are treated
    as sequences of Unicode code points, so no encoding assumption is made.
    """
    if reference == hypothesis:
        return 0
    if not reference:
        return len(hypothesis)
    if not hypothesis:
        return len(reference)

    # Ensure the inner loop runs over the shorter sequence.
    if len(reference) < len(hypothesis):
        reference, hypothesis = hypothesis, reference

    previous = list(range(len(hypothesis) + 1))
    for i, ref_char in enumerate(reference, start=1):
        current = [i]
        for j, hyp_char in enumerate(hypothesis, start=1):
            substitution = previous[j - 1] + (0 if ref_char == hyp_char else 1)
            insertion = current[j - 1] + 1
            deletion = previous[j] + 1
            current.append(min(substitution, insertion, deletion))
        previous = current
    return previous[-1]


def cer(reference: str | None, hypothesis: str | None) -> float:
    """Character Error Rate for one reference/hypothesis pair.

    See the module docstring for the empty-reference policy. ``None`` is
    treated as the empty string, so "no reference" and "empty reference" are
    reported consistently rather than raising.
    """
    ref = reference or ""
    hyp = hypothesis or ""
    if not ref:
        return 0.0 if not hyp else 1.0
    return edit_distance(ref, hyp) / len(ref)


def corpus_cer(pairs: list[tuple[str, str]]) -> float:
    """Micro-averaged CER over a corpus.

    Sums edit distances and reference lengths *before* dividing, so long
    samples weigh more than short ones. This is the conventional corpus CER
    and is not the mean of per-sample rates (which would be macro-averaged
    and give a 3-character sample the same weight as a 300-character one).
    An entirely empty corpus reports 0.0.
    """
    total_distance = 0
    total_length = 0
    for reference, hypothesis in pairs:
        ref = reference or ""
        hyp = hypothesis or ""
        total_distance += 0 if not ref and not hyp else (
            len(hyp) if not ref else edit_distance(ref, hyp)
        )
        total_length += len(ref)
    if total_length == 0:
        # Every reference was empty: report 0.0 only if nothing was hallucinated.
        return 0.0 if total_distance == 0 else 1.0
    return total_distance / total_length
