"""PostgreSQL: a container on the host (stage 0), or RDS / Aurora (stage 1 / 4).

Whatever the mode, the API receives the same ``DB_*`` variables, so switching modes never
changes application code. See docs/aws/05-sql.md.
"""

from __future__ import annotations

import json

import aws_cdk as cdk
import aws_cdk.aws_cloudwatch as cloudwatch
import aws_cdk.aws_dlm as dlm
import aws_cdk.aws_ec2 as ec2
import aws_cdk.aws_ecr as ecr
import aws_cdk.aws_ecs as ecs
import aws_cdk.aws_iam as iam
import aws_cdk.aws_rds as rds
import aws_cdk.aws_s3 as s3
import aws_cdk.aws_secretsmanager as secretsmanager
from constructs import Construct

from .compute import AppService, Host
from .config import EnvConfig

PG_PORT = 5432


class Postgres(Construct):
    # Environment and secrets the API container needs to reach the database.
    env: dict[str, str]
    secrets: dict[str, ecs.Secret]

    def __init__(
        self,
        scope: Construct,
        id: str,
        *,
        config: EnvConfig,
        host: Host,
        vpc: ec2.IVpc,
        repository: ecr.IRepository,
    ) -> None:
        super().__init__(scope, id)
        db = config.database
        self.services: list[AppService] = []
        self.backup_bucket: s3.Bucket | None = None
        self.managed_secret: secretsmanager.ISecret

        if db.mode == "host" or db.keep_host_container:
            host_secret = self._host_container(config, host, repository)
        if db.mode == "host":
            self.managed_secret = host_secret
            self.env = {"DB_HOST": "127.0.0.1", "DB_PORT": str(PG_PORT), "DB_NAME": db.name}
            self.secrets = {
                "DB_USER": ecs.Secret.from_secrets_manager(host_secret, "username"),
                "DB_PASSWORD": ecs.Secret.from_secrets_manager(host_secret, "password"),
            }
        else:
            self._managed(config, host, vpc)

    # -- stage 0 -----------------------------------------------------------------------------

    def _host_container(
        self, config: EnvConfig, host: Host, repository: ecr.IRepository
    ) -> secretsmanager.ISecret:
        db, region = config.database, cdk.Stack.of(self).region

        secret = secretsmanager.Secret(
            self,
            "HostSecret",
            description=f"{config.prefix} Postgres credentials (host container)",
            generate_secret_string=secretsmanager.SecretStringGenerator(
                secret_string_template=json.dumps({"username": db.username}),
                generate_string_key="password",
                exclude_punctuation=True,
                password_length=32,
            ),
            removal_policy=cdk.RemovalPolicy.RETAIN,
        )

        bucket = self.backup_bucket = s3.Bucket(
            self,
            "Backups",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            versioned=True,
            removal_policy=cdk.RemovalPolicy.RETAIN,
            lifecycle_rules=[
                s3.LifecycleRule(
                    noncurrent_version_expiration=cdk.Duration.days(14),
                    abort_incomplete_multipart_upload_after=cdk.Duration.days(7),
                )
            ],
        )
        # pgBackRest authenticates with the instance role (repo1-s3-key-type=auto).
        host.role.add_to_policy(
            iam.PolicyStatement(
                actions=["s3:ListBucket", "s3:GetBucketLocation"], resources=[bucket.bucket_arn]
            )
        )
        host.role.add_to_policy(
            iam.PolicyStatement(
                actions=["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
                resources=[bucket.arn_for_objects("*")],
            )
        )

        backrest = {
            "PGBACKREST_STANZA": "main",
            "PGBACKREST_PG1_PATH": "/var/lib/postgresql/data",
            "PGBACKREST_PG1_SOCKET_PATH": "/var/run/postgresql",
            "PGBACKREST_REPO1_TYPE": "s3",
            "PGBACKREST_REPO1_PATH": "/pgbackrest",
            "PGBACKREST_REPO1_S3_BUCKET": bucket.bucket_name,
            "PGBACKREST_REPO1_S3_ENDPOINT": f"s3.{region}.amazonaws.com",
            "PGBACKREST_REPO1_S3_REGION": region,
            "PGBACKREST_REPO1_S3_KEY_TYPE": "auto",
            "PGBACKREST_REPO1_RETENTION_FULL": str(db.full_backup_retention),
            "PGBACKREST_START_FAST": "y",
            "PGBACKREST_PROCESS_MAX": "1",
        }
        image = ecs.ContainerImage.from_ecr_repository(repository, config.postgres_image_tag)
        volumes = [
            ("/data/postgres", "/var/lib/postgresql/data"),
            ("/data/run", "/var/run/postgresql"),
        ]

        pg = AppService(
            self,
            "Postgres",
            host=host,
            name=f"{config.prefix}-postgres",
            image=image,
            port=None,
            cpu=0,
            memory_mib=db.memory_mib,
            environment={"POSTGRES_DB": db.name, **backrest},
            secrets={
                "POSTGRES_USER": ecs.Secret.from_secrets_manager(secret, "username"),
                "POSTGRES_PASSWORD": ecs.Secret.from_secrets_manager(secret, "password"),
            },
            command=[
                "postgres",
                "-c",
                "listen_addresses=127.0.0.1",  # never reachable from outside the host
                "-c",
                "unix_socket_directories=/var/run/postgresql",
                "-c",
                "max_connections=50",
                "-c",
                "shared_buffers=192MB",
                "-c",
                "wal_level=replica",
                "-c",
                "archive_mode=on",
                "-c",
                "archive_command=pgbackrest archive-push %p",
            ],
            health_check=["pg_isready -h /var/run/postgresql -q"],
            volumes=volumes,
        )

        backup = pg.task_definition.add_container(
            "backup",
            image=image,
            memory_limit_mib=128,
            user="postgres",
            command=["tsp-pg-backup.sh"],
            essential=False,
            environment={
                **backrest,
                "TSP_METRIC_NAMESPACE": "TSP/Backup",
                "TSP_METRIC_DIMENSION": config.name,
                "AWS_DEFAULT_REGION": region,
            },
            logging=ecs.LogDrivers.aws_logs(stream_prefix="backup"),
        )
        for i, (_, container_path) in enumerate(volumes):
            backup.add_mount_points(
                ecs.MountPoint(
                    container_path=container_path, source_volume=f"v{i}", read_only=False
                )
            )
        self.services.append(pg)

        # Daily EBS snapshots of the data volume: a second, independent recovery path.
        dlm_role = iam.Role(
            self,
            "DlmRole",
            assumed_by=iam.ServicePrincipal("dlm.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSDataLifecycleManagerServiceRole"
                )
            ],
        )
        dlm.CfnLifecyclePolicy(
            self,
            "Snapshots",
            description=f"{config.prefix} daily data volume snapshots keep 7",
            state="ENABLED",
            execution_role_arn=dlm_role.role_arn,
            policy_details=dlm.CfnLifecyclePolicy.PolicyDetailsProperty(
                resource_types=["VOLUME"],
                target_tags=[cdk.CfnTag(key="tsp:snapshot", value="daily")],
                schedules=[
                    dlm.CfnLifecyclePolicy.ScheduleProperty(
                        name="daily",
                        create_rule=dlm.CfnLifecyclePolicy.CreateRuleProperty(
                            interval=24, interval_unit="HOURS", times=["03:30"]
                        ),
                        retain_rule=dlm.CfnLifecyclePolicy.RetainRuleProperty(count=7),
                        copy_tags=True,
                    )
                ],
            ),
        )
        return secret

    # -- stage 1 / 4 -------------------------------------------------------------------------

    def _managed(self, config: EnvConfig, host: Host, vpc: ec2.IVpc) -> None:
        db = config.database
        sg = ec2.SecurityGroup(
            self, "DbSg", vpc=vpc, description="Postgres: host only", allow_all_outbound=False
        )
        sg.add_ingress_rule(host.security_group, ec2.Port.tcp(PG_PORT), "from the app host")
        isolated = ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_ISOLATED)
        engine = rds.DatabaseInstanceEngine.postgres(version=rds.PostgresEngineVersion.VER_16)
        creds = rds.Credentials.from_generated_secret(db.username)

        if db.mode == "rds":
            instance = rds.DatabaseInstance(
                self,
                "Rds",
                engine=engine,
                instance_type=ec2.InstanceType(db.rds_instance_class.removeprefix("db.")),
                vpc=vpc,
                vpc_subnets=isolated,
                security_groups=[sg],
                credentials=creds,
                database_name=db.name,
                allocated_storage=db.rds_storage_gb,
                storage_type=rds.StorageType.GP3,
                storage_encrypted=True,
                backup_retention=cdk.Duration.days(7),
                deletion_protection=True,
                removal_policy=cdk.RemovalPolicy.SNAPSHOT,
                multi_az=False,
                publicly_accessible=False,
                enable_performance_insights=False,
                cloudwatch_logs_exports=["postgresql"],
                auto_minor_version_upgrade=True,
            )
            secret = instance.secret
            self.endpoint = instance.db_instance_endpoint_address
        else:
            cluster = rds.DatabaseCluster(
                self,
                "Aurora",
                engine=rds.DatabaseClusterEngine.aurora_postgres(
                    version=rds.AuroraPostgresEngineVersion.VER_16_4
                ),
                writer=rds.ClusterInstance.serverless_v2("writer"),
                serverless_v2_min_capacity=0.5,
                serverless_v2_max_capacity=4,
                vpc=vpc,
                vpc_subnets=isolated,
                security_groups=[sg],
                credentials=creds,
                default_database_name=db.name,
                storage_encrypted=True,
                backup=rds.BackupProps(retention=cdk.Duration.days(7)),
                deletion_protection=True,
                removal_policy=cdk.RemovalPolicy.SNAPSHOT,
                cloudwatch_logs_exports=["postgresql"],
            )
            secret = cluster.secret
            self.endpoint = cluster.cluster_endpoint.hostname
        assert secret is not None
        self.managed_secret = secret
        self.env = {"DB_NAME": db.name, "DB_PORT": str(PG_PORT)}
        self.secrets = {
            "DB_HOST": ecs.Secret.from_secrets_manager(secret, "host"),
            "DB_USER": ecs.Secret.from_secrets_manager(secret, "username"),
            "DB_PASSWORD": ecs.Secret.from_secrets_manager(secret, "password"),
        }

    def backup_age_alarm(
        self, config: EnvConfig, topic: cdk.aws_sns.ITopic
    ) -> cloudwatch.Alarm | None:
        """Alarm when the newest backup is over 26 h old, or no backup metric arrives at all."""
        if self.backup_bucket is None:
            return None
        alarm = cloudwatch.Alarm(
            self,
            "BackupStale",
            metric=cloudwatch.Metric(
                namespace="TSP/Backup",
                metric_name="HoursSinceLastBackup",
                dimensions_map={"Environment": config.name},
                statistic="Maximum",
                period=cdk.Duration.hours(1),
            ),
            threshold=26,
            evaluation_periods=2,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
            treat_missing_data=cloudwatch.TreatMissingData.BREACHING,
            alarm_description="Postgres backups stopped or are failing",
        )
        alarm.add_alarm_action(cdk.aws_cloudwatch_actions.SnsAction(topic))
        return alarm
