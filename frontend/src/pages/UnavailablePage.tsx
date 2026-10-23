/**
 * Honest "not available in this build" screen.
 *
 * Used for destinations whose backend functionality is not yet demonstrable
 * through the API. It describes the intended capability, marks it unavailable,
 * and never renders fabricated records, charts, or controls that do nothing.
 */

import { Link } from "react-router-dom";
import { Construction } from "lucide-react";
import { Alert, Button, Card, EmptyState } from "@/components/ui";

export function UnavailablePage({
  eyebrow,
  title,
  description,
  capabilities,
  gaps,
}: {
  eyebrow: string;
  title: string;
  description: string;
  capabilities: string[];
  gaps: string[];
}) {
  return (
    <>
      <div className="page-head">
        <div className="page-head__title">
          <span className="page-head__eyebrow">{eyebrow}</span>
          <h1>{title}</h1>
          <p className="page-head__desc">{description}</p>
        </div>
        <div className="page-head__actions">
          <Link to="/documents">
            <Button variant="secondary">Go to documents</Button>
          </Link>
        </div>
      </div>

      <div className="grid grid--2">
        <Card title="Intended capability" icon={<Construction />}>
          <ul className="stack gap-3 text-sm">
            {capabilities.map((item) => (
              <li key={item} className="row gap-3" style={{ alignItems: "flex-start" }}>
                <span
                  aria-hidden="true"
                  style={{
                    width: 6,
                    height: 6,
                    borderRadius: "50%",
                    background: "var(--accent)",
                    marginTop: 8,
                    flex: "none",
                  }}
                />
                <span className="grow">{item}</span>
              </li>
            ))}
          </ul>
        </Card>

        <Card title="Not available yet" icon={<Construction />}>
          <Alert tone="warning" title="This workspace is not connected">
            {gaps.length === 1
              ? gaps[0]
              : "The following backend capabilities are missing or not exposed to the API."}
          </Alert>
          <ul className="stack gap-2 text-sm mt-4">
            {gaps.map((item) => (
              <li key={item} className="row gap-2" style={{ alignItems: "flex-start" }}>
                <span className="text-danger" aria-hidden="true">
                  {"—"}
                </span>
                <span className="grow text-secondary">{item}</span>
              </li>
            ))}
          </ul>
        </Card>
      </div>

      <div className="card card--pad mt-6">
        <EmptyState
          icon={<Construction />}
          title="Nothing is shown here on purpose"
          description="Displaying empty charts or placeholder scores would misrepresent the system's state. This screen will populate once the API supports it."
          action={
            <Link to="/">
              <Button variant="primary">Back to Overview</Button>
            </Link>
          }
        />
      </div>
    </>
  );
}
