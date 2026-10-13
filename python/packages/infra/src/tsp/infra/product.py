"""Product features: customer uploads bucket and AI gateway credentials."""

from __future__ import annotations

import aws_cdk as cdk
import aws_cdk.aws_ec2 as ec2
import aws_cdk.aws_ecr as ecr
import aws_cdk.aws_ecs as ecs
import aws_cdk.aws_iam as iam
import aws_cdk.aws_s3 as s3
import aws_cdk.aws_s3vectors as s3vectors
import aws_cdk.aws_secretsmanager as secretsmanager
from constructs import Construct

from .compute import Host
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
            "AI_DEFAULT_RPM": str(a.default_rpm),
        }
        self.secrets: dict[str, ecs.Secret] = {}
        if a.provider_keys:
            # Created by hand so provider keys never pass through CloudFormation.
            sec = secretsmanager.Secret.from_secret_name_v2(
                self, "Keys", f"/{config.prefix}/ai/keys"
            )
            for name in a.provider_keys:
                self.secrets[name] = ecs.Secret.from_secrets_manager(sec, name)


class Vector(Construct):
    """Vector search settings for the API. ``pgvector`` needs no resources (the database image has the
    extension). ``s3vectors`` creates a vector bucket the API may read and write."""

    def __init__(self, scope: Construct, id: str, *, config: EnvConfig) -> None:
        super().__init__(scope, id)
        v = config.vector
        self.bucket: s3vectors.CfnVectorBucket | None = None
        self.environment = {
            "VECTOR_BACKEND": v.mode,
            "VECTOR_DIMS": str(v.dims),
            "VECTOR_EMBED_MODEL": v.embed_model,
        }
        if v.max_chunks_per_org:
            self.environment["VECTOR_MAX_CHUNKS_PER_ORG"] = str(v.max_chunks_per_org)
        if v.mode == "s3vectors":
            self.bucket = s3vectors.CfnVectorBucket(
                self,
                "Bucket",
                vector_bucket_name=f"{config.prefix}-vectors-{config.account}",
            )
            self.bucket.apply_removal_policy(cdk.RemovalPolicy.RETAIN)
            self.environment["VECTOR_S3_BUCKET"] = f"{config.prefix}-vectors-{config.account}"

    def grant(self, role: iam.IRole) -> None:
        if self.bucket is None:
            return
        arn = self.bucket.attr_vector_bucket_arn
        role.add_to_principal_policy(
            iam.PolicyStatement(
                actions=[
                    "s3vectors:CreateIndex",
                    "s3vectors:DeleteIndex",
                    "s3vectors:GetIndex",
                    "s3vectors:ListIndexes",
                    "s3vectors:PutVectors",
                    "s3vectors:GetVectors",
                    "s3vectors:ListVectors",
                    "s3vectors:QueryVectors",
                    "s3vectors:DeleteVectors",
                ],
                resources=[arn, f"{arn}/index/*"],
            )
        )


class Crawl(Construct):
    """Website ingestion settings for the API and, with ``render: fargate``, the browser render worker.

    The worker is a Fargate Spot task definition, not a service: it starts when there are pages to
    render and exits after ``idle_exit_minutes`` of nothing. It sits in a public subnet with a
    security group that has no inbound rules, an IAM role with no permissions, and no database or
    secret access except the render token. It reaches the API over HTTPS through the edge.
    Start one with the ``RunTaskCommand`` stack output."""

    def __init__(
        self,
        scope: Construct,
        id: str,
        *,
        config: EnvConfig,
        host: Host,
        vpc: ec2.IVpc,
        repository: ecr.IRepository | None,
    ) -> None:
        super().__init__(scope, id)
        c = config.crawl
        self.environment: dict[str, str] = {}
        self.secrets: dict[str, ecs.Secret] = {}
        self.task_definition: ecs.FargateTaskDefinition | None = None
        self._cluster: ecs.ICluster | None = None
        if c.render != "fargate" or repository is None:
            return
        # Created by hand so the token never passes through CloudFormation (at least 24 random characters).
        token = secretsmanager.Secret.from_secret_name_v2(
            self, "RenderToken", f"/{config.prefix}/crawl/render-token"
        )
        self.environment["CRAWL_RENDER"] = "fargate"
        self.secrets["CRAWL_RENDER_TOKEN"] = ecs.Secret.from_secrets_manager(token)

        host.cluster.enable_fargate_capacity_providers()
        self.task_definition = ecs.FargateTaskDefinition(
            self,
            "RenderTask",
            family=f"{config.prefix}-crawl-render",
            cpu=1024,
            memory_limit_mib=2048,
            runtime_platform=ecs.RuntimePlatform(
                cpu_architecture=ecs.CpuArchitecture.ARM64,
                operating_system_family=ecs.OperatingSystemFamily.LINUX,
            ),
        )
        self.task_definition.add_container(
            "render",
            image=ecs.ContainerImage.from_ecr_repository(repository, config.image_tag),
            environment={
                "CRAWL_API_URL": f"https://{config.domain.app}/api",
                "CRAWL_IDLE_EXIT_MINUTES": str(c.idle_exit_minutes),
            },
            secrets={"CRAWL_RENDER_TOKEN": self.secrets["CRAWL_RENDER_TOKEN"]},
            logging=ecs.LogDrivers.aws_logs(
                stream_prefix=f"{config.prefix}-crawl-render", log_retention=host.log_retention
            ),
        )
        sg = ec2.SecurityGroup(
            self,
            "RenderSg",
            vpc=vpc,
            description="Render worker: no inbound, outbound HTTPS and DNS",
            allow_all_outbound=False,
        )
        sg.add_egress_rule(ec2.Peer.any_ipv4(), ec2.Port.tcp(443), "HTTPS to the web and the API")
        sg.add_egress_rule(ec2.Peer.any_ipv4(), ec2.Port.tcp(80), "HTTP to the web")
        sg.add_egress_rule(ec2.Peer.any_ipv4(), ec2.Port.udp(53), "DNS")
        sg.add_egress_rule(ec2.Peer.any_ipv4(), ec2.Port.tcp(53), "DNS over TCP")
        subnets = ",".join(s.subnet_id for s in vpc.public_subnets)
        # The API starts render tasks itself (tsp.crawl.render_scaler) when pages are waiting.
        self._cluster = host.cluster
        self.environment.update(
            CRAWL_RENDER_CLUSTER=host.cluster.cluster_name,
            CRAWL_RENDER_TASK_DEFINITION=self.task_definition.family,
            CRAWL_RENDER_SUBNETS=subnets,
            CRAWL_RENDER_SECURITY_GROUPS=sg.security_group_id,
            CRAWL_RENDER_MAX_TASKS=str(c.max_render_tasks),
        )
        cdk.CfnOutput(
            self,
            "RunTaskCommand",
            value=(
                f"aws ecs run-task --cluster {host.cluster.cluster_name} "
                f"--task-definition {self.task_definition.family} "
                "--capacity-provider-strategy capacityProvider=FARGATE_SPOT,weight=1 "
                f'--network-configuration "awsvpcConfiguration={{subnets=[{subnets}],'
                f'securityGroups=[{sg.security_group_id}],assignPublicIp=ENABLED}}"'
            ),
        )

    def grant(self, role: iam.IRole) -> None:
        """Let the API start and list render tasks, and nothing else: ``ecs:RunTask`` on this task
        definition in this cluster, ``ecs:ListTasks`` in this cluster, ``iam:PassRole`` on the task's own
        two roles (only to ECS tasks)."""
        if self.task_definition is None or self._cluster is None:
            return
        td = self.task_definition
        in_cluster = {"ArnEquals": {"ecs:cluster": self._cluster.cluster_arn}}
        role.add_to_principal_policy(
            iam.PolicyStatement(
                actions=["ecs:RunTask"],
                resources=[
                    cdk.Stack.of(self).format_arn(
                        service="ecs",
                        resource="task-definition",
                        resource_name=f"{td.family}:*",  # RunTask by family uses the newest revision
                    )
                ],
                conditions=in_cluster,
            )
        )
        role.add_to_principal_policy(
            iam.PolicyStatement(
                actions=["ecs:ListTasks"], resources=["*"], conditions=in_cluster
            )  # list actions take no resource ARN; the condition limits it to this cluster
        )
        role.add_to_principal_policy(
            iam.PolicyStatement(
                actions=["cloudwatch:PutMetricData"],
                resources=[
                    "*"
                ],  # PutMetricData takes no resource ARN; the condition limits the namespace
                conditions={"StringEquals": {"cloudwatch:namespace": "TSP/Crawl"}},
            )
        )
        role.add_to_principal_policy(
            iam.PolicyStatement(
                actions=["iam:PassRole"],
                resources=[td.task_role.role_arn, td.obtain_execution_role().role_arn],
                conditions={"StringEquals": {"iam:PassedToService": "ecs-tasks.amazonaws.com"}},
            )
        )
