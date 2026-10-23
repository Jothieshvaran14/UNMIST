/**
 * Explicit mappings from backend enum values to UI labels and tones.
 *
 * Every value here corresponds to a member of the enums defined in
 * `backend/app/models/documents.py`. Nothing is inferred: an unknown value
 * falls back to a neutral "Unknown" pill rather than being guessed at.
 *
 * OCR lifecycle (`DocumentProcessingState`) and segmentation lifecycle
 * (`SegmentationStatus`) are two independent axes and are always rendered as
 * two separate badges — segmentation completion is never inferred from OCR
 * completion.
 */

import type { ReactNode } from "react";
import { Badge, type BadgeTone } from "./ui";

export interface StatusInfo {
  label: string;
  tone: BadgeTone;
  /** True while work is in flight — drives pulse + polling. */
  active: boolean;
  /** Short user-facing explanation shown as a tooltip / caption. */
  hint?: string;
}

const UNKNOWN: StatusInfo = { label: "Unknown", tone: "neutral", active: false };

/* ---------------- Document processing (OCR axis) ------------------- */

const PROCESSING: Record<string, StatusInfo> = {
  uploaded: {
    label: "Uploaded",
    tone: "neutral",
    active: false,
    hint: "Stored and waiting to be scheduled for preprocessing.",
  },
  queued: {
    label: "Queued",
    tone: "info",
    active: true,
    hint: "A preprocessing job is waiting to be claimed by a worker.",
  },
  preprocessing: {
    label: "Preprocessing",
    tone: "info",
    active: true,
    hint: "Pages are being rasterized and normalized into working images.",
  },
  preprocessed: {
    label: "Pages ready",
    tone: "accent",
    active: false,
    hint: "Page preparation finished. No text recognition has run yet.",
  },
  ocr_processing: {
    label: "OCR processing",
    tone: "info",
    active: true,
    hint: "Text recognition is running over the prepared pages.",
  },
  review_required: {
    label: "Review required",
    tone: "warning",
    active: false,
    hint: "Recognition finished for some but not all pages; a human should look.",
  },
  completed: {
    label: "Processed",
    tone: "success",
    active: false,
    hint: "Every page completed recognition.",
  },
  failed: {
    label: "Failed",
    tone: "danger",
    active: false,
    hint: "Processing stopped with an error. The bounded category is shown below.",
  },
  cancelled: {
    label: "Cancelled",
    tone: "neutral",
    active: false,
    hint: "Processing was cancelled.",
  },
  deletion_pending: {
    label: "Deletion pending",
    tone: "warning",
    active: true,
    hint: "Deletion was requested; storage cleanup is still running.",
  },
  deleted: {
    label: "Deleted",
    tone: "neutral",
    active: false,
    hint: "Document and its generated artifacts were removed.",
  },
};

export function processingInfo(state: string | null | undefined): StatusInfo {
  if (!state) return UNKNOWN;
  return PROCESSING[state] ?? UNKNOWN;
}

/* ---------------- Segmentation axis ------------------------------- */

const SEGMENTATION: Record<string, StatusInfo> = {
  not_run: {
    label: "Not segmented",
    tone: "neutral",
    active: false,
    hint: "Structure extraction has not been requested for this document.",
  },
  scheduled: {
    label: "Segmentation queued",
    tone: "info",
    active: true,
    hint: "A segmentation job is waiting to be claimed by a worker.",
  },
  processing: {
    label: "Segmenting",
    tone: "info",
    active: true,
    hint: "Question/answer candidates are being derived from recognized regions.",
  },
  completed: {
    label: "Segmented",
    tone: "success",
    active: false,
    hint: "Every input region was placed into a segment.",
  },
  partial: {
    label: "Partially segmented",
    tone: "warning",
    active: false,
    hint: "Segmentation stopped partway; results are incomplete.",
  },
  failed: {
    label: "Segmentation failed",
    tone: "danger",
    active: false,
    hint: "The segmentation run failed. The bounded category is shown below.",
  },
  cancelled: {
    label: "Segmentation cancelled",
    tone: "neutral",
    active: false,
    hint: "Stopped because the document was cancelled or deleted.",
  },
};

export function segmentationInfo(status: string | null | undefined): StatusInfo {
  if (!status) return UNKNOWN;
  return SEGMENTATION[status] ?? UNKNOWN;
}

/* ---------------- Segment review axis ----------------------------- */

const REVIEW: Record<string, StatusInfo> = {
  pending: { label: "Pending review", tone: "warning", active: false },
  confirmed: { label: "Confirmed", tone: "success", active: false },
  corrected: { label: "Corrected", tone: "accent", active: false },
};

export function reviewInfo(status: string | null | undefined): StatusInfo {
  if (!status) return UNKNOWN;
  return REVIEW[status] ?? UNKNOWN;
}

/* ---------------- Segment type ------------------------------------ */

const SEGMENT_TYPES: Record<string, string> = {
  question: "Question",
  subpart: "Subpart",
  answer: "Answer",
  page_header: "Page header",
  page_footer: "Page footer",
  page_number: "Page number",
  diagram: "Diagram",
  equation: "Equation",
  table: "Table",
  unclassified: "Unclassified",
};

export function segmentTypeLabel(type: string): string {
  return SEGMENT_TYPES[type] ?? type;
}

const TYPE_TONES: Record<string, BadgeTone> = {
  question: "accent",
  answer: "info",
  subpart: "neutral",
  unclassified: "neutral",
};

export function segmentTypeTone(type: string): BadgeTone {
  return TYPE_TONES[type] ?? "neutral";
}

/**
 * Categorical uncertainty from `SegmentUncertainty` — shown as words only.
 * The backend deliberately returns a category, never a probability, so no
 * percentage is ever rendered here.
 */
const UNCERTAINTY: Record<string, { label: string; tone: BadgeTone }> = {
  certain: { label: "Certain", tone: "success" },
  likely: { label: "Likely", tone: "info" },
  uncertain: { label: "Uncertain", tone: "warning" },
  unknown: { label: "Uncertain", tone: "warning" },
};

export function uncertaintyInfo(value: string | null | undefined): {
  label: string;
  tone: BadgeTone;
} | null {
  if (!value) return null;
  return UNCERTAINTY[value] ?? { label: value, tone: "neutral" };
}

/* ---------------- Rendering helpers ------------------------------- */

export function StatusPill({
  info,
  showDot = true,
}: {
  info: StatusInfo;
  showDot?: boolean;
}) {
  return (
    <Badge tone={info.tone} pulse={info.active} dot={showDot} title={info.hint}>
      {info.label}
    </Badge>
  );
}

/** Pill that renders "Unknown" when segmentation status was not fetched. */
export function SegmentationPill({ status }: { status: string | null | undefined }) {
  return <StatusPill info={segmentationInfo(status ?? undefined)} />;
}

export function ProcessingPill({ state }: { state: string | null | undefined }) {
  return <StatusPill info={processingInfo(state)} />;
}

export function TypeBadge({ type }: { type: string }): ReactNode {
  return <Badge tone={segmentTypeTone(type)}>{segmentTypeLabel(type)}</Badge>;
}
