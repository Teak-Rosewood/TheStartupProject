"""Private bucket for personal-data export files. Files expire after 7 days; links are presigned."""

from __future__ import annotations

import aws_cdk as cdk
import aws_cdk.aws_iam as iam
import aws_cdk.aws_s3 as s3
from constructs import Construct

from .config import EnvConfig


class Exports(Construct):
    def __init__(self, scope: Construct, id: str, *, config: EnvConfig) -> None:
        super().__init__(scope, id)
        self.bucket = s3.Bucket(
            self,
            "Bucket",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            removal_policy=cdk.RemovalPolicy.RETAIN,
            lifecycle_rules=[
                s3.LifecycleRule(
                    expiration=cdk.Duration.days(7),
                    abort_incomplete_multipart_upload_after=cdk.Duration.days(1),
                )
            ],
        )

    def grant(self, role: iam.IRole) -> None:
        """Write export files and read them back to sign links: nothing else."""
        role.add_to_principal_policy(
            iam.PolicyStatement(
                actions=["s3:PutObject", "s3:GetObject"],
                resources=[self.bucket.arn_for_objects("exports/*")],
            )
        )
