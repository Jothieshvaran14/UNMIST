/**
 * Assessments.
 *
 * Connected to real data: `GET /v1/me/profile` returns the learner's
 * `mastery`, `assessment_history` and `diagnoses`. `mastery_probability` is a
 * value the backend's spaced-repetition model produced — it is shown with its
 * model version and evidence count, and labelled as model output rather than a
 * guarantee. Nothing here is generated client-side.
 *
 * Gaps are stated rather than papered over: the API exposes no question
 * catalogue, so the UI cannot start a new attempt.
 */

import { Link } from "react-router-dom";
import { Award, Brain, ClipboardList, RefreshCw, Stethoscope } from "lucide-react";
import { useAsyncData } from "@/hooks/useAsyncData";
import { Badge, Button, Card, EmptyState, ErrorState, LoadingRow, ProgressBar } from "@/components/ui";
import * as api from "@/services/api";
import type { ProfileMasteryEntry } from "@/types/api";

function formatPercent(value: number): string {
  if (!Number.isFinite(value)) return "—";
  return `${Math.round(value * 100)}%`;
}

function formatDate(value: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleString();
}

export function AssessmentsPage() {
  const profile = useAsyncData((signal) => api.getMyProfile(signal), []);

  if (profile.initialLoading) {
    return (
      <div className="card card--pad">
        <LoadingRow label="Loading assessment data…" />
      </div>
    );
  }

  if (profile.error && !profile.data) {
    return (
      <>
        <Header onRetry={profile.reload} refreshing={profile.refreshing} />
        <div className="card card--pad">
          <ErrorState error={profile.error} onRetry={profile.reload} />
        </div>
      </>
    );
  }

  const data = profile.data;
  const mastery = data?.mastery ?? [];
  const attempts = data?.assessment_history ?? [];
  const gaps = data?.prerequisite_gaps ?? [];
  const diagnoses = data?.diagnoses ?? [];
  const submitted = attempts.filter((attempt) => attempt.status === "submitted");

  return (
    <>
      <Header onRetry={profile.reload} refreshing={profile.refreshing} />

      <div className="grid grid--3" style={{ marginBottom: "var(--space-5)" }}>
        <div className="stat">
          <div className="stat__label">
            <Brain />
            Concepts tracked
          </div>
          <div className="stat__value">{mastery.length}</div>
          <div className="stat__note">From GET /v1/me/profile</div>
        </div>
        <div className="stat">
          <div className="stat__label">
            <ClipboardList />
            Attempts
          </div>
          <div className="stat__value">{attempts.length}</div>
          <div className="stat__note">{submitted.length} submitted</div>
        </div>
        <div className="stat">
          <div className="stat__label">
            <Stethoscope />
            Diagnoses
          </div>
          <div className="stat__value">{diagnoses.length}</div>
          <div className="stat__note">Recorded learning diagnoses</div>
        </div>
      </div>

      <div className="grid grid--2">
        <Card title="Concept mastery" icon={<Brain />}>
          {mastery.length === 0 ? (
            <EmptyState
              icon={<Brain />}
              title="No mastery estimates yet"
              description="The model produces an estimate only after assessment evidence has been recorded for a concept."
            />
          ) : (
            <ul className="stack gap-4">
              {mastery.slice(0, 12).map((entry: ProfileMasteryEntry) => (
                <li key={entry.concept_id} className="stack gap-2">
                  <div className="row gap-3">
                    <span className="grow truncate" style={{ fontWeight: 500 }}>
                      {entry.canonical_name}
                    </span>
                    <Badge tone="neutral">{entry.state}</Badge>
                    <span className="mono">{formatPercent(entry.mastery_probability)}</span>
                  </div>
                  <ProgressBar
                    value={entry.mastery_probability * 100}
                    label={`Modelled mastery for ${entry.canonical_name}`}
                  />
                  <div className="text-xs text-muted">
                    {entry.evidence_count} evidence item{entry.evidence_count === 1 ? "" : "s"} ·{" "}
                    {entry.model_version} · last activity {formatDate(entry.last_activity_at)}
                  </div>
                </li>
              ))}
            </ul>
          )}
          {mastery.length > 12 && (
            <p className="text-xs text-muted mt-4">Showing 12 of {mastery.length} concepts.</p>
          )}
          <p className="text-xs text-muted mt-4">
            Values are produced by the backend model from recorded evidence. They are not a
            guarantee of correctness or of exam performance.
          </p>
        </Card>

        <Card title="Attempt history" icon={<ClipboardList />} pad={false}>
          {attempts.length === 0 ? (
            <EmptyState
              icon={<ClipboardList />}
              title="No attempts recorded"
              description="Attempts appear here after an assessment is submitted through the API."
            />
          ) : (
            <div className="table-wrap">
              <table className="table">
                <caption className="sr-only">Recorded assessment attempts</caption>
                <thead>
                  <tr>
                    <th scope="col">Question</th>
                    <th scope="col">Status</th>
                    <th scope="col">Score</th>
                    <th scope="col">Submitted</th>
                  </tr>
                </thead>
                <tbody>
                  {attempts.slice(0, 20).map((attempt) => (
                    <tr key={attempt.attempt_id}>
                      <td className="mono text-xs">{attempt.question_id.slice(0, 8)}…</td>
                      <td>
                        <Badge
                          tone={
                            attempt.status === "submitted"
                              ? "success"
                              : attempt.status === "graded"
                                ? "accent"
                                : attempt.status === "expired"
                                  ? "neutral"
                                  : "info"
                          }
                        >
                          {attempt.status}
                        </Badge>
                      </td>
                      <td className="text-sm">
                        {attempt.score !== null && attempt.max_score !== null
                          ? `${attempt.score} / ${attempt.max_score}`
                          : attempt.score !== null
                            ? String(attempt.score)
                            : "—"}
                      </td>
                      <td className="text-xs text-muted">{formatDate(attempt.submitted_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <footer className="card__foot text-xs text-muted">
            The API has no question-catalogue endpoint, so the UI cannot offer “start a new
            assessment” — that action is omitted rather than shown as a dead button.
          </footer>
        </Card>
      </div>

      {gaps.length > 0 && (
        <div className="mt-6">
          <Card title="Concepts below the model threshold" icon={<Award />}>
            <div className="row gap-2 wrap">
              {gaps.slice(0, 20).map((entry) => (
                <Badge key={entry.concept_id} tone="warning">
                  {entry.canonical_name} · {formatPercent(entry.mastery_probability)}
                </Badge>
              ))}
            </div>
            <p className="text-xs text-muted mt-3">
              Threshold applied by the backend when flagging a concept for attention.
            </p>
          </Card>
        </div>
      )}
    </>
  );
}

function Header({ onRetry, refreshing }: { onRetry: () => void; refreshing: boolean }) {
  return (
    <div className="page-head">
      <div className="page-head__title">
        <span className="page-head__eyebrow">Learning</span>
        <h1>Assessments</h1>
        <p className="page-head__desc">
          Modelled mastery and recorded attempt history for your account. Every value is read from
          the API — nothing is estimated in the browser.
        </p>
      </div>
      <div className="page-head__actions">
        <Button variant="ghost" onClick={onRetry} icon={<RefreshCw />} disabled={refreshing}>
          Refresh
        </Button>
        <Link to="/documents">
          <Button variant="secondary">Go to documents</Button>
        </Link>
      </div>
    </div>
  );
}
