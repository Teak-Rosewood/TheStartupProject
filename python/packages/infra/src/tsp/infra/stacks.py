"""Stacks of one environment. ``AppStage`` is the unit you add once per environment."""

from __future__ import annotations

from typing import cast

import aws_cdk as cdk
import aws_cdk.aws_certificatemanager as acm
import aws_cdk.aws_ecr as ecr
import aws_cdk.aws_ecs as ecs
import aws_cdk.aws_iam as iam
from constructs import Construct

from .auth import AdminPool, UserPool
from .billing import Billing
from .cache import Cache
from .compute import AppService, Host
from .config import EnvConfig
from .database import Postgres
from .edge import Site
from .exports import Exports
from .github_oidc import GithubOidc
from .growth import Growth
from .mail import Mail
from .nag import acknowledge_known
from .network import Network
from .nosql import Store
from .observability import Alarms, EdgeAlarms

# Feature flags the constructs rely on; a plain ``cdk.App()`` would silently use legacy behavior.
APP_CONTEXT = {
    "@aws-cdk/aws-autoscaling:generateLaunchTemplateInsteadOfLaunchConfig": True,
}


def new_app() -> cdk.App:
    return cdk.App(context=APP_CONTEXT)


class OidcStack(cdk.Stack):
    def __init__(self, scope: Construct, id: str, *, config: EnvConfig, **kw) -> None:
        super().__init__(scope, id, **kw)
        self.oidc = GithubOidc(self, "GithubOidc", env=config)


class RegistryStack(cdk.Stack):
    """Container registries. Deployed first so CI can push images before any service needs them."""

    def __init__(self, scope: Construct, id: str, *, config: EnvConfig, **kw) -> None:
        super().__init__(scope, id, **kw)

        def repo(name: str) -> ecr.Repository:
            return ecr.Repository(
                self,
                f"{name.title()}Repo",
                repository_name=f"{config.prefix}-{name}",
                image_scan_on_push=True,
                image_tag_mutability=ecr.TagMutability.IMMUTABLE,
                removal_policy=cdk.RemovalPolicy.RETAIN,
                lifecycle_rules=[
                    ecr.LifecycleRule(
                        max_image_count=20,
                        rule_priority=2,
                        tag_status=ecr.TagStatus.TAGGED,
                        tag_pattern_list=["*"],
                    ),
                    ecr.LifecycleRule(
                        max_image_age=cdk.Duration.days(7),
                        rule_priority=1,
                        tag_status=ecr.TagStatus.UNTAGGED,
                    ),
                ],
            )

        self.api = repo("api")
        self.postgres = repo("postgres")
        cdk.CfnOutput(self, "ApiRepositoryUri", value=self.api.repository_uri)
        cdk.CfnOutput(self, "PostgresRepositoryUri", value=self.postgres.repository_uri)


class AuthCertStack(cdk.Stack):
    """Certificate for the Cognito custom domain. Cognito requires it in us-east-1."""

    def __init__(self, scope: Construct, id: str, *, config: EnvConfig, **kw) -> None:
        super().__init__(scope, id, **kw)
        assert config.domain.auth
        self.certificate = acm.Certificate(
            self,
            "Cert",
            domain_name=config.domain.auth,
            validation=acm.CertificateValidation.from_dns(),
        )


class BackendStack(cdk.Stack):
    """Region-local request path: network, host, database, cache, NoSQL, auth, alarms."""

    def __init__(
        self,
        scope: Construct,
        id: str,
        *,
        config: EnvConfig,
        registry: RegistryStack,
        auth_certificate: acm.ICertificate | None = None,
        **kw,
    ) -> None:
        super().__init__(scope, id, **kw)
        cfg = config.compute
        self.network = Network(
            self, "Network", availability_zones=[f"{config.region}a", f"{config.region}b"]
        )
        self.host = Host(self, "Host", env=config, vpc=self.network.vpc)

        self.postgres = Postgres(
            self,
            "Postgres",
            config=config,
            host=self.host,
            vpc=self.network.vpc,
            repository=registry.postgres,
        )
        self.cache = Cache(self, "Cache", config=config, host=self.host, vpc=self.network.vpc)
        self.store = Store(self, "Store", config=config)
        self.auth = UserPool(self, "Auth", config=config, certificate=auth_certificate)
        self.exports = Exports(self, "Exports", config=config)
        self.billing = (
            Billing(self, "Billing", config=config)
            if (config.payments.razorpay or config.payments.stripe)
            else None
        )
        self.growth = Growth(self, "Growth", config=config)
        self.mail = Mail(self, "Mail", config=config) if config.notify.enabled else None
        self.admin_pool = AdminPool(self, "Staff", config=config) if config.admin.enabled else None

        environment = {
            "TSP_ENV": config.name,
            "TSP_ORIGIN_VERIFY": config.origin_verify_secret,
            "TSP_PORT": str(cfg.container_port),
            "AWS_REGION": config.region,
            "TSP_MIGRATE_ON_START": "1",  # schema first, under an advisory lock; failure rolls back
            "DYNAMODB_TABLE": self.store.table.table_name,
            "EXPORT_BUCKET": self.exports.bucket.bucket_name,
            "COGNITO_USER_POOL_ID": self.auth.pool.user_pool_id,
            "AUTH_ISSUER": self.auth.issuer,
            "AUTH_CLIENT_IDS": self.auth.client_ids,
            **self.postgres.env,
        }
        if self.cache.url:
            environment["CACHE_URL"] = self.cache.url
        secrets = dict(self.postgres.secrets)
        environment.update(self.growth.environment)
        secrets.update(self.growth.secrets)
        if self.mail:
            environment.update(self.mail.environment)
            secrets.update(self.mail.secrets)
        if self.billing:
            environment.update(self.billing.environment)
            secrets.update(self.billing.secrets)
        if self.admin_pool:
            environment.update(
                ADMIN_ISSUER=self.admin_pool.issuer,
                ADMIN_CLIENT_ID=self.admin_pool.client.user_pool_client_id,
                ADMIN_DOMAIN=self.admin_pool.domain_url,
                ADMIN_EXTERNAL_URL=self.admin_pool.external_url,
                ADMIN_ALLOWED_CIDRS=",".join(config.admin.allowed_cidrs),
            )
            secrets["ADMIN_CLIENT_SECRET"] = ecs.Secret.from_secrets_manager(
                self.admin_pool.client_secret
            )

        self.api = AppService(
            self,
            "Api",
            host=self.host,
            name=f"{config.prefix}-api",
            image=ecs.ContainerImage.from_ecr_repository(registry.api, config.image_tag),
            port=cfg.container_port,
            cpu=cfg.api_cpu,
            memory_mib=cfg.api_memory_mib,
            environment=environment,
            secrets=secrets,
            health_check=[
                f"python -c \"import urllib.request as u; u.urlopen('http://127.0.0.1:{cfg.container_port}/api/health', timeout=3)\""
            ],
        )
        self.store.table.grant_read_write_data(self.api.task_definition.task_role)
        self.exports.grant(self.api.task_definition.task_role)
        if self.billing:
            self.billing.grant(self.api.task_definition.task_role)
        if self.mail:
            self.mail.grant(self.api.task_definition.task_role)
        # Account deletion: end sessions, disable and delete users of this pool only.
        self.api.task_definition.task_role.add_to_principal_policy(
            iam.PolicyStatement(
                actions=[
                    "cognito-idp:ListUsers",
                    "cognito-idp:AdminUserGlobalSignOut",
                    "cognito-idp:AdminDisableUser",
                    "cognito-idp:AdminDeleteUser",
                ],
                resources=[self.auth.pool.user_pool_arn],
            )
        )

        self.alarms = Alarms(
            self,
            "Alarms",
            config=config,
            asg_name=self.host.asg.auto_scaling_group_name,
        )
        self.postgres.backup_age_alarm(config, self.alarms.topic)

        cdk.CfnOutput(self, "ClusterName", value=self.host.cluster.cluster_name)
        cdk.CfnOutput(self, "HostPublicDns", value=self.host.public_dns)
        cdk.CfnOutput(self, "ApiServiceName", value=f"{config.prefix}-api")
        cdk.CfnOutput(self, "UserPoolId", value=self.auth.pool.user_pool_id)
        cdk.CfnOutput(self, "UserPoolIssuer", value=self.auth.issuer)
        cdk.CfnOutput(self, "UserPoolClientIds", value=self.auth.client_ids)
        if self.admin_pool:
            cdk.CfnOutput(self, "StaffPoolId", value=self.admin_pool.pool.user_pool_id)
            cdk.CfnOutput(
                self, "StaffConsoleUrl", value=f"{self.admin_pool.external_url}/api/admin/"
            )
        cdk.CfnOutput(self, "DynamoTable", value=self.store.table.table_name)
        cdk.CfnOutput(self, "DataVolumeId", value=self.host.data_volume.volume_id)
        if self.postgres.backup_bucket:
            cdk.CfnOutput(self, "BackupBucket", value=self.postgres.backup_bucket.bucket_name)


class EdgeStack(cdk.Stack):
    """Global edge: certificate, frontend bucket, CloudFront. Must live in us-east-1."""

    def __init__(
        self, scope: Construct, id: str, *, config: EnvConfig, api_origin_domain: str, **kw
    ) -> None:
        super().__init__(scope, id, **kw)
        self.site = Site(self, "Site", env=config, api_origin_domain=api_origin_domain)
        EdgeAlarms(
            self, "Alarms", config=config, distribution_id=self.site.distribution.distribution_id
        )


class AppStage(cdk.Stage):
    """Everything for one environment. Instantiate once per environment/account."""

    def __init__(self, scope: Construct, id: str, *, config: EnvConfig) -> None:
        env_primary = cdk.Environment(account=config.account, region=config.region)
        env_edge = cdk.Environment(account=config.account, region=config.edge_region)
        super().__init__(scope, id, env=env_primary)

        self.oidc = OidcStack(self, f"{config.prefix}-oidc", config=config, env=env_primary)
        self.registry = RegistryStack(
            self, f"{config.prefix}-registry", config=config, env=env_primary
        )
        auth_cert = None
        if config.auth.custom_domain and config.domain.auth:
            self.auth_cert = AuthCertStack(
                self,
                f"{config.prefix}-auth-cert",
                config=config,
                env=env_edge,
                cross_region_references=True,
            )
            auth_cert = self.auth_cert.certificate
        self.backend = BackendStack(
            self,
            f"{config.prefix}-backend",
            config=config,
            registry=self.registry,
            auth_certificate=auth_cert,
            env=env_primary,
            cross_region_references=True,
            termination_protection=True,
        )
        self.edge = EdgeStack(
            self,
            f"{config.prefix}-edge",
            config=config,
            api_origin_domain=self.backend.host.public_dns,
            env=env_edge,
            cross_region_references=True,
            termination_protection=True,
        )
        self.edge.add_stack_dependency(self.backend)

        asg = cast(cdk.CfnElement, self.backend.host.asg.node.default_child)
        bucket_ids = [
            self.backend.get_logical_id(cast(cdk.CfnElement, b.node.default_child))
            for b in (
                self.backend.postgres.backup_bucket,
                self.backend.exports.bucket,
                self.backend.billing.invoices if self.backend.billing else None,
            )
            if b is not None
        ]
        acknowledge_known(self, config, self.backend.get_logical_id(asg), bucket_ids)
