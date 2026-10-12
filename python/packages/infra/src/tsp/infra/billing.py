"""Payments support: a retained invoices bucket and gateway credentials injected from Secrets Manager."""

from __future__ import annotations

import aws_cdk as cdk
import aws_cdk.aws_ecs as ecs
import aws_cdk.aws_iam as iam
import aws_cdk.aws_s3 as s3
import aws_cdk.aws_secretsmanager as secretsmanager
from constructs import Construct

from .config import EnvConfig


class Billing(Construct):
    """Invoices are legal records: private, versioned, never deleted by the stack."""

    def __init__(self, scope: Construct, id: str, *, config: EnvConfig) -> None:
        super().__init__(scope, id)
        self.invoices = s3.Bucket(
            self,
            "Invoices",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            versioned=True,
            removal_policy=cdk.RemovalPolicy.RETAIN,
            lifecycle_rules=[s3.LifecycleRule(noncurrent_version_expiration=cdk.Duration.days(90))],
        )
        self.environment: dict[str, str] = {"INVOICE_BUCKET": self.invoices.bucket_name}
        self.secrets: dict[str, ecs.Secret] = {}
        p = config.payments
        if p.seller:
            s = p.seller
            self.environment.update(
                SELLER_LEGAL_NAME=s.legal_name,
                SELLER_GSTIN=s.gstin,
                SELLER_ADDRESS=s.address,
                SELLER_STATE_CODE=s.state_code,
                SELLER_SAC=s.sac,
                SELLER_GST_RATE=str(s.gst_rate),
                SELLER_LUT="1" if s.lut else "0",
            )
        # These secrets are created by hand (see the docs) so the keys never pass through CloudFormation.
        if p.razorpay:
            sec = secretsmanager.Secret.from_secret_name_v2(
                self, "Razorpay", f"/{config.prefix}/payments/razorpay"
            )
            self.secrets.update(
                RAZORPAY_KEY_ID=ecs.Secret.from_secrets_manager(sec, "key_id"),
                RAZORPAY_KEY_SECRET=ecs.Secret.from_secrets_manager(sec, "key_secret"),
                RAZORPAY_WEBHOOK_SECRET=ecs.Secret.from_secrets_manager(sec, "webhook_secret"),
            )
        if p.stripe:
            sec = secretsmanager.Secret.from_secret_name_v2(
                self, "Stripe", f"/{config.prefix}/payments/stripe"
            )
            self.secrets.update(
                STRIPE_SECRET_KEY=ecs.Secret.from_secrets_manager(sec, "secret_key"),
                STRIPE_WEBHOOK_SECRET=ecs.Secret.from_secrets_manager(sec, "webhook_secret"),
            )
            self.environment["STRIPE_SUCCESS_URL"] = f"https://{config.domain.app}/billing/success"
            self.environment["STRIPE_CANCEL_URL"] = f"https://{config.domain.app}/billing/cancelled"

    def grant(self, role: iam.IRole) -> None:
        role.add_to_principal_policy(
            iam.PolicyStatement(
                actions=["s3:PutObject", "s3:GetObject"],
                resources=[self.invoices.arn_for_objects("invoices/*")],
            )
        )
