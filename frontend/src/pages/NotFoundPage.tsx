import { Link, useLocation } from "react-router-dom";
import { Compass } from "lucide-react";
import { Button, EmptyState } from "@/components/ui";

/** Unknown routes land here — never a blank screen. */
export function NotFoundPage() {
  const location = useLocation();
  return (
    <div className="card card--pad" style={{ marginTop: "var(--space-8)" }}>
      <EmptyState
        icon={<Compass />}
        title="Page not found"
        description={
          <>
            Nothing is routed at <code>{location.pathname}</code>.
          </>
        }
        action={
          <Link to="/">
            <Button variant="primary">Back to Overview</Button>
          </Link>
        }
      />
    </div>
  );
}
