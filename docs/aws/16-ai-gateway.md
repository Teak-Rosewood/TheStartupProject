# 16 · AI gateway and uploads setup

## Provider keys
1. Create one secret by hand, so keys never pass through CloudFormation:
   ```bash
   aws secretsmanager create-secret --region ap-south-1 --name /tsp-prod/ai/keys \
     --secret-string '{"GROQ_KEY":"...","OPENROUTER_KEY":"..."}'
   ```
   The name is `/{prefix}/ai/keys`; `prefix` is `tsp-prod` for production.
2. List the field names in `config/prod.py`: `ai=AiConfig(provider_keys=["GROQ_KEY", "OPENROUTER_KEY"])`. ECS injects each as an environment variable. Deploy.
3. The task role can read the secret only if it is created before the deploy.

Other `AiConfig` settings: `credits_per_1k_in`, `credits_per_1k_out` (what customers are charged, in credits), `default_rpm`, `verify_free_only`.

## Uploads
Set `uploads=UploadsConfig(enabled=True, max_mb=10, org_quota_mb=1024)`. Deploy creates a private bucket and gives the API task read, write and delete on `uploads/*`. See [product operations](../modules/product-ops.md).

## Web app sign-in
The stack outputs `UserPoolWebClientId`, `UserPoolIssuer` and `HostedLoginUrl`. The deploy workflow writes them to `/app/config.json`. To test locally, edit `js/packages/web-app/public/config.json` with the same values and add `http://localhost:5173/app/callback` as a callback URL on the web client.
