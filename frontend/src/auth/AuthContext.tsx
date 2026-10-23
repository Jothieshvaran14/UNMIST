/**
 * Authentication context.
 *
 * Real flow only: sign-in/registration call `POST /v1/auth/*`, the returned
 * tokens are handled by `services/session`, and `/v1/auth/me` supplies the
 * profile shown in the shell. A failed login is never faked — the error from
 * the server is displayed.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { ApiClientError } from "@/services/http";
import * as api from "@/services/api";
import {
  bootstrapSession,
  clearSession,
  isSignedIn,
  setSession,
  subscribeAuth,
} from "@/services/session";
import type { LoginRequest, RegisterRequest, UserProfile } from "@/types/api";

type AuthStatus = "booting" | "anonymous" | "authenticated";

interface AuthContextValue {
  status: AuthStatus;
  user: UserProfile | null;
  /** Set when `/auth/me` itself failed for a signed-in session. */
  profileError: string | null;
  signIn: (payload: LoginRequest) => Promise<void>;
  signUp: (payload: RegisterRequest) => Promise<void>;
  signOut: () => Promise<void>;
  refreshProfile: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AuthStatus>("booting");
  const [user, setUser] = useState<UserProfile | null>(null);
  const [profileError, setProfileError] = useState<string | null>(null);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const loadProfile = useCallback(async () => {
    try {
      const profile = await api.me();
      if (!mounted.current) return;
      setUser(profile);
      setProfileError(null);
      setStatus("authenticated");
    } catch (err) {
      if (!mounted.current) return;
      if (err instanceof ApiClientError && err.status === 401) {
        clearSession();
        setUser(null);
        setStatus("anonymous");
        return;
      }
      // Signed in but the profile call failed (network/server). Keep the
      // session and surface the failure rather than pretending all is well.
      setProfileError(err instanceof Error ? err.message : "Could not load your profile.");
      setStatus("authenticated");
    }
  }, []);

  // Boot: restore a session from a persisted refresh token, then load /me.
  useEffect(() => {
    let cancelled = false;
    bootstrapSession()
      .then((restored) => {
        if (cancelled || !mounted.current) return;
        if (restored && isSignedIn()) void loadProfile();
        else setStatus("anonymous");
      })
      .catch(() => {
        if (!cancelled && mounted.current) setStatus("anonymous");
      });
    return () => {
      cancelled = true;
    };
  }, [loadProfile]);

  // React to session changes triggered elsewhere (e.g. a failed refresh).
  useEffect(
    () =>
      subscribeAuth((signedIn) => {
        if (!mounted.current) return;
        if (!signedIn) {
          setUser(null);
          setStatus("anonymous");
        }
      }),
    [],
  );

  const signIn = useCallback(
    async (payload: LoginRequest) => {
      const tokens = await api.login(payload);
      setSession(tokens);
      await loadProfile();
    },
    [loadProfile],
  );

  const signUp = useCallback(
    async (payload: RegisterRequest) => {
      // `POST /v1/auth/register` creates the account but returns `UserResponse`
      // (201) with no tokens. A session is only established by a subsequent
      // `POST /v1/auth/login`, so both calls are made explicitly here rather
      // than pretending registration signed the user in.
      await api.register(payload);
      const tokens = await api.login({ email: payload.email, password: payload.password });
      setSession(tokens);
      await loadProfile();
    },
    [loadProfile],
  );

  const signOut = useCallback(async () => {
    try {
      await api.logout();
    } catch {
      // A failed server-side logout must still end the local session.
    }
    clearSession();
    setUser(null);
    setProfileError(null);
    setStatus("anonymous");
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({
      status,
      user,
      profileError,
      signIn,
      signUp,
      signOut,
      refreshProfile: loadProfile,
    }),
    [status, user, profileError, signIn, signUp, signOut, loadProfile],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside <AuthProvider>");
  return ctx;
}
