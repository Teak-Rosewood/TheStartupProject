"""cdk-nag (AWS Solutions pack): enable it, and acknowledge each finding with its reason.

Every acknowledgment below is a deliberate trade-off for a one-host, low-cost stack.
An unacknowledged finding fails synth, so a new risky resource cannot slip in quietly.
"""

from __future__ import annotations

import aws_cdk as cdk
from cdk_nag import AwsSolutionsChecks
from constructs import Construct

from .config import EnvConfig

_P = "AwsSolutions::AwsSolutions-"

_STATIC: dict[str, str] = {
    "VPC7": "VPC flow logs cost per GB. No NAT, one host: enable when investigating traffic.",
    "ECS4": "Container Insights adds per-metric CloudWatch cost. Alarms cover CPU, memory and disk.",
    "ECS2": "TSP_ORIGIN_VERIFY is a low-sensitivity defense-in-depth header already visible in "
    "the CloudFront template; real secrets use SSM SecureString.",
    "AS3": "Single-instance group: EC2 status-check and ASG alarms notify via the alarm topic.",
    "SNS3": "Topic is the CDK-managed ECS drain hook; only the Auto Scaling service publishes.",
    "L1": "Lambda is a CDK-managed construct (ECS drain hook / custom resource); the runtime "
    "is chosen by the CDK version.",
    "S1": "Access logs on the frontend bucket add cost; CloudFront standard logs are the audit trail.",
    "CFR1": "No geo restriction required; the app is global.",
    "CFR2": "AWS WAF is attached through the CloudFront flat-rate plan or later, not by this stack.",
    "CFR3": "CloudFront access logs add S3 cost; enable when you need request-level forensics.",
    "SMG4": "The Postgres password is only used on the loopback of one host; rotate it with the "
    "documented runbook, which also restarts the database.",
    "COG8": "Plus tier adds per-user cost; threat protection is not needed at this stage.",
    "COG2": "MFA is optional per user (TOTP). Make it required once your users expect it.",
    "RDS3": "Single-AZ at stage 1 to keep cost down; Multi-AZ arrives with stage 4 (docs/aws/10).",
    "RDS6": "The app authenticates with the generated Secrets Manager password; IAM auth is optional.",
    "RDS11": "Default port is acceptable: the instance sits in isolated subnets behind a host-only security group.",
    "CFR5": "Stage 0 reaches the host over HTTP, restricted by the CloudFront prefix-list security "
    "group and a secret header. The VPC origin (stage 2) makes this private.",
}

_MANAGED = {
    "IAM4[Policy::arn:<AWS::Partition>:iam::aws:policy/AmazonSSMManagedInstanceCore]": "AWS-managed SSM core policy for Session Manager (no SSH).",
    "IAM4[Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole]": "CDK-managed Lambda log-writing role.",
    "IAM4[Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AWSDataLifecycleManagerServiceRole]": "AWS-managed role for the EBS snapshot lifecycle policy.",
    "IAM4[Policy::arn:<AWS::Partition>:iam::aws:policy/ReadOnlyAccess]": "Read-only role for PR diffs and the restore drill; it cannot change anything.",
}

_WILDCARD_STATIC = {
    "IAM5[Resource::*]": "Actions without resource-level permissions (ec2:AssociateAddress, "
    "ecr:GetAuthorizationToken, cloudfront invalidation, ECS task registration, CDK custom resources).",
    "IAM5[Action::ecs:Submit*]": "Standard ECS container-instance permissions added by CDK.",
    "IAM5[Resource::arn:<AWS::Partition>:ec2:{region}:{account}:instance/*]": "Instances are created at runtime by the ASG; attach-volume is limited to the data volume.",
}


def _wildcards(env: EnvConfig) -> dict[str, str]:
    a, r, p = env.account, env.region, env.prefix
    return {
        f"IAM5[Resource::arn:aws:iam::{a}:role/cdk-*-{k}-role-{a}-*]": f"CI may assume only the CDK bootstrap {k} role."
        for k in ("lookup", "deploy", "file-publishing", "image-publishing")
    } | {
        f"IAM5[Resource::arn:aws:ecr:{r}:{a}:repository/{p}-*]": "Push images to this environment's repositories only.",
        f"IAM5[Resource::arn:aws:iam::{a}:role/{p}-*]": "PassRole limited to ECS tasks of this environment.",
        f"IAM5[Resource::arn:aws:s3:::{p}-frontend-{a}/*]": "Sync objects into the frontend bucket only.",
    }


def acknowledge_known(
    scope: Construct,
    env: EnvConfig,
    asg_logical_id: str,
    backup_bucket_logical_id: str | None = None,
) -> None:
    rules = {k: v for k, v in _STATIC.items()}
    rules.update(_MANAGED)
    rules.update(
        {k.format(region=env.region, account=env.account): v for k, v in _WILDCARD_STATIC.items()}
    )
    rules.update(_wildcards(env))
    rules[
        f"IAM5[Resource::arn:<AWS::Partition>:autoscaling:{env.region}:{env.account}:autoScalingGroup:*:autoScalingGroupName/<{asg_logical_id}>]"
    ] = "CDK-managed ECS drain hook may complete lifecycle actions for this group."
    if backup_bucket_logical_id:
        rules[f"IAM5[Resource::<{backup_bucket_logical_id}.Arn>/*]"] = (
            "pgBackRest reads and writes backup objects; limited to the backup bucket."
        )
    cdk.Validations.of(scope).acknowledge(
        *(cdk.Acknowledgment(id=_P + k, reason=v) for k, v in rules.items())
    )


def enable_checks(app: cdk.App) -> None:
    cdk.Validations.of(app).add_plugins(AwsSolutionsChecks(app))
