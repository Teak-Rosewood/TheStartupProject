"""GitHub Actions -> AWS trust via OIDC: no long-lived AWS keys exist anywhere."""

from __future__ import annotations

import aws_cdk as cdk
import aws_cdk.aws_iam as iam
from constructs import Construct

from .config import EnvConfig
from .edge import frontend_bucket_name

ISSUER = "token.actions.githubusercontent.com"


class GithubOidc(Construct):
    def __init__(self, scope: Construct, id: str, *, env: EnvConfig) -> None:
        super().__init__(scope, id)
        gh = env.github
        repo = f"{gh.owner}/{gh.repo}"
        acct = env.account

        provider = iam.OpenIdConnectProvider(
            self, "Provider", url=f"https://{ISSUER}", client_ids=["sts.amazonaws.com"]
        )

        def role(name: str, subjects: list[str]) -> iam.Role:
            return iam.Role(
                self,
                name,
                role_name=f"{env.prefix}-gha-{name.lower()}",
                max_session_duration=cdk.Duration.hours(1),
                assumed_by=iam.WebIdentityPrincipal(
                    provider.open_id_connect_provider_arn,
                    {
                        "StringEquals": {f"{ISSUER}:aud": "sts.amazonaws.com"},
                        "StringLike": {f"{ISSUER}:sub": subjects},
                    },
                ),
            )

        def cdk_roles(kind: str) -> str:
            return f"arn:aws:iam::{acct}:role/cdk-*-{kind}-role-{acct}-*"

        # Pull requests: read-only. They can look things up and diff, nothing else.
        self.diff = role("Diff", [f"repo:{repo}:pull_request"])
        self.diff.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name("ReadOnlyAccess")
        )
        self.diff.add_to_policy(
            iam.PolicyStatement(actions=["sts:AssumeRole"], resources=[cdk_roles("lookup")])
        )

        # Deploys: only jobs running in the protected GitHub environment.
        self.deploy = role("Deploy", [f"repo:{repo}:environment:{gh.environment}"])
        self.deploy.add_to_policy(
            iam.PolicyStatement(
                actions=["sts:AssumeRole"],
                resources=[
                    cdk_roles(k)
                    for k in ("deploy", "file-publishing", "image-publishing", "lookup")
                ],
            )
        )
        self.deploy.add_to_policy(
            iam.PolicyStatement(actions=["ecr:GetAuthorizationToken"], resources=["*"])
        )
        self.deploy.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "ecr:BatchCheckLayerAvailability",
                    "ecr:CompleteLayerUpload",
                    "ecr:InitiateLayerUpload",
                    "ecr:PutImage",
                    "ecr:UploadLayerPart",
                    "ecr:BatchGetImage",
                    "ecr:DescribeImages",
                ],
                resources=[f"arn:aws:ecr:{env.region}:{acct}:repository/{env.prefix}-*"],
            )
        )
        self.deploy.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "ecs:RunTask",
                    "ecs:DescribeTasks",
                    "ecs:DescribeServices",
                    "ecs:DescribeTaskDefinition",
                    "ecs:ListTasks",
                    "ecs:StopTask",
                ],
                resources=["*"],
                conditions={
                    "ArnLike": {
                        "ecs:cluster": f"arn:aws:ecs:{env.region}:{acct}:cluster/{env.prefix}-*"
                    }
                },
            )
        )
        self.deploy.add_to_policy(
            iam.PolicyStatement(
                actions=["ecs:DescribeTaskDefinition", "ecs:RegisterTaskDefinition"],
                resources=["*"],
            )
        )
        self.deploy.add_to_policy(
            iam.PolicyStatement(
                actions=["iam:PassRole"],
                resources=[f"arn:aws:iam::{acct}:role/{env.prefix}-*"],
                conditions={"StringEquals": {"iam:PassedToService": "ecs-tasks.amazonaws.com"}},
            )
        )
        bucket = frontend_bucket_name(env)
        self.deploy.add_to_policy(
            iam.PolicyStatement(
                actions=["s3:PutObject", "s3:DeleteObject", "s3:GetObject", "s3:ListBucket"],
                resources=[f"arn:aws:s3:::{bucket}", f"arn:aws:s3:::{bucket}/*"],
            )
        )
        self.deploy.add_to_policy(
            iam.PolicyStatement(
                actions=["cloudfront:CreateInvalidation", "cloudfront:GetInvalidation"],
                resources=["*"],
            )
        )
        self.deploy.add_to_policy(
            iam.PolicyStatement(
                actions=["cloudformation:DescribeStacks", "ssm:GetParameter"],
                resources=["*"],
            )
        )

        # Scheduled jobs on the default branch (backup restore drill).
        self.drill = role("Drill", [f"repo:{repo}:ref:refs/heads/{gh.branch}"])
        self.drill.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name("ReadOnlyAccess")
        )

        cdk.CfnOutput(self, "DiffRoleArn", value=self.diff.role_arn)
        cdk.CfnOutput(self, "DeployRoleArn", value=self.deploy.role_arn)
        cdk.CfnOutput(self, "DrillRoleArn", value=self.drill.role_arn)
