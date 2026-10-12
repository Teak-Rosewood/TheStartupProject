# Mobile starter (Expo)

Source only. It has **not** been installed, built or run in this repository's tooling, so treat it as a starting point and expect to adjust versions.

- Sign-in: authorization code with PKCE through the Cognito hosted UI (`expo-auth-session`); the token is kept in the device keychain (`expo-secure-store`).
- Shows the signed-in user and organizations from `GET /api/me`.
- Setup: edit `app.json` `extra` with the stack outputs `UserPoolIssuer`, `UserPoolMobileClientId` and `HostedLoginUrl`; add `mystartup://callback` as a callback URL on the mobile app client; also `npx expo install expo-constants`.
- Push notifications: register the device token with `PUT /api/me/devices` (see [jobs and notifications](../../../docs/modules/jobs-notify.md)).
- App store rules: digital goods sold in an iOS app generally must use Apple in-app purchase, not Razorpay **(verify)**; see [pricing and billing](../../../docs/startup/05-pricing-and-billing.md).
