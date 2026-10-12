import pytest
from aws_cdk.assertions import Match, Template
from tsp.infra import AppStage, DomainConfig, EnvConfig, GithubConfig, new_app


def make_config(**overrides) -> EnvConfig:
    return EnvConfig(
        name="prod",
        account="123456789012",
        domain=DomainConfig(app="app.example.com", auth="auth.example.com"),
        github=GithubConfig(owner="acme", repo="startup"),
        alarm_email="ops@example.com",
        origin_verify_secret="x" * 32,
        **overrides,
    )


@pytest.fixture(scope="module")
def stage() -> AppStage:
    return AppStage(new_app(), "t", config=make_config())


@pytest.fixture(scope="module")
def backend(stage) -> Template:
    return Template.from_stack(stage.backend)


def test_no_nat_gateway(backend):
    backend.resource_count_is("AWS::EC2::NatGateway", 0)


def test_gateway_endpoints_for_s3_and_dynamodb(backend):
    backend.resource_count_is("AWS::EC2::VPCEndpoint", 2)


def test_host_ingress_only_from_prefix_list(backend):
    sgs = backend.find_resources("AWS::EC2::SecurityGroup")
    ingress = [i for sg in sgs.values() for i in sg["Properties"].get("SecurityGroupIngress", [])]
    assert not ingress, "ingress must be separate prefix-list rules, never inline CIDRs"
    rules = backend.find_resources("AWS::EC2::SecurityGroupIngress")
    assert len(rules) == 1
    props = next(iter(rules.values()))["Properties"]
    assert "SourcePrefixListId" in props and props["FromPort"] == 8000
    assert "CidrIp" not in props


def test_data_volume_is_retained_and_encrypted(backend):
    backend.has_resource(
        "AWS::EC2::Volume", {"DeletionPolicy": "Retain", "Properties": {"Encrypted": True}}
    )


def test_instance_requires_imdsv2_and_one_instance(backend):
    backend.has_resource_properties(
        "AWS::EC2::LaunchTemplate",
        {"LaunchTemplateData": {"MetadataOptions": {"HttpTokens": "required"}}},
    )
    backend.has_resource_properties(
        "AWS::AutoScaling::AutoScalingGroup", {"MinSize": "1", "MaxSize": "1"}
    )


def test_user_data_claims_ip_and_mounts_data(backend):
    lts = backend.find_resources("AWS::EC2::LaunchTemplate")
    blob = str(next(iter(lts.values())))
    for needle in ("associate-address", "attach-volume", "mkfs", "/data/postgres"):
        assert needle in blob


def test_api_service_stops_before_starting_and_rolls_back(backend):
    backend.has_resource_properties(
        "AWS::ECS::Service",
        {
            "DeploymentConfiguration": {
                "MinimumHealthyPercent": 0,
                "MaximumPercent": 100,
                "DeploymentCircuitBreaker": {"Enable": True, "Rollback": True},
            }
        },
    )


def test_task_uses_host_networking(backend):
    backend.has_resource_properties("AWS::ECS::TaskDefinition", {"NetworkMode": "host"})


def test_ecr_is_immutable_scanned_and_retained(stage):
    Template.from_stack(stage.registry).has_resource(
        "AWS::ECR::Repository",
        {
            "DeletionPolicy": "Retain",
            "Properties": {
                "ImageTagMutability": "IMMUTABLE",
                "ImageScanningConfiguration": {"ScanOnPush": True},
            },
        },
    )


def test_edge_serves_api_path_and_spa(stage):
    edge = Template.from_stack(stage.edge)
    edge.has_resource_properties(
        "AWS::CloudFront::Distribution",
        {
            "DistributionConfig": {
                "Aliases": ["app.example.com"],
                "CacheBehaviors": [
                    Match.object_like(
                        {"PathPattern": "/api/*", "ViewerProtocolPolicy": "https-only"}
                    )
                ],
            }
        },
    )
    edge.has_resource("AWS::S3::Bucket", {"DeletionPolicy": "Retain"})


def test_edge_sends_origin_verify_header(stage):
    edge = Template.from_stack(stage.edge)
    assert "X-Origin-Verify" in str(edge.to_json())


def test_stacks_are_termination_protected(stage):
    assert stage.backend.termination_protection and stage.edge.termination_protection


def test_deploy_role_is_scoped_to_environment(stage):
    oidc = Template.from_stack(stage.oidc)
    blob = str(oidc.to_json())
    assert "repo:acme/startup:environment:prod" in blob
    assert "repo:acme/startup:pull_request" in blob


def test_bad_secret_is_rejected():
    with pytest.raises(ValueError):
        EnvConfig(**{**make_config().model_dump(), "origin_verify_secret": "short"})
