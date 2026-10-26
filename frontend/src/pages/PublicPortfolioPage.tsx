/**
 * Public portfolio — anonymous read of a published profile.
 *
 * Route: `/p/:publicId` (outside the auth guard, no session required).
 *
 * Data: `GET /v1/portfolios/{public_id}` — served only when the owner has
 * explicitly published. Every item carries the server's `verification_note`
 * (evidence vs self-report) and the profile carries a `limitations` string;
 * both are shown verbatim because the whole point of a public portfolio here
 * is honest provenance. Nothing is computed or embellished client-side.
 */

import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { ExternalLink, ShieldAlert } from "lucide-react";
import { Badge, Button, LoadingRow, toUserMessage } from "@/components/ui";
import * as api from "@/services/api";
import type { PublicProfile } from "@/types/api";

const TYPE_TONES: Record<string, "accent" | "info" | "neutral"> = {
  assessment: "info",
  certificate: "accent",
};

export function PublicPortfolioPage() {
  const { publicId = "" } = useParams();
  const [profile, setProfile] = useState<PublicProfile | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!publicId) return;
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    api
      .getPublicPortfolio(publicId, controller.signal)
      .then((data) => setProfile(data))
      .catch((caught: unknown) => {
        if (!controller.signal.aborted) setError(caught);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [publicId]);

  if (loading) {
    return (
      <main className="public">
        <LoadingRow label="Loading portfolio…" />
      </main>
    );
  }

  if (error || !profile) {
    return (
      <main className="public">
        <div className="public__panel">
          <ShieldAlert size={28} aria-hidden="true" />
          <h1>Portfolio not available</h1>
          <p className="text-sm text-secondary">
            This portfolio does not exist, is not published, or the link is wrong. Portfolios
            are private until their owner publishes them.
          </p>
          {error != null && (
            <p className="text-xs text-muted">
              Server response: <span className="mono">{toUserMessage(error)}</span>
            </p>
          )}
          <Button
            variant="secondary"
            icon={<ExternalLink />}
            onClick={() => window.location.assign("/")}
          >
            Go to Unmist
          </Button>
        </div>
      </main>
    );
  }

  return (
    <main className="public">
      <article className="public__panel">
        <header className="public__head">
          <span className="page-head__eyebrow">Unmist portfolio</span>
          <h1>{profile.headline || "Knowledge portfolio"}</h1>
          {profile.bio && <p className="reading public__bio">{profile.bio}</p>}
        </header>

        <section aria-labelledby="public-items">
          <h2 id="public-items" className="public__section">
            Entries
          </h2>
          {profile.items.length === 0 ? (
            <p className="text-sm text-muted">This portfolio has no visible entries yet.</p>
          ) : (
            <ul className="public__items">
              {profile.items.map((item, index) => (
                <li key={`${item.item_type}-${item.title}-${index}`} className="public__item">
                  <div className="row gap-2" style={{ alignItems: "center", flexWrap: "wrap" }}>
                    <Badge tone={TYPE_TONES[item.item_type] ?? "neutral"}>{item.item_type}</Badge>
                    <h3 className="public__item-title">{item.title}</h3>
                    {item.is_self_reported && <Badge tone="warning">self-reported</Badge>}
                    {item.score !== null && item.max_score !== null && (
                      <span className="text-xs text-muted">
                        score {item.score}/{item.max_score}
                      </span>
                    )}
                  </div>
                  {item.evidence_summary && (
                    <p className="reading public__item-text">{item.evidence_summary}</p>
                  )}
                  <p className="public__item-note">
                    <span className="prov prov--evidence">verification</span>{" "}
                    <span className="text-sm text-secondary">{item.verification_note}</span>
                  </p>
                </li>
              ))}
            </ul>
          )}
        </section>

        <footer className="public__foot">
          <h2 className="public__section">About this portfolio</h2>
          <p className="reading">{profile.limitations}</p>
          <p className="text-xs text-muted" style={{ marginTop: "var(--space-4)" }}>
            Published from Unmist · public id <span className="mono">{profile.public_id}</span>
          </p>
        </footer>
      </article>
    </main>
  );
}
