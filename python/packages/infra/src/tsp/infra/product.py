"""Product features: customer uploads bucket and AI gateway credentials."""

from __future__ import annotations

import aws_cdk as cdk
import aws_cdk.aws_ecs as ecs
import aws_cdk.aws_iam as iam
import aws_cdk.aws_s3 as s3
import aws_cdk.aws_secretsmanager as secretsmanager
from constructs import Construct

from .config import EnvConfig


class Uploads(Construct):
    """Private bucket; browsers upload with presigned POSTs that S3 itself limits by size and type."""

    def __init__(self, scope: Construct, id: str, *, config: EnvConfig) -> None:
        super().__init__(scope, id)
        u = config.uploads
        self.bucket = s3.Bucket(
            self,
            "Bucket",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            removal_policy=cdk.RemovalPolicy.RETAIN,
            lifecycle_rules=[
                s3.LifecycleRule(abort_incomplete_multipart_upload_after=cdk.Duration.days(1))
            ],
            cors=[
                s3.CorsRule(
                    allowed_origins=[f"https://{config.domain.app}"],
                    allowed_methods=[s3.HttpMethods.POST, s3.HttpMethods.GET],
                    allowed_headers=["*"],
                    max_age=3000,
                )
            ],
        )
        self.environment = {
            "UPLOAD_BUCKET": self.bucket.bucket_name,
            "UPLOAD_MAX_MB": str(u.max_mb),
            "UPLOAD_ORG_QUOTA_MB": str(u.org_quota_mb),
        }

    def grant(self, role: iam.IRole) -> None:
        role.add_to_principal_policy(
            iam.PolicyStatement(
                actions=["s3:PutObject", "s3:GetObject", "s3:DeleteObject"],
                resources=[self.bucket.arn_for_objects("uploads/*")],
            )
        )


class AiGateway(Construct):
    def __init__(self, scope: Construct, id: str, *, config: EnvConfig) -> None:
        super().__init__(scope, id)
        a = config.ai
        self.environment: dict[str, str] = {
            "AI_VERIFY_FREE_ONLY": "1" if a.verify_free_only else "0",
            "AI_CREDITS_PER_1K_IN": str(a.credits_per_1k_in),
            "AI_CREDITS_PER_1K_OUT": str(a.credits_per_1k_out),
        }
        self.secrets: dict[str, ecs.Secret] = {}
        if a.provider_keys:
            # Created by hand so provider keys never pass through CloudFormation.
            sec = secretsmanager.Secret.from_secret_name_v2(
                self, "Keys", f"/{config.prefix}/ai/keys"
            )
            for name in a.provider_keys:
                self.secrets[name] = ecs.Secret.from_secrets_manager(sec, name)
