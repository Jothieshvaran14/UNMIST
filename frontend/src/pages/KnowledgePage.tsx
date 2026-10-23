/**
 * Knowledge Map.
 *
 * Data boundary, inspected before building:
 *   - `GET /v1/topics` returns real topics (name, slug, description, visibility).
 *   - `GET /v1/topics/{id}/graphs` returns `GraphVersionResponse`, which carries
 *     `concept_count` and `relationship_count` only. The API exposes **no**
 *     node/edge list, so no graph is drawn and no relationships, prerequisites,
 *     or mastery values are invented.
 *
 * The view is therefore a readable topic list with the real concept/relationship
 * counts the server reports, plus an explicit note about what is not available.
 */

import { useState } from "react";
import { Link } from "react-router-dom";
import {
  BrainCircuit,
  ChevronRight,
  FileStack,
  Info,
  RefreshCw,
  Share2,
} from "lucide-react";
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
import type { GraphVersion, Topic } from "@/types/api";

export function KnowledgePage() {
  const topics = useAsyncData((signal) => api.listTopics({ limit: 50 }, signal), []);
  const [selected, setSelected] = useState<Topic | null>(null);

  const graphs = useAsyncData(
    (signal) => api.listGraphVersions(selected?.id ?? "", signal),
    [selected?.id],
    { enabled: selected !== null },
  );

  return (
    <>
      <div className="page-head">
        <div className="page-head__title">
          <span className="page-head__eyebrow">Knowledge</span>
          <h1>Knowledge Map</h1>
          <p className="page-head__desc">
            Topics and the concept graphs built from your source material. Select a topic to see
            the graph versions the server has recorded.
          </p>
        </div>
        <div className="page-head__actions">
          <Button
            variant="ghost"
            icon={<RefreshCw />}
            onClick={topics.reload}
            disabled={topics.refreshing}
          >
            Refresh
          </Button>
          <Link to="/documents">
            <Button variant="secondary" icon={<FileStack />}>
              Go to documents
            </Button>
          </Link>
        </div>
      </div>

      <div style={{ marginBottom: "var(--space-5)" }}>
        <Alert tone="info" title="What this view can and cannot show">
          The API returns topic metadata and graph <em>version summaries</em> (concept and
          relationship counts). It does not expose a node-and-edge list, so no interactive graph
          is rendered here — drawing one would require inventing connections.
        </Alert>
      </div>

      {topics.initialLoading ? (
        <div className="card card--pad">
          <LoadingRow label="Loading topics…" />
        </div>
      ) : topics.error ? (
        <div className="card card--pad">
          <ErrorState error={topics.error} onRetry={topics.reload} />
        </div>
      ) : (topics.data?.items.length ?? 0) === 0 ? (
        <div className="card card--pad">
          <EmptyState
            icon={<BrainCircuit />}
            title="No topics yet"
            description="Topics are created through the knowledge API. Once a topic exists and a graph build has run, its versions appear here with the counts the server reports."
            action={
              <Link to="/documents">
                <Button variant="primary">Start from a document</Button>
              </Link>
            }
          />
        </div>
      ) : (
        <div className="split" style={{ gridTemplateColumns: "1fr 1.1fr" }}>
          <Card title={`Topics (${topics.data?.total ?? 0})`} icon={<BrainCircuit />} pad={false}>
            <ul>
              {(topics.data?.items ?? []).map((topic) => (
                <li key={topic.id}>
                  <button
                    className="doc-row"
                    style={{
                      width: "100%",
                      textAlign: "left",
                      background: selected?.id === topic.id ? "var(--accent-soft)" : undefined,
                      borderLeft:
                        selected?.id === topic.id ? "3px solid var(--accent)" : "3px solid transparent",
                    }}
                    onClick={() => setSelected(topic)}
                    aria-current={selected?.id === topic.id ? "true" : undefined}
                  >
                    <span className="doc-row__icon" aria-hidden="true">
                      <Share2 />
                    </span>
                    <span className="doc-row__body">
                      <span className="doc-row__title">{topic.name}</span>
                      <span className="doc-row__meta">
                        <span className="mono">{topic.slug}</span>
                        {topic.is_public && <Badge tone="info">Public</Badge>}
                      </span>
                    </span>
                    <ChevronRight
                      style={{ width: 16, height: 16, color: "var(--text-muted)" }}
                      aria-hidden="true"
                    />
                  </button>
                </li>
              ))}
            </ul>
          </Card>

          <div className="stack gap-5">
            {selected ? (
              <Card
                title={selected.name}
                icon={<BrainCircuit />}
                action={<Badge tone="neutral">v · {selected.slug}</Badge>}
              >
                <div className="stack gap-4">
                  <p className="text-sm text-secondary">
                    {selected.description || "No description was recorded for this topic."}
                  </p>

                  <dl className="dl">
                    <dt>Topic id</dt>
                    <dd className="mono">{selected.id}</dd>
                    <dt>Visibility</dt>
                    <dd>{selected.is_public ? "Public" : "Private"}</dd>
                    <dt>Created</dt>
                    <dd>{new Date(selected.created_at).toLocaleString()}</dd>
                  </dl>

                  <div className="stack gap-2">
                    <div className="field__label">Graph versions</div>
                    {graphs.initialLoading ? (
                      <LoadingRow label="Loading graph versions…" />
                    ) : graphs.error ? (
                      <ErrorState error={graphs.error} onRetry={graphs.reload} compact />
                    ) : (graphs.data?.length ?? 0) === 0 ? (
                      <p className="text-sm text-muted">
                        No graph build has been recorded for this topic yet.
                      </p>
                    ) : (
                      <ul className="stack gap-2">
                        {graphs.data!.map((version: GraphVersion) => (
                          <li className="region-ref" key={version.id}>
                            <span className="region-ref__page">v{version.version}</span>
                            <span className="grow">
                              <div className="row gap-2 wrap">
                                <Badge
                                  tone={
                                    version.status === "active"
                                      ? "success"
                                      : version.status === "failed"
                                        ? "danger"
                                        : "neutral"
                                  }
                                >
                                  {version.status}
                                </Badge>
                                <span className="text-xs text-muted">
                                  {new Date(version.created_at).toLocaleString()}
                                </span>
                              </div>
                              <div className="region-ref__text">
                                {version.concept_count.toLocaleString()} concepts ·{" "}
                                {version.relationship_count.toLocaleString()} relationships
                              </div>
                            </span>
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>

                  <p className="text-xs text-muted row gap-2">
                    <Info style={{ width: 14, height: 14 }} aria-hidden="true" />
                    Counts are reported by the server. Relationship endpoints are not exposed, so
                    no edges are drawn.
                  </p>
                </div>
              </Card>
            ) : (
              <div className="card card--pad">
                <EmptyState
                  icon={<BrainCircuit />}
                  title="Select a topic"
                  description="Choose a topic on the left to inspect its recorded graph versions."
                />
              </div>
            )}
          </div>
        </div>
      )}
    </>
  );
}
