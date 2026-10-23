/**
 * Sign-in / registration.
 *
 * Calls the real `POST /v1/auth/login` and `POST /v1/auth/register` endpoints.
 * Server errors are shown verbatim (they are already bounded and safe); no
 * successful login is ever fabricated, and no demo credentials are hardcoded.
 */

import { useState, type FormEvent } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import { BookOpenCheck, Eye, EyeOff, LockKeyhole, ShieldCheck, Sparkles } from "lucide-react";
import { useAuth } from "@/auth/AuthContext";
import { Alert, Button, Field } from "@/components/ui";
import { toUserMessage } from "@/components/ui";
import { ApiClientError } from "@/services/http";

export function LoginPage() {
  const { status, signIn, signUp } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();

  const [mode, setMode] = useState<"signin" | "signup">("signin");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<{ email?: string; password?: string }>({});

  if (status === "authenticated") {
    const from = (location.state as { from?: string } | null)?.from ?? "/";
    return <Navigate to={from} replace />;
  }

  function validate(): boolean {
    const next: { email?: string; password?: string } = {};
    if (!email.trim()) next.email = "Email is required.";
    else if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.trim()))
      next.email = "Enter a valid email address.";
    if (!password) next.password = "Password is required.";
    else if (mode === "signup" && password.length < 10)
      next.password = "Password must be at least 10 characters.";
    setFieldErrors(next);
    return Object.keys(next).length === 0;
  }

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    if (!validate()) return;

    setBusy(true);
    try {
      if (mode === "signin") {
        await signIn({ email: email.trim(), password });
      } else {
        await signUp({
          email: email.trim(),
          password,
          ...(displayName.trim() ? { display_name: displayName.trim() } : {}),
        });
      }
      const from = (location.state as { from?: string } | null)?.from ?? "/";
      navigate(from, { replace: true });
    } catch (err) {
      if (err instanceof ApiClientError && err.status === 422) setError(toUserMessage(err));
      else if (err instanceof ApiClientError && err.status === 401)
        setError("That email and password combination was not accepted.");
      else setError(toUserMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="auth">
      <aside className="auth__aside">
        <div className="brand" style={{ padding: 0 }}>
          <span className="brand__mark" aria-hidden="true">
            <BookOpenCheck />
          </span>
          <span className="brand__name">Unmist</span>
        </div>

        <div>
          <h1 className="auth__headline">Knowledge you can actually evidence.</h1>
          <p className="auth__sub">
            Unmist connects source material to extracted text, structured question and answer
            segments, and the human review behind them — so what you learn, what you can
            demonstrate, and the evidence you hold stay linked.
          </p>

          <div className="auth__points">
            <div className="auth__point">
              <ShieldCheck />
              <span>
                <strong>Server-side authorization</strong>
                Every read and write is scoped to your account and tenant.
              </span>
            </div>
            <div className="auth__point">
              <Sparkles />
              <span>
                <strong>Review before trust</strong>
                Structure extraction is marked as candidate output until a human confirms it.
              </span>
            </div>
            <div className="auth__point">
              <LockKeyhole />
              <span>
                <strong>Private originals</strong>
                Uploaded documents are served only through authorized API paths.
              </span>
            </div>
          </div>
        </div>

        <p className="text-xs" style={{ color: "var(--shell-text-dim)" }}>
          Access tokens are held in memory for this session.
        </p>
      </aside>

      <main className="auth__main">
        <form className="auth__card" onSubmit={onSubmit} noValidate>
          <div>
            <h2 className="auth__title">{mode === "signin" ? "Sign in" : "Create account"}</h2>
            <p className="text-secondary text-sm mt-2">
              {mode === "signin"
                ? "Continue to your Unmist workspace."
                : "Registration uses the real account API and issues a server session."}
            </p>
          </div>

          {error && <Alert tone="danger" title="Could not sign in">{error}</Alert>}

          <Field label="Email" htmlFor="email" error={fieldErrors.email ?? null} required>
            <input
              id="email"
              className="input"
              type="email"
              autoComplete="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              aria-invalid={fieldErrors.email ? "true" : undefined}
              placeholder="you@example.com"
            />
          </Field>

          {mode === "signup" && (
            <Field label="Display name" htmlFor="name" hint="Optional">
              <input
                id="name"
                className="input"
                type="text"
                autoComplete="name"
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                placeholder="How your name appears"
              />
            </Field>
          )}

          <Field
            label="Password"
            htmlFor="password"
            error={fieldErrors.password ?? null}
            hint={mode === "signup" ? "Minimum 10 characters (server requirement)." : undefined}
            required
          >
            <div className="input-group">
              <input
                id="password"
                className="input"
                type={showPassword ? "text" : "password"}
                autoComplete={mode === "signin" ? "current-password" : "new-password"}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                aria-invalid={fieldErrors.password ? "true" : undefined}
                style={{ paddingLeft: 34, paddingRight: 40 }}
              />
              <LockKeyhole
                aria-hidden="true"
                style={{ position: "absolute", left: 11, width: 16, height: 16, color: "var(--text-muted)" }}
              />
              <button
                type="button"
                className="btn btn--ghost btn--sm btn--icon"
                style={{ position: "absolute", right: 4 }}
                onClick={() => setShowPassword((v) => !v)}
                aria-label={showPassword ? "Hide password" : "Show password"}
              >
                {showPassword ? <EyeOff /> : <Eye />}
              </button>
            </div>
          </Field>

          <Button type="submit" variant="primary" size="lg" block loading={busy}>
            {mode === "signin" ? "Sign in" : "Create account"}
          </Button>

          <p className="auth__switch">
            {mode === "signin" ? "Need an account? " : "Already registered? "}
            <button
              type="button"
              className="btn btn--ghost btn--sm"
              style={{ padding: "0 4px", height: "auto", color: "var(--accent)" }}
              onClick={() => {
                setMode(mode === "signin" ? "signup" : "signin");
                setError(null);
                setFieldErrors({});
              }}
            >
              {mode === "signin" ? "Create one" : "Sign in instead"}
            </button>
          </p>
        </form>
      </main>
    </div>
  );
}
