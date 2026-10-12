import type { UserManager } from "oidc-client-ts";
import { useEffect } from "react";
import { BrowserRouter, Link, Navigate, Route, Routes, useNavigate } from "react-router-dom";
import type { AppConfig } from "./config";
import { BillingPage, Keys, Org, Orgs, Settings } from "./pages";
import { SessionProvider, useSession } from "./session";

function Callback({ auth }: { auth: UserManager }) {
  const nav = useNavigate();
  useEffect(() => {
    auth.signinRedirectCallback().then(
      (u) => nav(typeof u.state === "string" ? u.state : "/", { replace: true }),
      () => nav("/", { replace: true }),
    );
  }, [auth, nav]);
  return <p>Signing you in…</p>;
}

function Shell() {
  const { me, signOut } = useSession();
  return (
    <>
      <nav>
        <Link to="/">Organizations</Link> · <Link to="/settings">Settings</Link> · {me.email}{" "}
        <button onClick={signOut}>Sign out</button>
      </nav>
      <main>
        <Routes>
          <Route path="/" element={<Orgs />} />
          <Route path="/orgs/:orgId" element={<Org />} />
          <Route path="/orgs/:orgId/billing" element={<BillingPage />} />
          <Route path="/orgs/:orgId/keys" element={<Keys />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </>
  );
}

export function App({ cfg, auth }: { cfg: AppConfig; auth: UserManager }) {
  return (
    <BrowserRouter basename="/app">
      <Routes>
        <Route path="/callback" element={<Callback auth={auth} />} />
        <Route
          path="*"
          element={
            <SessionProvider cfg={cfg} auth={auth}>
              <Shell />
            </SessionProvider>
          }
        />
      </Routes>
    </BrowserRouter>
  );
}
