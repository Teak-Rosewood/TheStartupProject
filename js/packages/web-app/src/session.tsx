import type { User, UserManager } from "oidc-client-ts";
import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { Api, ApiError, type Me } from "./api";
import { logoutUrl } from "./auth";
import type { AppConfig } from "./config";

interface Session {
  cfg: AppConfig;
  api: Api;
  me: Me;
  reload: () => Promise<void>;
  signOut: () => Promise<void>;
}

const Ctx = createContext<Session | null>(null);
export const useSession = () => {
  const s = useContext(Ctx);
  if (!s) throw new Error("useSession outside <SessionProvider>");
  return s;
};

/** Signs the visitor in (redirecting to the hosted login when needed), then loads /me. */
export function SessionProvider({ cfg, auth, children }: { cfg: AppConfig; auth: UserManager; children: ReactNode }) {
  const [user, setUser] = useState<User | null | undefined>(undefined);
  const [me, setMe] = useState<Me | null>(null);
  const [error, setError] = useState<string | null>(null);
  const api = useMemo(() => new Api(() => user?.access_token), [user]);

  useEffect(() => {
    auth.getUser().then((u) => {
      if (u && !u.expired) setUser(u);
      else {
        setUser(null);
        void auth.signinRedirect({ state: window.location.pathname.replace(/^\/app/, "") || "/" });
      }
    });
  }, [auth]);

  const reload = async () => {
    try {
      setMe(await api.me());
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) await auth.removeUser();
      setError(e instanceof Error ? e.message : String(e));
    }
  };
  useEffect(() => {
    if (user) void reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user]);

  if (error) return <p role="alert">Could not load your account: {error}</p>;
  if (!user || !me) return <p>Loading…</p>;
  const signOut = async () => {
    await auth.removeUser();
    window.location.assign(logoutUrl(cfg));
  };
  return <Ctx.Provider value={{ cfg, api, me, reload, signOut }}>{children}</Ctx.Provider>;
}
