# 14 · Payments setup

Gateway credentials never pass through CloudFormation. Create the secret by hand, then enable the gateway in config.

## Razorpay
```bash
aws secretsmanager create-secret --region ap-south-1 --name /tsp-prod/payments/razorpay \
  --secret-string '{"key_id":"rzp_live_...","key_secret":"...","webhook_secret":"..."}'
```
Use **test** keys (`rzp_test_...`) in a separate secret value first, then replace with live keys at launch (`aws secretsmanager put-secret-value`) and redeploy the API service so it picks them up.

## Stripe (optional)
```bash
aws secretsmanager create-secret --region ap-south-1 --name /tsp-prod/payments/stripe \
  --secret-string '{"secret_key":"sk_live_...","webhook_secret":"whsec_..."}'
```

## Config (`python/apps/infra/config/prod.py`)
```python
payments=PaymentsConfig(
    razorpay=True,
    seller=SellerConfig(legal_name="Acme Technologies Private Limited", gstin="29ABCDE1234F1Z5",
                        address="1 Example Street, Bengaluru 560001", state_code="29", sac="998314",
                        gst_rate=18, lut=True),
)
```
`state_code` must equal the first two digits of the GSTIN. Confirm the SAC code and rate with your CA.

## What the stack adds
- A private, **versioned, retained** invoices bucket (`INVOICE_BUCKET`); the API may write and read `invoices/*` only. Keep invoices for the period your CA specifies (commonly 8 years).
- The secrets above injected into the API container as `RAZORPAY_*` / `STRIPE_*` (never as plain environment values).
- `SELLER_*` environment values from the config.
- Webhook URL: `https://<your-domain>/api/webhooks/razorpay`. It goes through CloudFront like every `/api` call.

## Plans
Edit `python/apps/api/plans.toml` (baked into the image) and deploy; plans sync on start.

## Cost
Secrets Manager $0.40 per secret per month; the invoices bucket is pennies. Gateway fees apply per transaction (check the current rates for UPI, cards and international cards).
