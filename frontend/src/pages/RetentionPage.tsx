/**
 * Retention.
 *
 * Connected to real data:
 *   - `GET /v1/me/profile` -> `retention` (schedules with status,
 *     due date, interval, lapses)
 *   - `GET /v1/learners/me/reviews/due` -> the spaced-repetition due list
 *
 * No retention rate, forgetting curve, or projection is computed client-side —
 * only what the server reports is shown. Scheduling controls that the API does
 * not expose are listed as unavailable rather than rendered as dead buttons.
 */

import { Link } from "react-router-dom";
import { CalendarClock, Layers, RefreshCw, TimerReset } from "lucide-react";
import { useAsyncData } from "@/hooks/useAsyncData";
import { Badge, Card, EmptyState, ErrorState, LoadingRow } from "@/components/ui";
import { Button } from "@/components/ui";
import * as api from "@/services/api";

function formatDate(value: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  const now = Date.now();
  const diffDays = Math.round((date.getTime() - now) / 86_400_000);
  const stamp = date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  if (diffDays === 0) return `today · ${stamp}`;
  if (diffDays === 1) return `tomorrow · ${stamp}`;
  if (diffDays < 0) return `overdue since ${stamp}`;
  return `in ${diffDays} days · ${stamp}`;
}

const STATUS_TONES: Record<string, "success" | "warning" | "danger" | "info" | "neutral"> = {
  due: "warning",
  overdue: "danger",
  scheduled: "info",
  learning: "info",
  review: "warning",
  relearning: "warning",
  suspended: "neutral",
  done: "success",
};

export function RetentionPage() {
  const profile = useAsyncData((signal) => api.getMyProfile(signal), []);
  const due = useAsyncData((signal) => api.listDueReviews(signal), []);

  const loading = profile.initialLoading || due.initialLoading;
  const hardError = (profile.error && !profile.data) || (due.error && !due.data);

  if (loading) {
    return (
      <div className="card card--pad">
        <LoadingRow label="Loading retention data…" />
      </div>
    );
  }

  const schedules = profile.data?.retention ?? [];
  const dueItems = due.data ?? [];
  const overdue = schedules.filter(
    (item) => item.due_at !== null && new Date(item.due_at).getTime() < Date.now(),
  );

  return (
    <>
      <div className="page-head">
        <div className="page-head__title">
          <span className="page-head__eyebrow">Learning</span>
          <h1>Retention</h1>
          <p className="page-head__desc">
            Spaced-repetition schedules recorded for your account, and what is due next. All
            values come from the retention service — none are projected in the browser.
          </p>
        </div>
        <div className="page-head__actions">
          <Button
            variant="ghost"
            icon={<RefreshCw />}
            onClick={() => {
              profile.reload();
              due.reload();
            }}
            disabled={profile.refreshing || due.refreshing}
          >
            Refresh
          </Button>
          <Link to="/assessments">
            <Button variant="secondary">View mastery</Button>
          </Link>
        </div>
      </div>

      {hardError && (
        <div className="card card--pad" style={{ marginBottom: "var(--space-5)" }}>
          <ErrorState error={profile.error ?? due.error} onRetry={() => { profile.reload(); due.reload(); }} compact />
        </div>
      )}

      <div className="grid grid--3" style={{ marginBottom: "var(--space-5)" }}>
        <div className="stat">
          <div className="stat__label">
            <Layers />
            Scheduled concepts
          </div>
          <div className="stat__value">{schedules.length}</div>
          <div className="stat__note">From GET /v1/me/profile</div>
        </div>
        <div className="stat">
          <div className="stat__label">
            <CalendarClock />
            Due now
          </div>
          <div className="stat__value">{dueItems.length}</div>
          <div className="stat__note">From /learners/me/reviews/due</div>
        </div>
        <div className="stat">
          <div className="stat__label">
            <TimerReset />
            Past due date
          </div>
          <div className="stat__value">{overdue.length}</div>
          <div className="stat__note">Computed from server due_at</div>
        </div>
      </div>

      <div className="grid grid--2">
        <Card title="Due for review" icon={<CalendarClock />} pad={false}>
          {dueItems.length === 0 ? (
            <EmptyState
              icon={<CalendarClock />}
              title="Nothing due"
              description="The spaced-repetition service currently has no reviews due for your account."
            />
          ) : (
            <ul>
              {dueItems.slice(0, 20).map((item, index) => {
                const scheduleId =
                  (item.schedule_id as string | undefined) ??
                  (item.id as string | undefined) ??
                  `due-${index}`;
                const status = (item.status as string | undefined) ?? "due";
                const dueAt = (item.due_at as string | undefined) ?? null;
                const interval = item.interval_days as number | undefined;
                const repetitions = item.repetitions as number | undefined;
                const lapses = item.lapses as number | undefined;
                return (
                  <li className="doc-row" key={scheduleId} style={{ cursor: "default" }}>
                    <span className="doc-row__icon" aria-hidden="true">
                      <CalendarClock />
                    </span>
                    <span className="doc-row__body">
                      <span className="doc-row__title mono">{scheduleId.slice(0, 12)}…</span>
                      <span className="doc-row__meta">
                        <span>Due {formatDate(dueAt)}</span>
                        {typeof interval === "number" && <span>· interval {interval}d</span>}
                        {typeof repetitions === "number" && <span>· {repetitions} reps</span>}
                        {typeof lapses === "number" && lapses > 0 && <span>· {lapses} lapses</span>}
                      </span>
                    </span>
                    <span className="doc-row__badges">
                      <Badge tone={STATUS_TONES[status] ?? "neutral"}>{status}</Badge>
                    </span>
                  </li>
                );
              })}
            </ul>
          )}
          <footer className="card__foot text-xs text-muted">
            Submitting a review from this screen is not offered: the API requires a schedule id
            and mutation semantics the UI cannot yet confirm.
          </footer>
        </Card>

        <Card title="Retention schedules" icon={<TimerReset />} pad={false}>
          {schedules.length === 0 ? (
            <EmptyState
              icon={<TimerReset />}
              title="No schedules yet"
              description="Schedules are created once concepts have been assessed. Complete an assessment and the schedule appears here."
            />
          ) : (
            <div className="table-wrap">
              <table className="table">
                <caption className="sr-only">Spaced-repetition schedules</caption>
                <thead>
                  <tr>
                    <th scope="col">Concept</th>
                    <th scope="col">Status</th>
                    <th scope="col">Next due</th>
                    <th scope="col">Interval</th>
                    <th scope="col">Lapses</th>
                  </tr>
                </thead>
                <tbody>
                  {schedules.slice(0, 25).map((item) => (
                    <tr key={item.concept_id}>
                      <td style={{ maxWidth: 220 }}>
                        <span className="truncate" style={{ display: "block" }}>
                          {item.canonical_name}
                        </span>
                      </td>
                      <td>
                        <Badge tone={STATUS_TONES[item.status] ?? "neutral"}>{item.status}</Badge>
                      </td>
                      <td className="text-sm text-secondary">{formatDate(item.due_at)}</td>
                      <td className="text-sm">{item.interval_days}d</td>
                      <td className="text-sm">{item.lapses}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <footer className="card__foot text-xs text-muted">
            {schedules.length > 25 ? `Showing 25 of ${schedules.length}. ` : ""}
            The API exposes no endpoint to edit or suspend a schedule, so no such control is
            shown.
          </footer>
        </Card>
      </div>
    </>
  );
}
