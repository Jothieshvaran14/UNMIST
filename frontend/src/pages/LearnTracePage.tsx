/**
 * LearnTrace — prerequisite-gap diagnosis.
 *
 * Connected to real data:
 *   - `GET /v1/me/profile`            -> mastery estimates, prior diagnoses
 *   - `POST /v1/learners/me/diagnoses` -> runs the real diagnosis service
 *
 * Epistemic discipline (product boundary):
 *   - `mastery_probability` is a *provisional model estimate*, shown with its
 *     `model_version` and `evidence_count` — never as an established fact.
 *   - Hypotheses are machine-generated from recorded prerequisite links and
 *     are labelled as such; `likely_gap` is the server's flag, not ours.
 *   - `insufficient_evidence` is a real outcome and is shown as such — the
 *     page never invents a diagnosis when the service declines to give one.
 *   - Nothing is computed client-side: no probabilities, no rankings, no
 *     recommendations beyond what the server returned.
 */

import { useState } from "react";
import { Activity, ArrowRight, Search, Stethoscope } from "lucide-react";
import { useAsyncData } from "@/hooks/useAsyncData";
import {
  Alert,
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorState,
  LoadingRow,
} from "@/components/ui";
import * as api from "@/services/api";
import type { Diagnosis, ProfileMasteryEntry } from "@/types/api";

function pct(value: number): string {
  return `${Math.round(value * 100)}%`;
}

function formatDate(value: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? "—"
    : date.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

const STATUS_TONES: Record<string, "success" | "warning" | "info" | "neutral" | "danger"> = {
  insufficient_evidence: "warning",
  completed: "success",
  pending: "info",
  running: "info",
};

export function LearnTracePage() {
  const profile = useAsyncData((signal) => api.getMyProfile(signal), []);
  const [selected, setSelected] = useState<string | null>(null);
  const [diagnosis, setDiagnosis] = useState<Diagnosis | null>(null);
  const [running, setRunning] = useState(false);
  const [runError, setRunError] = useState<unknown>(null);

  const mastery: ProfileMasteryEntry[] = profile.data?.mastery ?? [];
  const gaps = new Set((profile.data?.prerequisite_gaps ?? []).map((g) => g.concept_id));
  const history = profile.data?.diagnoses ?? [];

  const active: ProfileMasteryEntry | undefined = mastery.find(
    (entry) => entry.concept_id === selected,
  );

  const runFor = async (conceptId: string) => {
    setRunning(true);
    setRunError(null);
    setDiagnosis(null);
    try {
      const result = await api.runDiagnosis({ concept_id: conceptId });
      setDiagnosis(result);
      profile.reload();
    } catch (error: unknown) {
      setRunError(error);
    } finally {
      setRunning(false);
    }
  };

  if (profile.initialLoading) {
    return (
      <div className="card card--pad">
        <LoadingRow label="Loading mastery evidence…" />
      </div>
    );
  }

  return (
    <>
      <div className="page-head">
        <div className="page-head__title">
          <span className="page-head__eyebrow">Learning</span>
          <h1>LearnTrace</h1>
          <p className="page-head__desc">
            Prerequisite reasoning over your recorded mastery evidence. Each diagnosis builds
            competing hypotheses from the graph's prerequisite links — a machine suggestion you
            can inspect, not a verdict about what you know.
          </p>
        </div>
        <div className="page-head__actions">
          <Button
            variant="ghost"
            icon={<Activity />}
            onClick={profile.reload}
            disabled={profile.refreshing}
          >
            Refresh
          </Button>
        </div>
      </div>

      {profile.error && !profile.data && (
        <div className="card card--pad" style={{ marginBottom: "var(--space-5)" }}>
          <ErrorState error={profile.error} onRetry={profile.reload} compact />
        </div>
      )}

      <div className="split">
        <Card title="Concepts with evidence" icon={<Stethoscope />} pad={false}>
          {mastery.length === 0 ? (
            <EmptyState
              icon={<Stethoscope />}
              title="No mastery evidence yet"
              description="LearnTrace diagnoses the concepts you have evidence for. Evidence accrues through assessments and knowledge extraction — start by uploading a document and building a topic."
            />
          ) : (
            <ul>
              {mastery.map((entry) => (
                <li key={entry.concept_id}>
                  <button
                    className={`seg-item ${selected === entry.concept_id ? "is-selected" : ""}`}
                    style={{ width: "100%", textAlign: "left", border: "none", background: undefined }}
                    onClick={() => {
                      setSelected(entry.concept_id);
                      setDiagnosis(null);
                      setRunError(null);
                    }}
                  >
                    <span className="seg-item__head">
                      <span className="seg-item__label">{entry.canonical_name}</span>
                      {gaps.has(entry.concept_id) && (
                        <Badge tone="warning" title="Server flag: provisional mastery below the profile threshold">
                          low evidence
                        </Badge>
                      )}
                    </span>
                    <span className="seg-item__text">
                      Provisional {pct(entry.mastery_probability)} · {entry.evidence_count} evidence{" "}
                      · {entry.model_version}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
          <footer className="card__foot text-xs text-muted">
            Probabilities are uncalibrated model estimates (<span className="mono">provisional</span>{" "}
            states), not established mastery.
          </footer>
        </Card>

        <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-5)" }}>
          {!selected ? (
            <Card title="Diagnosis" icon={<Search />}>
              <EmptyState
                icon={<Search />}
                title="Select a concept"
                description="Choose a concept on the left to run a prerequisite-gap diagnosis against your recorded evidence."
              />
            </Card>
          ) : (
            <Card
              title={active ? `Diagnosis · ${active.canonical_name}` : "Diagnosis"}
              icon={<Search />}
              action={
                <Button
                  variant="primary"
                  icon={<ArrowRight />}
                  onClick={() => runFor(selected)}
                  disabled={running}
                >
                  {running ? "Running…" : "Run diagnosis"}
                </Button>
              }
            >
              {runError != null && (
                <div style={{ marginBottom: "var(--space-4)" }}>
                  <Alert tone="danger" title="Diagnosis could not be run">
                    The service refused this request. This usually means the concept has no
                    recorded graph to reason over.
                  </Alert>
                </div>
              )}

              {diagnosis && (
                <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}>
                  <div className="row gap-3" style={{ alignItems: "center", flexWrap: "wrap" }}>
                    <Badge tone={STATUS_TONES[diagnosis.status] ?? "neutral"}>
                      {diagnosis.status.replace(/_/g, " ")}
                    </Badge>
                    <span className="text-xs text-muted">
                      {formatDate(diagnosis.created_at)} · diagnosis {diagnosis.id.slice(0, 8)}
                    </span>
                  </div>

                  {diagnosis.status === "insufficient_evidence" && (
                    <Alert tone="warning" title="Insufficient evidence — no gap asserted">
                      The service found no prerequisite link it could reason about with the
                      evidence on record. Nothing is inferred here.
                    </Alert>
                  )}

                  {diagnosis.evidence_summary && (
                    <div>
                      <div className="text-xs text-muted" style={{ marginBottom: 4 }}>
                        Evidence summary (server-generated)
                      </div>
                      <div className="reading">{diagnosis.evidence_summary}</div>
                    </div>
                  )}

                  {diagnosis.conclusion && (
                    <div>
                      <div className="text-xs text-muted" style={{ marginBottom: 4 }}>
                        Conclusion
                      </div>
                      <div className="reading">{diagnosis.conclusion}</div>
                    </div>
                  )}

                  {diagnosis.hypotheses.length > 0 && (
                    <div>
                      <div className="row gap-2" style={{ alignItems: "center", marginBottom: 8 }}>
                        <span className="prov prov--suggestion">Machine hypotheses</span>
                        <span className="text-xs text-muted">
                          prerequisite links, ordered by the server
                        </span>
                      </div>
                      <div className="table-wrap">
                        <table className="table">
                          <caption className="sr-only">Diagnosis hypotheses</caption>
                          <thead>
                            <tr>
                              <th scope="col">Prerequisite</th>
                              <th scope="col">Estimate</th>
                              <th scope="col">Evidence</th>
                              <th scope="col">Server flag</th>
                            </tr>
                          </thead>
                          <tbody>
                            {diagnosis.hypotheses.map((hypothesis) => (
                              <tr key={hypothesis.concept_id}>
                                <td>{hypothesis.canonical_name}</td>
                                <td className="text-sm">{pct(hypothesis.mastery_probability)}</td>
                                <td className="text-sm">{hypothesis.evidence_count}</td>
                                <td>
                                  {hypothesis.likely_gap ? (
                                    <Badge tone="warning">likely gap</Badge>
                                  ) : (
                                    <Badge tone="neutral">no flag</Badge>
                                  )}
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </div>
                  )}

                  {diagnosis.selected_question_id && (
                    <div className="text-xs text-muted">
                      Suggested next probe: an unattempted question on this topic is recorded as{" "}
                      <span className="mono">{diagnosis.selected_question_id.slice(0, 8)}</span>.
                    </div>
                  )}
                </div>
              )}

              {!diagnosis && !runError && !running && (
                <p className="text-sm text-secondary">
                  Running a diagnosis reads the recorded prerequisite links for this concept and
                  the mastery evidence attached to each one. The result names competing
                  hypotheses and the evidence behind them.
                </p>
              )}
            </Card>
          )}

          <Card title="Diagnosis history" icon={<Activity />} pad={false}>
            {history.length === 0 ? (
              <EmptyState
                icon={<Activity />}
                title="No diagnoses yet"
                description="Diagnoses you run appear here with their server-recorded status and conclusion."
              />
            ) : (
              <div className="table-wrap">
                <table className="table">
                  <caption className="sr-only">Prior diagnoses</caption>
                  <thead>
                    <tr>
                      <th scope="col">Concept</th>
                      <th scope="col">Status</th>
                      <th scope="col">Conclusion</th>
                      <th scope="col">Date</th>
                    </tr>
                  </thead>
                  <tbody>
                    {history.map((entry) => {
                      const concept = mastery.find((m) => m.concept_id === entry.concept_id);
                      return (
                        <tr key={entry.diagnosis_id}>
                          <td>{concept?.canonical_name ?? entry.concept_id.slice(0, 8)}</td>
                          <td>
                            <Badge tone={STATUS_TONES[entry.status] ?? "neutral"}>
                              {entry.status.replace(/_/g, " ")}
                            </Badge>
                          </td>
                          <td className="text-sm text-secondary" style={{ maxWidth: 320 }}>
                            <span className="truncate" style={{ display: "block" }}>
                              {entry.conclusion ?? "—"}
                            </span>
                          </td>
                          <td className="text-sm">{formatDate(entry.created_at)}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
        </div>
      </div>
    </>
  );
}
