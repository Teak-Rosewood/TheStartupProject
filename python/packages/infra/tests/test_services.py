"""Database, cache, NoSQL, auth and alarms: stage 0 defaults and every scaling mode."""

import json

import pytest
from aws_cdk.assertions import Match, Template
from test_stacks import make_config
from tsp.infra import (
    AppStage,
    AuthConfig,
    CacheConfig,
    DatabaseConfig,
    enable_checks,
    new_app,
)


def stage(**overrides) -> AppStage:
    return AppStage(new_app(), "t", config=make_config(**overrides))


def backend(**overrides) -> Template:
    return Template.from_stack(stage(**overrides).backend)


def services(t: Template) -> dict[str, dict]:
    return {
        r["Properties"]["ServiceName"]: r["Properties"]
        for r in t.find_resources("AWS::ECS::Service").values()
    }


# -- stage 0 ---------------------------------------------------------------------------------


def test_default_runs_api_postgres_valkey_on_the_host():
    assert set(services(backend())) == {"tsp-prod-api", "tsp-prod-postgres", "tsp-prod-valkey"}


def test_postgres_never_overlaps_during_deploy():
    pg = services(backend())["tsp-prod-postgres"]["DeploymentConfiguration"]
    assert pg["MinimumHealthyPercent"] == 0 and pg["MaximumPercent"] == 100


def test_postgres_listens_on_loopback_only_and_archives_wal():
    t = backend()
    blob = json.dumps(t.to_json())
    assert "listen_addresses=127.0.0.1" in blob
    assert "archive_command=pgbackrest archive-push %p" in blob
    assert "/data/postgres" in blob and "/var/lib/postgresql/data" in blob


def test_backup_sidecar_shares_the_data_volumes():
    t = backend()
    pg = next(
        r
        for r in t.find_resources("AWS::ECS::TaskDefinition").values()
        if any(c["Name"] == "backup" for c in r["Properties"]["ContainerDefinitions"])
    )["Properties"]
    backup = next(c for c in pg["ContainerDefinitions"] if c["Name"] == "backup")
    assert backup["User"] == "postgres" and backup["Essential"] is False
    assert {m["ContainerPath"] for m in backup["MountPoints"]} == {
        "/var/lib/postgresql/data",
        "/var/run/postgresql",
    }


def test_backup_bucket_is_private_versioned_retained():
    t = backend()
    t.has_resource(
        "AWS::S3::Bucket",
        {
            "DeletionPolicy": "Retain",
            "Properties": {
                "VersioningConfiguration": {"Status": "Enabled"},
                "PublicAccessBlockConfiguration": Match.object_like({"BlockPublicAcls": True}),
            },
        },
    )


def test_daily_ebs_snapshots_target_the_data_volume():
    t = backend()
    t.has_resource_properties(
        "AWS::DLM::LifecyclePolicy",
        {
            "PolicyDetails": Match.object_like(
                {"TargetTags": [{"Key": "tsp:snapshot", "Value": "daily"}]}
            )
        },
    )
    t.has_resource_properties(
        "AWS::EC2::Volume", {"Tags": Match.array_with([{"Key": "tsp:snapshot", "Value": "daily"}])}
    )


def test_db_credentials_come_from_secrets_manager_not_plaintext():
    api = next(
        r["Properties"]["ContainerDefinitions"][0]
        for r in backend().find_resources("AWS::ECS::TaskDefinition").values()
        if r["Properties"]["ContainerDefinitions"][0]["Name"] == "main"
        and any(
            e["Name"] == "DB_HOST"
            for e in r["Properties"]["ContainerDefinitions"][0].get("Environment", [])
        )
    )
    assert {s["Name"] for s in api["Secrets"]} == {"DB_USER", "DB_PASSWORD"}
    assert "DB_PASSWORD" not in {e["Name"] for e in api["Environment"]}


def test_instance_role_cannot_reach_other_buckets():
    blob = json.dumps(backend().find_resources("AWS::IAM::Policy"))
    assert "s3:*" not in blob


def test_dynamodb_is_on_demand_with_pitr_and_protection():
    t = backend()
    t.has_resource(
        "AWS::DynamoDB::Table",
        {
            "DeletionPolicy": "Retain",
            "Properties": {
                "BillingMode": "PAY_PER_REQUEST",
                "DeletionProtectionEnabled": True,
                "PointInTimeRecoverySpecification": {"PointInTimeRecoveryEnabled": True},
                "TimeToLiveSpecification": {"AttributeName": "expires_at", "Enabled": True},
            },
        },
    )


def test_user_pool_is_essentials_with_passkeys_and_pkce_clients():
    t = backend()
    pool = next(iter(t.find_resources("AWS::Cognito::UserPool").values()))["Properties"]
    assert pool["UserPoolTier"] == "ESSENTIALS" and pool["DeletionProtection"] == "ACTIVE"
    assert set(pool["Policies"]["SignInPolicy"]["AllowedFirstAuthFactors"]) == {
        "PASSWORD",
        "EMAIL_OTP",
        "WEB_AUTHN",
    }
    assert pool["Policies"]["PasswordPolicy"]["RequireSymbols"] is True
    clients = t.find_resources("AWS::Cognito::UserPoolClient")
    assert len(clients) == 2
    for c in clients.values():
        p = c["Properties"]
        assert p["GenerateSecret"] is False and p["AllowedOAuthFlows"] == ["code"]


def test_alarms_cover_host_disk_backup_and_budget():
    t = backend()
    names = {
        a["Properties"]["AlarmDescription"]
        for a in t.find_resources("AWS::CloudWatch::Alarm").values()
    }
    assert any("status check" in n for n in names)
    assert any("/data above 80%" in n for n in names)
    assert any("backups stopped" in n.lower() for n in names)
    t.resource_count_is("AWS::Budgets::Budget", 1)
    backup = next(
        a["Properties"]
        for a in t.find_resources("AWS::CloudWatch::Alarm").values()
        if "backups" in a["Properties"]["AlarmDescription"].lower()
    )
    assert backup["TreatMissingData"] == "breaching"  # a dead sidecar must alarm


def test_cloudfront_5xx_alarm_lives_in_the_edge_stack():
    Template.from_stack(stage().edge).has_resource_properties(
        "AWS::CloudWatch::Alarm", {"MetricName": "5xxErrorRate"}
    )


def test_registry_has_api_and_postgres_repos():
    Template.from_stack(stage().registry).resource_count_is("AWS::ECR::Repository", 2)


# -- scaling modes (stage 1+) ----------------------------------------------------------------


def test_rds_mode_drops_the_container_and_uses_managed_secret_fields():
    t = backend(database=DatabaseConfig(mode="rds"))
    assert "tsp-prod-postgres" not in services(t)
    t.has_resource(
        "AWS::RDS::DBInstance",
        {
            "DeletionPolicy": "Snapshot",
            "Properties": Match.object_like(
                {"DeletionProtection": True, "PubliclyAccessible": False, "StorageEncrypted": True}
            ),
        },
    )
    api_secrets = {
        s["Name"]
        for r in t.find_resources("AWS::ECS::TaskDefinition").values()
        for c in r["Properties"]["ContainerDefinitions"]
        for s in c.get("Secrets", [])
    }
    assert {"DB_HOST", "DB_USER", "DB_PASSWORD"} <= api_secrets


def test_rds_migration_window_keeps_both():
    t = backend(database=DatabaseConfig(mode="rds", keep_host_container=True))
    assert "tsp-prod-postgres" in services(t)
    t.resource_count_is("AWS::RDS::DBInstance", 1)


def test_rds_is_only_reachable_from_the_host():
    t = backend(database=DatabaseConfig(mode="rds"))
    t.has_resource_properties(
        "AWS::EC2::SecurityGroupIngress",
        Match.object_like(
            {"FromPort": 5432, "ToPort": 5432, "SourceSecurityGroupId": Match.any_value()}
        ),
    )


def test_aurora_mode_is_serverless_v2():
    t = backend(database=DatabaseConfig(mode="aurora"))
    t.has_resource_properties(
        "AWS::RDS::DBCluster",
        {
            "ServerlessV2ScalingConfiguration": {"MinCapacity": 0.5, "MaxCapacity": 4},
            "DeletionProtection": True,
        },
    )


def test_elasticache_mode_uses_tls_url_and_drops_valkey_container():
    t = backend(cache=CacheConfig(mode="elasticache"))
    assert "tsp-prod-valkey" not in services(t)
    t.resource_count_is("AWS::ElastiCache::ServerlessCache", 1)
    assert "rediss://" in json.dumps(t.to_json())


def test_cache_none_has_no_cache_anywhere():
    t = backend(cache=CacheConfig(mode="none"))
    assert "tsp-prod-valkey" not in services(t)
    t.resource_count_is("AWS::ElastiCache::ServerlessCache", 0)
    assert "CACHE_URL" not in json.dumps(t.to_json())


def test_social_providers_and_custom_domain_modes_synthesize():
    s = stage(
        auth=AuthConfig(
            google=True, apple=True, custom_domain=True, ses_from="no-reply@example.com"
        )
    )
    t = Template.from_stack(s.backend)
    t.resource_count_is("AWS::Cognito::UserPoolIdentityProvider", 2)
    t.has_resource_properties("AWS::Cognito::UserPoolDomain", {"Domain": "auth.example.com"})
    t.has_resource_properties(
        "AWS::Cognito::UserPool",
        {"EmailConfiguration": Match.object_like({"EmailSendingAccount": "DEVELOPER"})},
    )
    Template.from_stack(s.auth_cert).has_resource_properties(
        "AWS::CertificateManager::Certificate", {"DomainName": "auth.example.com"}
    )
    blob = json.dumps(t.to_json())
    assert "resolve:secretsmanager:/tsp-prod/auth/google" in blob


@pytest.mark.parametrize(
    "overrides",
    [
        {"database": DatabaseConfig(mode="rds"), "cache": CacheConfig(mode="elasticache")},
        {"database": DatabaseConfig(mode="aurora")},
        {"auth": AuthConfig(google=True, apple=True, custom_domain=True)},
    ],
    ids=["stage1-managed", "aurora", "social-custom-domain"],
)
def test_every_mode_passes_security_checks(overrides):
    app = new_app()
    AppStage(app, "t", config=make_config(**overrides))
    enable_checks(app)
    app.synth()
