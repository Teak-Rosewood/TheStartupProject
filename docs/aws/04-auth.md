# 04 · Authentication (Cognito)

Cognito owns accounts, passwords, social login, passkeys and MFA. The Python API never sees a password. It only verifies the tokens Cognito issues (`tsp.auth`).

## Tier and cost
Use the **Essentials** tier. It includes managed login (hosted UI), passkeys, one-time codes and MFA. Monthly active users are free up to 10,000 under current pricing; verify before launch. SMS messages are billed separately, so prefer email codes, TOTP and passkeys over SMS.

## What `tsp.infra` creates
- One user pool, sign-in by email, self sign-up on, email verification required, strong password policy.
- Identity providers: **Google** and **Sign in with Apple** (enabled with `auth.google` / `auth.apple` once their Secrets Manager secrets exist).
- Passwordless: email one-time code and WebAuthn passkeys enabled as first-factor choices alongside password.
- Two app clients, both using the authorization-code flow with PKCE and no client secret: `web` (single-page app) and `mobile` (React Native, custom scheme redirect).
- Hosted login on a Cognito domain by default. Set `auth.custom_domain: True` to use `auth.<your-domain>`; that creates a us-east-1 certificate you validate by DNS (see [edge and domain](03-edge-and-domain.md)) and the deploy waits for it.
- Email through Cognito's built-in sender (50 messages a day) until you set `auth.ses_from` to a verified SES address.
- `AUTH_ISSUER` and `AUTH_CLIENT_IDS` are passed to the API container as environment variables.

## Manual steps
### Google
1. Google Cloud Console → create a project → OAuth consent screen (external).
2. Credentials → OAuth client ID (Web). Authorized redirect URI: `https://auth.<your-domain>/oauth2/idpresponse`.
3. Store the client ID and secret in Secrets Manager, then set `auth.google: True` and deploy:
```bash
aws secretsmanager create-secret --region ap-south-1 --name /tsp-prod/auth/google \
  --secret-string '{"client_id":"...","client_secret":"..."}'
```

### Apple
Requires a paid Apple Developer account (and is mandatory on iOS apps that offer any other social login).
1. Create an App ID with *Sign In with Apple*, then a **Services ID** (this is the client ID). Set the return URL to `https://auth.<your-domain>/oauth2/idpresponse` and the domain to `auth.<your-domain>`.
2. Create a Sign in with Apple **key** and download the `.p8` file. Note the Team ID and Key ID.
3. Store them in Secrets Manager, then set `auth.apple: True` and deploy:
```bash
aws secretsmanager create-secret --region ap-south-1 --name /tsp-prod/auth/apple \
  --secret-string "$(jq -n --arg k "$(cat AuthKey_XXXX.p8)" '{services_id:"com.example.web",team_id:"TEAMID",key_id:"KEYID",private_key:$k}')"
```

### Passkeys
Passkeys are bound to the domain that serves the login page (the *relying party ID*): the custom auth domain if enabled, otherwise the Cognito domain. Decide before launch, because moving to a custom domain later invalidates existing passkeys.

### SES production access
In `ap-south-1`, verify your domain (add the DKIM CNAMEs SES gives you at your registrar) and request production access. Until approved, only verified addresses can receive mail.

## Using it from the API
```python
from fastapi import Depends
from tsp.auth import Claims
from tsp.server import current_user, require_groups

@router.get("/me")
async def me(user: Claims = Depends(current_user)): ...

@router.get("/admin")
async def admin(user: Claims = Depends(require_groups("admin"))): ...
```
`tsp.auth.CognitoVerifier` does the token checks and has no web-framework dependency; `tsp.server` wraps it as FastAPI dependencies. Set `AUTH_ISSUER` and `AUTH_CLIENT_IDS` (the stack sets both).
Access tokens are validated for signature, `iss`, expiry, `token_use` and `client_id`. Groups in the `cognito:groups` claim drive role checks (`require_groups("admin")`).

## Frontend
Use the hosted login page with PKCE (for example `oidc-client-ts` for web, `expo-auth-session` for React Native). Never embed a client secret in a front end.
