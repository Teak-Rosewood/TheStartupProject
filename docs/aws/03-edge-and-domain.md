# 03 · Edge and domain

## What CloudFront does
One distribution, your domain, two behaviors:
- default path → S3 frontend bucket (private, reached through Origin Access Control). A small CloudFront Function rewrites unknown paths to `index.html` for single-page apps and adds security headers.
- `/api/*` → the host (stage 0) or the load balancer through a VPC origin (stage 2). Caching is off for the API unless you opt specific paths in; all headers needed for auth (`Authorization`) are forwarded.

CloudFront is also your edge layer. There are no separate edge compute nodes. Add CloudFront Functions or Lambda@Edge only if you measure a need (A/B routing, per-country logic).

CloudFront pricing has a free plan and paid flat-rate plans in addition to pay-as-you-go. Check the current options and their request/transfer limits when you deploy, and pick the cheapest that covers your traffic. Plan choice is a config value.

## Certificates
CloudFront requires its certificate in **us-east-1**. `tsp.infra` creates an ACM certificate there for your domain (`app.example.com`, optionally `api` and `auth` names) using DNS validation.

## DNS at an external registrar
Your DNS is not in Route 53, so CDK cannot create records for you. The deploy prints the records; add them at your registrar:

1. **Certificate validation:** for each domain, a `CNAME` named `_abc123.app.example.com` pointing to the value ACM gives you. Add it before the first deploy finishes, or the stack waits (and times out) at certificate validation.
2. **Site:** `app.example.com` → `CNAME` to `dxxxxxxxxxxxx.cloudfront.net`.
3. **Apex domain** (`example.com`): plain DNS does not allow a CNAME at the apex. Use your provider's flattening: **Cloudflare** (CNAME flattening, set proxy to *DNS only*), **Namecheap/others** (ALIAS/ANAME record). If yours has neither, use `www.` and redirect the apex.
4. **Cognito custom domain** (`auth.example.com`): a `CNAME` to the CloudFront domain Cognito prints. Cognito needs the parent domain to resolve before it accepts the custom domain.

If you would rather automate this, delegate a sub-domain to Route 53 ($0.50/month per hosted zone) and set `domain.route53_zone` in config; CDK then manages every record. Nothing else changes.

## Frontend deploys
CI runs `aws s3 sync` of your build output followed by a CloudFront invalidation of `/index.html`. Hashed asset files are cached for a year.
