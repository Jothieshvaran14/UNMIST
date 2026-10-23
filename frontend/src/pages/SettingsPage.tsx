/**
 * Settings.
 *
 * Shows real account, organization and learner data from `GET /v1/auth/me` and
 * `GET /v1/me/profile`, and a change-password form wired to the real
 * `POST /v1/auth/change-password` endpoint.
 *
 * Capabilities the API does not expose (renaming your account, deleting data,
 * exporting records) are listed as unavailable instead of being rendered as
 * controls that do nothing.
 */

import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import {
  Building2,
  KeyRound,
  LogOut,
  Mail,
  RefreshCw,
  ShieldCheck,
  Trash2,
  UserRound,
} from "lucide-react";
import { useAuth } from "@/auth/AuthContext";
import { useAsyncData } from "@/hooks/useAsyncData";
import {
  Alert,
  Badge,
  Button,
  Card,
  Field,
  LoadingRow,
  toUserMessage,
  useToast,
} from "@/components/ui";
import { request } from "@/services/http";
import { clearSession } from "@/services/session";
import * as api from "@/services/api";

export function SettingsPage() {
  const { user, signOut, profileError, refreshProfile } = useAuth();
  const navigate = useNavigate();
  const toast = useToast();

  const profile = useAsyncData((signal) => api.getMyProfile(signal), []);

  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [fieldError, setFieldError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  async function changePassword(event: FormEvent) {
    event.preventDefault();
    setFormError(null);
    setFieldError(null);
    setDone(false);

    if (next.length < 10) {
      setFieldError("The server requires at least 10 characters.");
      return;
    }
    if (current === next) {
      setFieldError("The new password must differ from the current one.");
      return;
    }

    setBusy(true);
    try {
      await request<void>("/v1/auth/change-password", {
        method: "POST",
        body: { current_password: current, new_password: next },
      });
      setDone(true);
      setCurrent("");
      setNext("");
      toast.success("Password updated");
    } catch (err) {
      if (err instanceof Error && err.message.includes("session has expired")) {
        clearSession();
        navigate("/login", { replace: true });
        return;
      }
      setFormError(toUserMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function handleSignOut() {
    await signOut();
    navigate("/login", { replace: true });
  }

  return (
    <>
      <div className="page-head">
        <div className="page-head__title">
          <span className="page-head__eyebrow">Account</span>
          <h1>Settings</h1>
          <p className="page-head__desc">
            Your account, organization membership and learner identity as the API reports them.
          </p>
        </div>
        <div className="page-head__actions">
          <Button
            variant="ghost"
            icon={<RefreshCw />}
            onClick={() => {
              void refreshProfile();
              profile.reload();
            }}
            disabled={profile.refreshing}
          >
            Refresh
          </Button>
          <Button variant="secondary" icon={<LogOut />} onClick={handleSignOut}>
            Sign out
          </Button>
        </div>
      </div>

      {profileError && (
        <div style={{ marginBottom: "var(--space-4)" }}>
          <Alert tone="warning" title="Profile unavailable">{profileError}</Alert>
        </div>
      )}

      <div className="grid grid--2">
        <Card title="Account" icon={<UserRound />}>
          <dl className="dl">
            <dt>Email</dt>
            <dd className="row gap-2">
              <Mail style={{ width: 14, height: 14, color: "var(--text-muted)" }} aria-hidden="true" />
              {user?.email ?? "—"}
            </dd>
            <dt>Display name</dt>
            <dd>{user?.display_name || "Not set"}</dd>
            <dt>Verified</dt>
            <dd>
              {user ? (
                <Badge tone={user.email_verified ? "success" : "warning"}>
                  {user.email_verified ? "Verified" : "Unverified"}
                </Badge>
              ) : (
                "—"
              )}
            </dd>
            <dt>Account id</dt>
            <dd className="mono">{user?.id ?? "—"}</dd>
            <dt>Member since</dt>
            <dd>{user ? new Date(user.created_at).toLocaleDateString() : "—"}</dd>
          </dl>

          <div className="mt-4">
            <div className="field__label row gap-2">
              <Building2 style={{ width: 14, height: 14 }} aria-hidden="true" />
              Organizations
            </div>
            {user && user.organizations.length > 0 ? (
              <ul className="stack gap-2 mt-2">
                {user.organizations.map((org) => (
                  <li className="region-ref" key={org.org_id}>
                    <span className="region-ref__page">{org.org_slug}</span>
                    <span className="grow">
                      <strong>{org.org_name}</strong>
                      <div className="region-ref__text">
                        {org.role} · joined {new Date(org.joined_at).toLocaleDateString()}
                      </div>
                    </span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-sm text-muted mt-2">No organization memberships recorded.</p>
            )}
          </div>
        </Card>

        <div className="stack gap-5">
          <Card title="Change password" icon={<KeyRound />}>
            <form className="stack gap-4" onSubmit={changePassword} noValidate>
              {done && (
                <Alert tone="success" title="Password changed">
                  The server accepted the new password.
                </Alert>
              )}
              {formError && <Alert tone="danger" title="Could not change password">{formError}</Alert>}

              <Field label="Current password" htmlFor="current-password" required>
                <input
                  id="current-password"
                  className="input"
                  type="password"
                  autoComplete="current-password"
                  value={current}
                  onChange={(event) => setCurrent(event.target.value)}
                  required
                />
              </Field>

              <Field
                label="New password"
                htmlFor="new-password"
                required
                error={fieldError}
                hint="Minimum 10 characters (server requirement)."
              >
                <input
                  id="new-password"
                  className="input"
                  type="password"
                  autoComplete="new-password"
                  value={next}
                  onChange={(event) => setNext(event.target.value)}
                  aria-invalid={fieldError ? "true" : undefined}
                  required
                />
              </Field>

              <div>
                <Button type="submit" variant="primary" loading={busy} icon={<ShieldCheck />}>
                  Update password
                </Button>
              </div>
            </form>
          </Card>

          <Card title="Learner profile" icon={<ShieldCheck />}>
            {profile.initialLoading ? (
              <LoadingRow label="Loading profile…" />
            ) : profile.error && !profile.data ? (
              <div className="text-sm text-secondary">
                {toUserMessage(profile.error)}{" "}
                <button className="btn btn--ghost btn--sm" onClick={profile.reload}>
                  Retry
                </button>
              </div>
            ) : (
              <dl className="dl">
                <dt>Learner id</dt>
                <dd className="mono">{profile.data?.learner_id ?? "—"}</dd>
                <dt>Organization</dt>
                <dd className="mono">{profile.data?.org_id ?? "Personal"}</dd>
                <dt>Topics</dt>
                <dd>{profile.data?.topics.length ?? 0}</dd>
                <dt>Tracked concepts</dt>
                <dd>{profile.data?.mastery.length ?? 0}</dd>
                <dt>Retention schedules</dt>
                <dd>{profile.data?.retention.length ?? 0}</dd>
              </dl>
            )}
          </Card>

          <Card title="Not available yet" icon={<Trash2 />}>
            <Alert tone="warning" title="No backend endpoint">
              These actions are listed only to set expectations. None of them are shown as
              buttons.
            </Alert>
            <ul className="stack gap-2 text-sm mt-3">
              <li className="row gap-2">
                <span className="text-danger" aria-hidden="true">{"—"}</span>
                <span className="grow">Change display name (no profile-update endpoint)</span>
              </li>
              <li className="row gap-2">
                <span className="text-danger" aria-hidden="true">{"—"}</span>
                <span className="grow">Delete account or erase records (no endpoint)</span>
              </li>
              <li className="row gap-2">
                <span className="text-danger" aria-hidden="true">{"—"}</span>
                <span className="grow">Export data (no endpoint)</span>
              </li>
            </ul>
          </Card>
        </div>
      </div>
    </>
  );
}
