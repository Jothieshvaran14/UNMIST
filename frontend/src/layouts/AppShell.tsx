/**
 * Application shell: dark ink sidebar, light workspace, responsive drawer.
 *
 * Desktop keeps a persistent left navigation with an explicit current-page
 * indicator; below 900px the sidebar collapses into a focus-trapped drawer
 * with a scrim so primary actions stay reachable without horizontal scroll.
 */

import { useEffect, useRef, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import {
  BookOpenCheck,
  BrainCircuit,
  ChevronRight,
  ClipboardCheck,
  FileStack,
  Gauge,
  LogOut,
  Menu,
  Settings2,
  X,
} from "lucide-react";
import { useAuth } from "@/auth/AuthContext";
import { Button } from "@/components/ui";

interface NavEntry {
  to: string;
  label: string;
  icon: React.ReactNode;
  end?: boolean;
  group: string;
}

const NAV: NavEntry[] = [
  { to: "/", label: "Overview", icon: <Gauge />, end: true, group: "Workspace" },
  { to: "/documents", label: "Documents", icon: <FileStack />, group: "Workspace" },
  { to: "/knowledge", label: "Knowledge Map", icon: <BrainCircuit />, group: "Workspace" },
  { to: "/assessments", label: "Assessments", icon: <ClipboardCheck />, group: "Learning" },
  { to: "/retention", label: "Retention", icon: <BookOpenCheck />, group: "Learning" },
  { to: "/settings", label: "Settings", icon: <Settings2 />, group: "Account" },
];

const ROUTE_TITLES: Array<{ match: RegExp; title: string }> = [
  { match: /^\/$/, title: "Overview" },
  { match: /^\/documents$/, title: "Documents" },
  { match: /^\/documents\/[^/]+\/segments$/, title: "Segment review" },
  { match: /^\/documents\/[^/]+$/, title: "Document detail" },
  { match: /^\/knowledge$/, title: "Knowledge Map" },
  { match: /^\/assessments$/, title: "Assessments" },
  { match: /^\/retention$/, title: "Retention" },
  { match: /^\/settings$/, title: "Settings" },
];

function titleFor(pathname: string): string {
  return ROUTE_TITLES.find((entry) => entry.match.test(pathname))?.title ?? "Unmist";
}

function initials(name: string | null | undefined, email: string): string {
  const source = (name || email || "").trim();
  if (!source) return "?";
  const parts = source.split(/[\s@._-]+/).filter(Boolean);
  return (parts[0]?.[0] ?? "?") + (parts[1]?.[0] ?? "");
}

export function AppShell() {
  const { user, signOut } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const [drawerOpen, setDrawerOpen] = useState(false);
  const toggleRef = useRef<HTMLButtonElement>(null);
  const sidebarRef = useRef<HTMLElement>(null);

  // Close the mobile drawer whenever navigation happens.
  useEffect(() => {
    setDrawerOpen(false);
  }, [location.pathname]);

  // Escape closes the drawer; focus returns to the toggle button.
  useEffect(() => {
    if (!drawerOpen) return undefined;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setDrawerOpen(false);
        toggleRef.current?.focus();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    const firstLink = sidebarRef.current?.querySelector<HTMLElement>("a, button");
    firstLink?.focus();
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [drawerOpen]);

  const groups = NAV.reduce<Record<string, NavEntry[]>>((acc, entry) => {
    (acc[entry.group] ??= []).push(entry);
    return acc;
  }, {});

  const handleSignOut = async () => {
    await signOut();
    navigate("/login", { replace: true });
  };

  const title = titleFor(location.pathname);

  return (
    <div className="app">
      <a className="skip-link" href="#main-content">
        Skip to main content
      </a>

      {drawerOpen && <div className="scrim" onClick={() => setDrawerOpen(false)} aria-hidden="true" />}

      <aside
        className={`sidebar ${drawerOpen ? "is-open" : ""}`.trim()}
        ref={sidebarRef}
        aria-label="Primary navigation"
      >
        <div className="brand">
          <span className="brand__mark" aria-hidden="true">
            <BookOpenCheck />
          </span>
          <span>
            <span className="brand__name">Unmist</span>
            <br />
            <span className="brand__tag">Evidence · Learning</span>
          </span>
          <button
            className="btn btn--ghost btn--sm btn--icon menu-toggle"
            style={{ marginLeft: "auto", color: "#fff" }}
            onClick={() => setDrawerOpen(false)}
            aria-label="Close navigation"
          >
            <X />
          </button>
        </div>

        <nav className="nav">
          {Object.entries(groups).map(([group, entries]) => (
            <div key={group}>
              <div className="nav__section">{group}</div>
              {entries.map((entry) => (
                <NavLink
                  key={entry.to}
                  to={entry.to}
                  end={entry.end}
                  className={({ isActive }) =>
                    `nav__item ${isActive ? "is-active" : ""}`.trim()
                  }
                >
                  {entry.icon}
                  <span>{entry.label}</span>
                </NavLink>
              ))}
            </div>
          ))}
        </nav>

        <div className="sidebar__foot">
          <button className="account" onClick={handleSignOut} title="Sign out">
            <span className="account__avatar" aria-hidden="true">
              {initials(user?.display_name, user?.email ?? "")}
            </span>
            <span className="account__meta">
              <span className="account__name">
                {user?.display_name || user?.email || "Signed in"}
              </span>
              <span className="account__role">
                {user?.organizations?.[0]?.role
                  ? `${user.organizations[0].org_name} · ${user.organizations[0].role}`
                  : "Sign out"}
              </span>
            </span>
            <LogOut aria-hidden="true" />
          </button>
        </div>
      </aside>

      <div className="main">
        <header className="topbar">
          <Button
            className="menu-toggle btn--icon"
            variant="ghost"
            onClick={() => setDrawerOpen(true)}
            aria-label="Open navigation"
            aria-expanded={drawerOpen}
            ref={toggleRef}
          >
            <Menu />
          </Button>

          <div className="topbar__crumb">
            <span>Unmist</span>
            <ChevronRight aria-hidden="true" />
            <strong>{title}</strong>
          </div>

          <div className="topbar__right">
            <span className="conn-dot conn-dot--ok" title="Session active">
              <span className="conn-dot__dot" aria-hidden="true" />
              {user?.email ?? "Signed in"}
            </span>
          </div>
        </header>

        <main className="content" id="main-content" tabIndex={-1}>
          <Outlet />
        </main>
      </div>
    </div>
  );
}
