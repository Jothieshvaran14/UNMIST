/**
 * Routing and application entry composition.
 *
 * Route structure:
 *   /                          Overview
 *   /documents                 Document library
 *   /documents/:documentId     Document detail + processing workspace
 *   /documents/:documentId/segments   Segment review workspace
 *   /knowledge                 Knowledge Map
 *   /assessments               Assessments
 *   /retention                 Retention
 *   /settings                  Settings
 *   /login                     Sign in / register
 *   *                          Not found
 *
 * Routes are browser-history based, so direct navigation and refresh work;
 * unknown paths land on a real not-found screen with a working way back.
 */

import { Suspense, lazy } from "react";
import { BrowserRouter, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { AuthProvider, useAuth } from "@/auth/AuthContext";
import { ToastProvider, LoadingRow } from "@/components/ui";
import { AppShell } from "@/layouts/AppShell";
import { OverviewPage } from "@/pages/OverviewPage";
import { DocumentsPage } from "@/pages/DocumentsPage";
import { DocumentDetailPage } from "@/pages/DocumentDetailPage";
import { LoginPage } from "@/pages/LoginPage";
import { NotFoundPage } from "@/pages/NotFoundPage";

// Secondary screens are code-split so the primary upload-to-segmentation
// workflow loads first.
const KnowledgePage = lazy(() =>
  import("@/pages/KnowledgePage").then((m) => ({ default: m.KnowledgePage })),
);
const AssessmentsPage = lazy(() =>
  import("@/pages/AssessmentsPage").then((m) => ({ default: m.AssessmentsPage })),
);
const RetentionPage = lazy(() =>
  import("@/pages/RetentionPage").then((m) => ({ default: m.RetentionPage })),
);
const SettingsPage = lazy(() =>
  import("@/pages/SettingsPage").then((m) => ({ default: m.SettingsPage })),
);
const SegmentWorkspacePage = lazy(() =>
  import("@/pages/SegmentWorkspacePage").then((m) => ({ default: m.SegmentWorkspacePage })),
);

function FullPageLoading({ label }: { label: string }) {
  return (
    <div
      style={{
        minHeight: "100dvh",
        display: "grid",
        placeItems: "center",
        background: "var(--bg)",
      }}
      role="status"
      aria-live="polite"
    >
      <LoadingRow label={label} />
    </div>
  );
}

/** Gate for authenticated routes; preserves the requested path for redirect. */
function RequireAuth({ children }: { children: React.ReactNode }) {
  const { status } = useAuth();
  const location = useLocation();

  if (status === "booting") return <FullPageLoading label="Restoring your session…" />;
  if (status === "anonymous") {
    return <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />;
  }
  return <>{children}</>;
}

function AppRoutes() {
  const { status } = useAuth();

  if (status === "booting") return <FullPageLoading label="Starting Unmist…" />;

  return (
    <Suspense fallback={<FullPageLoading label="Loading screen…" />}>
      <Routes>
        <Route path="/login" element={<LoginPage />} />

        <Route
          element={
            <RequireAuth>
              <AppShell />
            </RequireAuth>
          }
        >
          <Route path="/" element={<OverviewPage />} />
          <Route path="/documents" element={<DocumentsPage />} />
          <Route path="/documents/:documentId" element={<DocumentDetailPage />} />
          <Route path="/documents/:documentId/segments" element={<SegmentWorkspacePage />} />
          <Route path="/knowledge" element={<KnowledgePage />} />
          <Route path="/assessments" element={<AssessmentsPage />} />
          <Route path="/retention" element={<RetentionPage />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="*" element={<NotFoundPage />} />
        </Route>
      </Routes>
    </Suspense>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <ToastProvider>
          <AppRoutes />
        </ToastProvider>
      </AuthProvider>
    </BrowserRouter>
  );
}
