"""Reproducible OCR evaluation CLI. Runs without the API or a live server.

Reads a JSONL ground-truth file, runs the configured provider over the paired
images (or scores pre-computed hypotheses), and writes a machine-readable JSON
report plus a short human summary. Ordinary CI never invokes this; it is an
explicit, opt-in tool for measured evaluation.

Input format (one JSON object per line)::

    {"image": "path/to/page.png", "reference": "ground truth text"}
    {"reference": "text", "hypothesis": "already-recognized text"}

Either both ``image`` and ``reference`` (the provider is run on the image), or
both ``reference`` and ``hypothesis`` (pure scoring, no model) must be present.

Dataset provenance is *required* for real-model runs: pass ``--dataset-name``,
``--dataset-license``, etc. A run with a real provider and no recorded license
is refused, so accuracy numbers can never be produced without stating what lawfully
permits their use.

Examples::

    # Score pre-computed hypotheses (no model, no network):
    python scripts/evaluate_ocr.py --pairs pairs.jsonl --output report.json

    # Run the configured provider over images (needs the `ocr` extra):
    python scripts/evaluate_ocr.py --pairs pairs.jsonl --output report.json \\
        --dataset-name IAM --dataset-license "research-only; verify terms"
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Allow running as a plain script without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.services.evaluation import (  # noqa: E402
    build_report,
    evaluate_pairs,
    render_summary,
)


def _load_pairs(path: Path) -> list[dict]:
    rows: list[dict] = []
    # utf-8-sig tolerates a BOM, which Windows editors and PowerShell commonly
    # prepend; without it a valid JSONL file fails to parse on the first line.
    with path.open("r", encoding="utf-8-sig") as handle:
        for lineno, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise SystemExit(f"{path}:{lineno}: invalid JSON: {exc}") from exc
    return rows


def _recognize_image(image_path: Path, provider, limits) -> str:
    """Run the provider on one image file and join its regions in order."""
    from app.services.documents.ocr.base import OcrPageInput
    from PIL import Image

    data = image_path.read_bytes()
    with Image.open(image_path) as img:
        width, height = img.size

    import hashlib

    page = OcrPageInput(
        document_id="evaluation",
        page_id=image_path.stem,
        page_number=1,
        image_png=data,
        width_px=width,
        height_px=height,
        # Evaluation images are already in original space.
        working_scale=1.0,
        content_hash=hashlib.sha256(data).hexdigest(),
    )
    result = provider.recognize(page, limits=limits)
    ordered = sorted(result.regions, key=lambda r: (r.reading_order or 0))
    return "\n".join(r.text or "" for r in ordered)


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate OCR quality reproducibly")
    parser.add_argument("--pairs", required=True, help="JSONL ground-truth file")
    parser.add_argument("--output", required=True, help="Path for the JSON report")
    parser.add_argument("--provider", default=None, help="Override OCR_PROVIDER")
    parser.add_argument("--dataset-name", default=None)
    parser.add_argument("--dataset-license", default=None)
    parser.add_argument("--dataset-version", default=None)
    parser.add_argument("--dataset-source", default=None)
    parser.add_argument("--dataset-languages", default=None)
    parser.add_argument("--dataset-limitations", default=None)
    args = parser.parse_args()

    rows = _load_pairs(Path(args.pairs))
    if not rows:
        print("No pairs found in input file.", file=sys.stderr)
        return 1

    from app.config import get_settings
    from app.services.documents.ocr.base import OcrLimits
    from app.services.documents.ocr.registry import get_ocr_provider

    settings = get_settings()
    provider_name = (args.provider or settings.ocr_provider).strip().lower()
    is_real_model = provider_name not in {"fake", "none"}

    dataset = {
        "name": args.dataset_name,
        "license": args.dataset_license,
        "version": args.dataset_version,
        "source": args.dataset_source,
        "languages": args.dataset_languages,
        "limitations": args.dataset_limitations,
    }
    # Refuse to emit real-model accuracy without stated dataset permissions.
    if is_real_model and not args.dataset_license:
        print(
            "Refusing to evaluate a real model without --dataset-license: "
            "accuracy numbers must record what lawfully permits their use.",
            file=sys.stderr,
        )
        return 2

    needs_model = any("image" in row for row in rows)
    provider = None
    limits = None
    if needs_model:
        provider = get_ocr_provider(settings, name=provider_name)
        limits = OcrLimits.from_settings(settings)

    pairs: list[tuple[str, str]] = []
    for row in rows:
        reference = row.get("reference", "")
        if "hypothesis" in row:
            hypothesis = row["hypothesis"]
        elif "image" in row and provider is not None and limits is not None:
            hypothesis = _recognize_image(Path(row["image"]), provider, limits)
        else:
            print(
                f"Row lacks both 'hypothesis' and a usable 'image': {row}",
                file=sys.stderr,
            )
            return 1
        pairs.append((reference, hypothesis))

    metrics = evaluate_pairs(pairs)
    report = build_report(
        metrics=metrics,
        provider=provider_name,
        model_id=getattr(provider, "model_id", provider_name) if provider else provider_name,
        dataset=dataset,
        configuration={
            "normalization": "none applied before scoring",
            "tokenization": "whitespace split (see app.services.evaluation.wer)",
        },
    )

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(render_summary(report))
    print(f"\nReport written to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
