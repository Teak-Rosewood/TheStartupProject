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
    assert {"DB_USER", "DB_PASSWORD"} <= {s["Name"] for s in api["Secrets"]}
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
    clients = {
        k: v
        for k, v in t.find_resources("AWS::Cognito::UserPoolClient").items()
        if "staff" not in v["Properties"]["ClientName"]
    }
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


# -- compliance support ------------------------------------------------------------------------


def test_exports_bucket_is_private_short_lived_and_retained():
    t = backend()
    buckets = [
        r
        for r in t.find_resources("AWS::S3::Bucket").values()
        if any(
            rule.get("ExpirationInDays") == 7
            for rule in r["Properties"].get("LifecycleConfiguration", {}).get("Rules", [])
        )
    ]
    assert len(buckets) == 1 and buckets[0]["DeletionPolicy"] == "Retain"
    assert buckets[0]["Properties"]["PublicAccessBlockConfiguration"]["BlockPublicAcls"] is True


def test_api_role_can_manage_only_this_pools_users_and_only_export_objects():
    blob = json.dumps(backend().find_resources("AWS::IAM::Policy"))
    for action in ("AdminDeleteUser", "AdminDisableUser", "AdminUserGlobalSignOut", "ListUsers"):
        assert f"cognito-idp:{action}" in blob
    assert "cognito-idp:*" not in blob and "exports/*" in blob


def test_api_receives_compliance_settings():
    blob = json.dumps(backend().to_json())
    assert "EXPORT_BUCKET" in blob and "COGNITO_USER_POOL_ID" in blob


def test_log_retention_is_configurable_and_validated():
    from tsp.infra import EnvConfig

    t = Template.from_stack(stage(log_retention_days=180).backend)
    assert {
        g["Properties"].get("RetentionInDays")
        for g in t.find_resources("AWS::Logs::LogGroup").values()
    } == {180}
    with pytest.raises(ValueError, match="log_retention_days"):
        EnvConfig(**{**make_config().model_dump(), "log_retention_days": 15})


# -- staff console -----------------------------------------------------------------------------


def staff_pool(t: Template) -> dict:
    pools = [
        p["Properties"]
        for p in t.find_resources("AWS::Cognito::UserPool").values()
        if p["Properties"]["UserPoolName"].endswith("-staff")
    ]
    assert len(pools) == 1
    return pools[0]


def test_staff_pool_is_closed_mfa_required_and_separate_from_customers():
    t = backend()
    pool = staff_pool(t)
    assert pool["MfaConfiguration"] == "ON" and pool["EnabledMfas"] == ["SOFTWARE_TOKEN_MFA"]
    assert pool["AdminCreateUserConfig"]["AllowAdminCreateUserOnly"] is True
    assert (
        pool["DeletionProtection"] == "ACTIVE"
        and pool["Policies"]["PasswordPolicy"]["MinimumLength"] >= 14
    )
    customers = [
        p["Properties"]
        for p in t.find_resources("AWS::Cognito::UserPool").values()
        if p["Properties"]["UserPoolName"].endswith("-users")
    ]
    assert (
        len(customers) == 1
        and customers[0]["AdminCreateUserConfig"]["AllowAdminCreateUserOnly"] is False
    )


def test_staff_groups_and_confidential_client():
    t = backend()
    groups = {
        g["Properties"]["GroupName"]
        for g in t.find_resources("AWS::Cognito::UserPoolGroup").values()
    }
    assert groups == {"support", "finance", "superadmin"}
    client = next(
        c["Properties"]
        for c in t.find_resources("AWS::Cognito::UserPoolClient").values()
        if c["Properties"]["ClientName"].endswith("staff-console")
    )
    assert client["GenerateSecret"] is True and client["AllowedOAuthFlows"] == ["code"]
    assert client["CallbackURLs"] == ["https://app.example.com/api/admin/oidc/callback"]
    assert client["RefreshTokenValidity"] == 480  # minutes: an 8 hour working day


def test_console_secret_is_injected_not_written_into_the_task():
    t = backend()
    blob = json.dumps(t.to_json())
    api = next(
        c
        for r in t.find_resources("AWS::ECS::TaskDefinition").values()
        for c in r["Properties"]["ContainerDefinitions"]
        if any(e["Name"] == "ADMIN_ISSUER" for e in c.get("Environment", []))
    )
    assert "ADMIN_CLIENT_SECRET" in {s["Name"] for s in api["Secrets"]}
    assert "ADMIN_CLIENT_SECRET" not in {e["Name"] for e in api["Environment"]}
    assert "ADMIN_EXTERNAL_URL" in blob


def test_console_can_be_switched_off():
    from tsp.infra import AdminConfig

    t = backend(admin=AdminConfig(enabled=False))
    assert "ADMIN_ISSUER" not in json.dumps(t.to_json())
    assert not [
        p
        for p in t.find_resources("AWS::Cognito::UserPool").values()
        if p["Properties"]["UserPoolName"].endswith("-staff")
    ]


def test_console_allowed_networks_reach_the_api():
    from tsp.infra import AdminConfig

    t = backend(admin=AdminConfig(allowed_cidrs=["203.0.113.0/24", "198.51.100.7/32"]))
    assert "203.0.113.0/24,198.51.100.7/32" in json.dumps(t.to_json())


# -- payments ----------------------------------------------------------------------------------


def _payments_cfg(**kw):
    from tsp.infra import PaymentsConfig, SellerConfig

    seller = SellerConfig(
        legal_name="Acme Pvt Ltd",
        gstin="29AAAPL1234C1ZF",
        address="Bengaluru",
        state_code="29",
        sac="998314",
    )
    return PaymentsConfig(razorpay=True, seller=seller, **kw)


def test_payments_off_by_default_means_no_billing_resources():
    blob = json.dumps(backend().to_json())
    assert "INVOICE_BUCKET" not in blob and "RAZORPAY" not in blob


def test_razorpay_secrets_are_injected_from_secrets_manager_not_the_template():
    t = backend(payments=_payments_cfg())
    api = next(
        c
        for r in t.find_resources("AWS::ECS::TaskDefinition").values()
        for c in r["Properties"]["ContainerDefinitions"]
        if any(e["Name"] == "INVOICE_BUCKET" for e in c.get("Environment", []))
    )
    assert {"RAZORPAY_KEY_ID", "RAZORPAY_KEY_SECRET", "RAZORPAY_WEBHOOK_SECRET"} <= {
        s["Name"] for s in api["Secrets"]
    }
    env = {e["Name"]: e["Value"] for e in api["Environment"]}
    assert env["SELLER_GSTIN"] == "29AAAPL1234C1ZF" and not any(
        "SECRET" in k for k in env if k.startswith("RAZORPAY")
    )
    assert "STRIPE_SECRET_KEY" not in {s["Name"] for s in api["Secrets"]}


def test_invoices_bucket_is_private_versioned_and_retained():
    t = backend(payments=_payments_cfg())
    invoices = [
        r
        for r in t.find_resources("AWS::S3::Bucket").values()
        if r["Properties"]
        .get("LifecycleConfiguration", {})
        .get("Rules", [{}])[0]
        .get("NoncurrentVersionExpiration", {})
        .get("NoncurrentDays")
        == 90
    ]
    assert len(invoices) == 1 and invoices[0]["DeletionPolicy"] == "Retain"
    assert invoices[0]["Properties"]["VersioningConfiguration"]["Status"] == "Enabled"


def test_stripe_adds_its_secrets_and_return_urls():
    from tsp.infra import PaymentsConfig

    blob = json.dumps(backend(payments=PaymentsConfig(stripe=True)).to_json())
    assert "STRIPE_SECRET_KEY" in blob and "https://app.example.com/billing/success" in blob


def test_payments_mode_passes_security_checks():
    app = new_app()
    AppStage(app, "t", config=make_config(payments=_payments_cfg(stripe=True)))
    enable_checks(app)
    app.synth()


# -- email -------------------------------------------------------------------------------------


def _mail_cfg(**kw):
    from tsp.infra import NotifyConfig

    return NotifyConfig(
        enabled=True, domain="example.com", from_address="no-reply@example.com", brand="Acme", **kw
    )


def test_mail_is_off_by_default():
    blob = json.dumps(backend().to_json())
    assert "SES_FROM" not in blob and "AWS::SES::EmailIdentity" not in blob


def test_mail_needs_a_matching_domain_and_sender():
    from tsp.infra import NotifyConfig

    with pytest.raises(ValueError, match="domain"):
        NotifyConfig(enabled=True, domain="example.com", from_address="no-reply@other.com")


def test_ses_has_dkim_two_config_sets_and_bounce_and_complaint_events():
    t = backend(notify=_mail_cfg())
    t.has_resource_properties(
        "AWS::SES::EmailIdentity",
        Match.object_like(
            {
                "EmailIdentity": "example.com",
                "MailFromAttributes": Match.object_like({"MailFromDomain": "mail.example.com"}),
            }
        ),
    )
    sets = {
        c["Properties"]["Name"] for c in t.find_resources("AWS::SES::ConfigurationSet").values()
    }
    assert sets == {"tsp-prod-transactional", "tsp-prod-marketing"}
    events = t.find_resources("AWS::SES::ConfigurationSetEventDestination")
    assert len(events) == 2
    for e in events.values():
        assert set(e["Properties"]["EventDestination"]["MatchingEventTypes"]) == {
            "bounce",
            "complaint",
        }
    outputs = t.to_json()["Outputs"]
    assert (
        sum("Dkim" in k for k in outputs) == 6
    )  # three CNAME names and values to add at the registrar


def test_events_arrive_through_an_encrypted_queue_with_a_dead_letter_queue():
    t = backend(notify=_mail_cfg())
    queues = t.find_resources("AWS::SQS::Queue")
    main = next(q for q in queues.values() if "RedrivePolicy" in q["Properties"])
    assert (
        main["Properties"]["RedrivePolicy"]["maxReceiveCount"] == 5
        and main["Properties"]["SqsManagedSseEnabled"] is True
    )
    assert len(queues) == 2


def test_api_may_send_only_from_this_identity_and_read_only_this_queue():
    t = backend(notify=_mail_cfg())
    blob = json.dumps(t.find_resources("AWS::IAM::Policy"))
    assert (
        "ses:SendEmail" in blob
        and "ses:*" not in blob
        and "sqs:ReceiveMessage" in blob
        and "sqs:*" not in blob
    )


def test_mail_settings_and_signing_secret_reach_the_api():
    t = backend(notify=_mail_cfg(footer="Acme Pvt Ltd"))
    api = next(
        c
        for r in t.find_resources("AWS::ECS::TaskDefinition").values()
        for c in r["Properties"]["ContainerDefinitions"]
        if any(e["Name"] == "SES_FROM" for e in c.get("Environment", []))
    )
    env = {e["Name"]: e.get("Value") for e in api["Environment"]}
    assert (
        env["BRAND_NAME"] == "Acme"
        and env["APP_URL"] == "https://app.example.com"
        and env["EMAIL_FOOTER"] == "Acme Pvt Ltd"
    )
    assert "NOTIFY_SECRET" in {s["Name"] for s in api["Secrets"]}
    assert "NOTIFY_SECRET" not in env


def test_mail_mode_passes_security_checks():
    app = new_app()
    AppStage(app, "t", config=make_config(notify=_mail_cfg()))
    enable_checks(app)
    app.synth()


# -- growth and the site's browser policy ------------------------------------------------------


def test_captcha_secret_is_injected_only_when_enabled():
    from tsp.infra import GrowthConfig

    off = backend()
    assert "CAPTCHA_SECRET" not in json.dumps(off.to_json())
    t = backend(growth=GrowthConfig(captcha="turnstile"))
    api = next(
        c
        for r in t.find_resources("AWS::ECS::TaskDefinition").values()
        for c in r["Properties"]["ContainerDefinitions"]
        if any(
            e["Name"] == "CAPTCHA_PROVIDER" and e.get("Value") == "turnstile"
            for e in c.get("Environment", [])
        )
    )
    assert "CAPTCHA_SECRET" in {s["Name"] for s in api["Secrets"]}


def test_site_has_a_strict_security_header_policy_but_the_api_path_does_not():
    edge = Template.from_stack(stage().edge)
    pol = next(iter(edge.find_resources("AWS::CloudFront::ResponseHeadersPolicy").values()))[
        "Properties"
    ]["ResponseHeadersPolicyConfig"]
    csp = pol["SecurityHeadersConfig"]["ContentSecurityPolicy"]["ContentSecurityPolicy"]
    assert (
        "frame-ancestors 'none'" in csp
        and "object-src 'none'" in csp
        and "script-src 'self'" in csp
        and "'unsafe-inline'" not in csp.split("style-src")[0]
    )
    assert (
        pol["SecurityHeadersConfig"]["StrictTransportSecurity"]["AccessControlMaxAgeSec"]
        == 31536000
    )
    assert pol["SecurityHeadersConfig"]["FrameOptions"]["FrameOption"] == "DENY"
    assert any(h["Header"] == "Permissions-Policy" for h in pol["CustomHeadersConfig"]["Items"])
    dist = next(iter(edge.find_resources("AWS::CloudFront::Distribution").values()))["Properties"][
        "DistributionConfig"
    ]
    assert "ResponseHeadersPolicyId" in dist["DefaultCacheBehavior"]
    assert (
        "ResponseHeadersPolicyId" not in dist["CacheBehaviors"][0]
    )  # /api/*: the console's pages need inline scripts


def test_csp_can_be_overridden():
    from tsp.infra import EdgeConfig

    edge = Template.from_stack(stage(edge=EdgeConfig(csp="default-src 'none'")).edge)
    assert "default-src 'none'" in json.dumps(edge.to_json())


def test_edge_function_serves_directory_pages_and_the_app_fallback():
    edge = Template.from_stack(stage().edge)
    fn = next(iter(edge.find_resources("AWS::CloudFront::Function").values()))["Properties"][
        "FunctionCode"
    ]
    assert "'/index.html'" in fn and "/app/index.html" in fn and "/api/" in fn
    assert "uri.indexOf('.') !== -1" in fn  # files with an extension are served as they are
