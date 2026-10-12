import { UserManager, WebStorageStateStore } from "oidc-client-ts";
import type { AppConfig } from "./config";

/** Authorization-code flow with PKCE against the Cognito user pool. Tokens stay in sessionStorage. */
export function createAuth(cfg: AppConfig): UserManager {
  const origin = window.location.origin;
  return new UserManager({
    authority: cfg.issuer,
    client_id: cfg.clientId,
    redirect_uri: `${origin}/app/callback`,
    post_logout_redirect_uri: `${origin}/app/`,
    response_type: "code",
    scope: "openid email profile",
    userStore: new WebStorageStateStore({ store: window.sessionStorage }),
    automaticSilentRenew: false,
    // Cognito has no end_session_endpoint in its discovery document; sign-out is built by hand below.
    metadataSeed: { end_session_endpoint: `${cfg.hostedLoginUrl}/logout` },
  });
}

export function logoutUrl(cfg: AppConfig): string {
  const q = new URLSearchParams({ client_id: cfg.clientId, logout_uri: `${window.location.origin}/app/` });
  return `${cfg.hostedLoginUrl}/logout?${q}`;
}
