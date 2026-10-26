/**
 * Portfolio — private knowledge profile with explicit public sharing.
 *
 * Connected to real data:
 *   - `GET/PATCH /v1/me/portfolio/settings`  -> publishing state, headline, bio
 *   - `GET/POST  /v1/me/portfolio/items`     -> the learner's own entries
 *
 * Authorization boundary: the portfolio is **private by default**. Publishing
 * is a deliberate, confirmed action (`is_public: true` is only ever sent when
 * the user presses Publish and confirms). The public view exposes only the
 * server's redacted fields — never private documents or source content.
 */

import { useEffect, useState } from "react";
import { CheckCircle2, Eye, EyeOff, Link2, Plus, Save, Share2 } from "lucide-react";
import { useAsyncData } from "@/hooks/useAsyncData";
import {
  Alert,
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorState,
  Field,
  LoadingRow,
  Modal,
  useToast,
} from "@/components/ui";
import * as api from "@/services/api";
import type { PortfolioItemCreate } from "@/types/api";

const ITEM_TYPES: Array<PortfolioItemCreate["item_type"]> = [
  "concept",
  "skill",
  "project",
  "assessment",
  "certificate",
];

function publicUrl(publicId: string): string {
  return `${window.location.origin}/p/${publicId}`;
}

export function PortfolioPage() {
  const toast = useToast();
  const settings = useAsyncData((signal) => api.getPortfolioSettings(signal), []);
  const items = useAsyncData((signal) => api.listPortfolioItems(signal), []);

  const [headline, setHeadline] = useState("");
  const [bio, setBio] = useState("");
  const [assessedOnly, setAssessedOnly] = useState(false);
  const [saving, setSaving] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);

  // Item form
  const [itemType, setItemType] = useState<PortfolioItemCreate["item_type"]>("concept");
  const [itemTitle, setItemTitle] = useState("");
  const [itemEvidence, setItemEvidence] = useState("");
  const [itemSelfReported, setItemSelfReported] = useState(false);
  const [itemVisibility, setItemVisibility] = useState<"private" | "public">("private");
  const [adding, setAdding] = useState(false);

  // Mirror server state into the form once loaded.
  useEffect(() => {
    if (settings.data) {
      setHeadline(settings.data.headline ?? "");
      setBio(settings.data.bio ?? "");
      setAssessedOnly(settings.data.show_assessed_only);
    }
  }, [settings.data]);

  const server = settings.data;
  const dirty =
    server !== null &&
    (headline !== (server.headline ?? "") ||
      bio !== (server.bio ?? "") ||
      assessedOnly !== server.show_assessed_only);

  const saveSettings = async () => {
    setSaving(true);
    try {
      const updated = await api.updatePortfolioSettings({
        headline,
        bio,
        show_assessed_only: assessedOnly,
      });
      settings.setData(updated);
      toast.success("Profile saved");
    } catch (error: unknown) {
      toast.error("Could not save profile", error instanceof Error ? error.message : undefined);
    } finally {
      setSaving(false);
    }
  };

  const setVisibility = async (isPublic: boolean) => {
    setSaving(true);
    try {
      const updated = await api.updatePortfolioSettings({ is_public: isPublic });
      settings.setData(updated);
      toast.success(
        isPublic ? "Portfolio published" : "Portfolio made private",
        isPublic
          ? "Anyone with the link can now read the public view."
          : "The public link no longer resolves.",
      );
    } catch (error: unknown) {
      toast.error("Could not change visibility", error instanceof Error ? error.message : undefined);
    } finally {
      setSaving(false);
      setConfirmOpen(false);
    }
  };

  const addItem = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!itemTitle.trim()) return;
    setAdding(true);
    try {
      await api.createPortfolioItem({
        item_type: itemType,
        title: itemTitle.trim(),
        ...(itemEvidence.trim() ? { evidence_summary: itemEvidence.trim() } : {}),
        is_self_reported: itemSelfReported,
        visibility: itemVisibility,
      });
      setItemTitle("");
      setItemEvidence("");
      setItemSelfReported(false);
      setItemVisibility("private");
      items.reload();
      toast.success("Entry added");
    } catch (error: unknown) {
      toast.error("Could not add entry", error instanceof Error ? error.message : undefined);
    } finally {
      setAdding(false);
    }
  };

  const copyLink = async (value: string) => {
    try {
      await navigator.clipboard.writeText(value);
      toast.success("Link copied");
    } catch {
      toast.error("Could not copy — select the link manually");
    }
  };

  if (settings.initialLoading) {
    return (
      <div className="card card--pad">
        <LoadingRow label="Loading portfolio…" />
      </div>
    );
  }

  return (
    <>
      <div className="page-head">
        <div className="page-head__title">
          <span className="page-head__eyebrow">Identity</span>
          <h1>Portfolio</h1>
          <p className="page-head__desc">
            Your knowledge profile is private by default. Publishing is a separate, deliberate
            decision — and the public view only ever exposes the fields the server approves for
            sharing.
          </p>
        </div>
        <div className="page-head__actions">
          <Button
            variant="ghost"
            icon={<Save />}
            onClick={saveSettings}
            disabled={saving || !dirty}
          >
            Save changes
          </Button>
        </div>
      </div>

      {settings.error && !server && (
        <div className="card card--pad" style={{ marginBottom: "var(--space-5)" }}>
          <ErrorState error={settings.error} onRetry={settings.reload} compact />
        </div>
      )}

      <div className="grid grid--2" style={{ marginBottom: "var(--space-5)" }}>
        <Card title="Profile" icon={<Share2 />}>
          <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}>
            <Field label="Headline" htmlFor="pf-headline" hint="Shown at the top of the public view.">
              <input
                id="pf-headline"
                className="input"
                maxLength={300}
                value={headline}
                onChange={(event) => setHeadline(event.target.value)}
                placeholder="e.g. Electronic engineering student, {year}"
              />
            </Field>
            <Field label="About" htmlFor="pf-bio" hint="Plain prose. No markdown is rendered.">
              <textarea
                id="pf-bio"
                className="textarea"
                value={bio}
                onChange={(event) => setBio(event.target.value)}
                placeholder="A short, factual description."
              />
            </Field>
            <label className="row gap-2" style={{ alignItems: "center", cursor: "pointer" }}>
              <input
                type="checkbox"
                checked={assessedOnly}
                onChange={(event) => setAssessedOnly(event.target.checked)}
              />
              <span className="text-sm">Show only assessed entries when published</span>
            </label>
            <div className="text-xs text-muted">
              Saved through <span className="mono">PATCH /v1/me/portfolio/settings</span>.
            </div>
          </div>
        </Card>

        <Card
          title="Visibility"
          icon={server?.is_public ? <Eye /> : <EyeOff />}
          action={
            server?.is_public ? (
              <Badge tone="success">published</Badge>
            ) : (
              <Badge tone="neutral">private</Badge>
            )
          }
        >
          <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}>
            {server?.is_public ? (
              <>
                <Alert tone="success" title="Your portfolio is public">
                  Anyone holding the link can read the public view. It exposes titles, approved
                  evidence summaries and verification notes — never your private documents or
                  source content.
                </Alert>
                <div className="row gap-2" style={{ alignItems: "center", flexWrap: "wrap" }}>
                  <span className="mono text-xs" style={{ wordBreak: "break-all" }}>
                    {server.public_id ? publicUrl(server.public_id) : "—"}
                  </span>
                  <Button
                    variant="secondary"
                    size="sm"
                    icon={<Link2 />}
                    onClick={() => server.public_id && copyLink(publicUrl(server.public_id))}
                  >
                    Copy link
                  </Button>
                </div>
                <Button variant="secondary" icon={<EyeOff />} onClick={() => setVisibility(false)} disabled={saving}>
                  Make private again
                </Button>
              </>
            ) : (
              <>
                <p className="text-sm text-secondary">
                  Publishing makes the public view readable by anyone with the link. Private stays
                  the default until you confirm otherwise.
                </p>
                <Button variant="primary" icon={<Eye />} onClick={() => setConfirmOpen(true)} disabled={saving}>
                  Publish portfolio
                </Button>
              </>
            )}
            {server?.published_at && (
              <div className="text-xs text-muted">
                Last published {new Date(server.published_at).toLocaleString()}
              </div>
            )}
          </div>
        </Card>
      </div>

      <div className="grid grid--2">
        <Card title="Entries" icon={<CheckCircle2 />} pad={false}>
          {items.initialLoading ? (
            <LoadingRow label="Loading entries…" />
          ) : items.error && !items.data ? (
            <div className="card__body">
              <ErrorState error={items.error} onRetry={items.reload} compact />
            </div>
          ) : (items.data ?? []).length === 0 ? (
            <EmptyState
              icon={<CheckCircle2 />}
              title="No entries yet"
              description="Entries are things you choose to present: concepts demonstrated, projects, assessments, certificates. Add one below."
            />
          ) : (
            <ul>
              {(items.data ?? []).map((item) => (
                <li className="doc-row" key={item.id} style={{ cursor: "default" }}>
                  <span className="doc-row__body">
                    <span className="doc-row__title">{item.title}</span>
                    <span className="doc-row__meta">
                      <span>{item.item_type}</span>
                      {item.score !== null && item.max_score !== null && (
                        <span>
                          · score {item.score}/{item.max_score}
                        </span>
                      )}
                      {item.is_self_reported && <span>· self-reported</span>}
                    </span>
                    {item.evidence_summary && (
                      <span className="text-xs text-secondary">{item.evidence_summary}</span>
                    )}
                  </span>
                  <span className="doc-row__badges">
                    <Badge tone={item.visibility === "public" ? "accent" : "neutral"}>
                      {item.visibility}
                    </Badge>
                  </span>
                </li>
              ))}
            </ul>
          )}
          <footer className="card__foot text-xs text-muted">
            Private entries are never served by the public endpoint, whatever the portfolio's
            visibility setting.
          </footer>
        </Card>

        <Card title="Add an entry" icon={<Plus />}>
          <form onSubmit={addItem} style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}>
            <Field label="Type" htmlFor="pf-type">
              <select
                id="pf-type"
                className="select"
                value={itemType}
                onChange={(event) => setItemType(event.target.value as PortfolioItemCreate["item_type"])}
              >
                {ITEM_TYPES.map((type) => (
                  <option key={type} value={type}>
                    {type}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Title" htmlFor="pf-title" required>
              <input
                id="pf-title"
                className="input"
                maxLength={300}
                value={itemTitle}
                onChange={(event) => setItemTitle(event.target.value)}
                placeholder="What are you presenting?"
                required
              />
            </Field>
            <Field
              label="Evidence summary"
              htmlFor="pf-evidence"
              hint="What supports this entry. Be factual — this text can become public."
            >
              <textarea
                id="pf-evidence"
                className="textarea"
                style={{ minHeight: 80 }}
                value={itemEvidence}
                onChange={(event) => setItemEvidence(event.target.value)}
              />
            </Field>
            <div className="row gap-4" style={{ alignItems: "center", flexWrap: "wrap" }}>
              <label className="row gap-2" style={{ alignItems: "center", cursor: "pointer" }}>
                <input
                  type="checkbox"
                  checked={itemSelfReported}
                  onChange={(event) => setItemSelfReported(event.target.checked)}
                />
                <span className="text-sm">Self-reported</span>
              </label>
              <label className="row gap-2" style={{ alignItems: "center", cursor: "pointer" }}>
                <input
                  type="checkbox"
                  checked={itemVisibility === "public"}
                  onChange={(event) => setItemVisibility(event.target.checked ? "public" : "private")}
                />
                <span className="text-sm">Mark visible when published</span>
              </label>
            </div>
            <Button type="submit" variant="primary" icon={<Plus />} disabled={adding || !itemTitle.trim()}>
              {adding ? "Adding…" : "Add entry"}
            </Button>
          </form>
        </Card>
      </div>

      <Modal
        open={confirmOpen}
        onClose={() => setConfirmOpen(false)}
        title="Publish your portfolio?"
        footer={
          <>
            <Button variant="secondary" onClick={() => setConfirmOpen(false)}>
              Cancel
            </Button>
            <Button variant="primary" onClick={() => setVisibility(true)} disabled={saving}>
              {saving ? "Publishing…" : "Yes, publish"}
            </Button>
          </>
        }
      >
        <p className="text-sm text-secondary">
          This sends <span className="mono">is_public: true</span> to the server. Anyone with the
          link will be able to read your headline, bio and every entry you marked visible —
          including self-reported ones, which the public view labels as self-reported.
        </p>
        <p className="text-sm text-secondary" style={{ marginTop: "var(--space-3)" }}>
          Private documents, source content and anything not explicitly shared stay private; the
          server enforces that, not this screen.
        </p>
      </Modal>
    </>
  );
}
