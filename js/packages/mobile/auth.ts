import Constants from "expo-constants";
import * as AuthSession from "expo-auth-session";
import * as SecureStore from "expo-secure-store";
import * as WebBrowser from "expo-web-browser";

WebBrowser.maybeCompleteAuthSession();
const extra = Constants.expoConfig!.extra as Record<string, string>;
const KEY = "tsp.access_token";

/** Authorization-code flow with PKCE against the same Cognito pool as the web app (use the mobile app client). */
export function useSignIn() {
  const discovery = AuthSession.useAutoDiscovery(extra.issuer);
  const redirectUri = AuthSession.makeRedirectUri({ scheme: "mystartup", path: "callback" });
  const [request, , prompt] = AuthSession.useAuthRequest(
    { clientId: extra.clientId, redirectUri, scopes: ["openid", "email", "profile"], usePKCE: true },
    discovery,
  );
  return async (): Promise<string | null> => {
    const res = await prompt();
    if (res.type !== "success" || !discovery || !request) return null;
    const tokens = await AuthSession.exchangeCodeAsync(
      { clientId: extra.clientId, code: res.params.code, redirectUri, extraParams: { code_verifier: request.codeVerifier! } },
      discovery,
    );
    await SecureStore.setItemAsync(KEY, tokens.accessToken);
    return tokens.accessToken;
  };
}

export const storedToken = () => SecureStore.getItemAsync(KEY);
export const signOut = () => SecureStore.deleteItemAsync(KEY);

export async function api<T>(path: string, token: string): Promise<T> {
  const res = await fetch(`${extra.apiBase}${path}`, { headers: { Authorization: `Bearer ${token}` } });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json() as Promise<T>;
}
