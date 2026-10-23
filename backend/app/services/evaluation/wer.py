"""Word Error Rate (WER).

WER is the word-level analogue of CER: Levenshtein distance over *words*
divided by the number of reference words. It is more sensitive to word-boundary
mistakes (splitting/merging tokens) than CER, which is why both are reported
together — a model can have low CER and poor WER if it glues words together.

Tokenization (documented, because it changes reported numbers)
-------------------------------------------------------------
:func:`tokenize_words` splits on any run of whitespace and drops empty tokens.
It deliberately performs **no** case folding, punctuation stripping, or Unicode
normalization. Those are choices that must be made explicitly by the caller and
recorded in the evaluation configuration (see ``report.py``); hiding them here
would make two reports silently incomparable.

Empty-reference policy matches CER (see ``cer.py``): empty+empty -> 0.0,
empty reference with non-empty hypothesis -> 1.0, otherwise
``distance / reference_word_count``.
"""

from __future__ import annotations

from .cer import edit_distance


def tokenize_words(text: str | None) -> list[str]:
    """Whitespace-tokenize for WER.

    Split on any whitespace run, dropping empty tokens. No case folding, no
    punctuation removal, no normalization — those are caller choices that must
    be recorded in the evaluation configuration, not hidden here.
    """
    if not text:
        return []
    return text.split()


def wer(reference: str | None, hypothesis: str | None) -> float:
    """Word Error Rate for one reference/hypothesis pair.

    ``None`` is treated as the empty string, consistent with :func:`cer.cer`.
    """
    ref = tokenize_words(reference)
    hyp = tokenize_words(hypothesis)
    if not ref:
        return 0.0 if not hyp else 1.0
    # Join back to strings with a single-space separator so the shared,
    # character-level edit distance operates on a canonical word sequence.
    return edit_distance(" ".join(ref), " ".join(hyp)) / len(ref)


def corpus_wer(pairs: list[tuple[str, str]]) -> float:
    """Micro-averaged WER over a corpus.

    Sums word-level edit distances and reference word counts before dividing,
    so longer samples weigh more (the conventional corpus WER, not the mean of
    per-sample rates). An entirely empty corpus reports 0.0.
    """
    total_distance = 0
    total_words = 0
    for reference, hypothesis in pairs:
        ref = tokenize_words(reference)
        hyp = tokenize_words(hypothesis)
        if not ref:
            total_distance += 0 if not hyp else len(hyp)
        else:
            total_distance += edit_distance(" ".join(ref), " ".join(hyp))
        total_words += len(ref)
    if total_words == 0:
        return 0.0 if total_distance == 0 else 1.0
    return total_distance / total_words
