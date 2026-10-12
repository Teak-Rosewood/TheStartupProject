# 00 · Account setup

Do this once, by hand, before any CI runs.

## 1. Secure the account
1. Create a dedicated AWS account for this product (not a personal sandbox).
2. Root user: enable MFA (hardware key or authenticator), delete root access keys, set the account alternate contacts.
3. Create a human admin through **IAM Identity Center** (free) and use SSO for the CLI: `aws configure sso`. Do not create IAM users with long-lived keys.
4. Enable **AWS Budgets** (the first two are free): a monthly cost budget with alerts at 50%, 80%, 100% of your limit, and **Cost Anomaly Detection** to your email. CDK also creates a budget (see [observability](08-observability.md)), but set the first one by hand so it exists before anything is deployed.

## 2. Pick regions
- Primary: `ap-south-1` (host, database, cache, DynamoDB, Cognito).
- Global: `us-east-1` (CloudFront certificate, frontend bucket).

## 3. Bootstrap CDK in both regions
```bash
cd python
uv sync --all-extras
export CDK_DEFAULT_ACCOUNT=<account-id>
uv run --directory apps/infra cdk bootstrap aws://$CDK_DEFAULT_ACCOUNT/ap-south-1 aws://$CDK_DEFAULT_ACCOUNT/us-east-1
```

## 4. Create the GitHub OIDC trust
CI authenticates to AWS with short-lived OIDC tokens. No AWS keys are ever stored in GitHub. The `OidcStack` creates the identity provider and three roles: read-only (PR `cdk diff`), deploy (main/tag, scoped to the repo and the `prod` environment), and a restore-drill role.
```bash
uv run --directory apps/infra cdk deploy OidcStack
```
Copy the printed role ARNs into GitHub repository variables: `AWS_DEPLOY_ROLE_ARN`, `AWS_DIFF_ROLE_ARN`, `AWS_ACCOUNT_ID`.

## 5. Quotas and email
- SES starts in sandbox mode. Request **production access** in `ap-south-1` before launch, or Cognito will only email verified addresses.
- Check that the account has default quotas for EC2 vCPUs (new accounts can start low).
