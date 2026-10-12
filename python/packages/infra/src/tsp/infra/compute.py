"""The single ECS-on-EC2 host and the helper that runs a container on it."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence

import aws_cdk as cdk
import aws_cdk.aws_autoscaling as autoscaling
import aws_cdk.aws_ec2 as ec2
import aws_cdk.aws_ecs as ecs
import aws_cdk.aws_iam as iam
import aws_cdk.aws_logs as logs
import aws_cdk.custom_resources as cr
from constructs import Construct

from .config import EnvConfig

CLOUDFRONT_PREFIX_LIST = "com.amazonaws.global.cloudfront.origin-facing"

# disk_used_percent{path=/data} and mem_used_percent in namespace TSP/Host (see observability.py).
_CW_AGENT_CONFIG = json.dumps(
    {
        "agent": {"metrics_collection_interval": 300},
        "metrics": {
            "namespace": "TSP/Host",
            "aggregation_dimensions": [["path"], []],
            "metrics_collected": {
                "disk": {"measurement": ["used_percent"], "resources": ["/data"]},
                "mem": {"measurement": ["used_percent"]},
            },
        },
    },
    indent=2,
)


class Host(Construct):
    """One Graviton instance in one AZ with a retained data volume and a fixed public IP.

    On start the instance claims the Elastic IP, attaches and mounts the data volume at /data,
    and only then lets the ECS agent run tasks. See docs/aws/02-compute.md.
    """

    def __init__(self, scope: Construct, id: str, *, env: EnvConfig, vpc: ec2.IVpc) -> None:
        super().__init__(scope, id)
        cfg = env.compute
        az = vpc.availability_zones[cfg.az_index]
        subnets = ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC, availability_zones=[az])

        self.cluster = ecs.Cluster(self, "Cluster", vpc=vpc, cluster_name=f"{env.prefix}-cluster")

        self.elastic_ip = ec2.CfnEIP(self, "Ip", domain="vpc")
        self.data_volume = ec2.Volume(
            self,
            "Data",
            availability_zone=az,
            size=cdk.Size.gibibytes(cfg.data_volume_gb),
            volume_type=ec2.EbsDeviceVolumeType.GP3,
            encrypted=True,
            removal_policy=cdk.RemovalPolicy.RETAIN,
        )

        self.security_group = ec2.SecurityGroup(
            self, "Sg", vpc=vpc, description="API host: CloudFront origin-facing IPs only"
        )
        self._allow_cloudfront(cfg.container_port)

        self.role = role = iam.Role(
            self,
            "Role",
            assumed_by=iam.ServicePrincipal("ec2.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("AmazonSSMManagedInstanceCore")
            ],
        )
        role.add_to_policy(
            iam.PolicyStatement(
                actions=["ec2:AssociateAddress", "ec2:DescribeVolumes", "ec2:DescribeAddresses"],
                resources=["*"],  # these actions do not support resource-level permissions
            )
        )
        role.add_to_policy(
            iam.PolicyStatement(
                actions=["ec2:AttachVolume"],
                resources=[
                    cdk.Stack.of(self).format_arn(
                        service="ec2", resource="volume", resource_name=self.data_volume.volume_id
                    ),
                    cdk.Stack.of(self).format_arn(
                        service="ec2", resource="instance", resource_name="*"
                    ),
                ],
            )
        )

        role.add_to_policy(
            iam.PolicyStatement(
                actions=["cloudwatch:PutMetricData"],
                resources=["*"],  # PutMetricData has no resource-level permissions
                conditions={"StringEquals": {"cloudwatch:namespace": ["TSP/Host", "TSP/Backup"]}},
            )
        )
        cdk.Tags.of(self.data_volume).add("tsp:snapshot", "daily")  # picked up by the DLM policy

        self.asg = autoscaling.AutoScalingGroup(
            self,
            "Asg",
            vpc=vpc,
            vpc_subnets=subnets,
            instance_type=ec2.InstanceType(cfg.instance_type),
            machine_image=ecs.EcsOptimizedImage.amazon_linux2023(ecs.AmiHardwareType.ARM),
            min_capacity=1,
            max_capacity=1,
            security_group=self.security_group,
            role=role,
            require_imdsv2=True,
            associate_public_ip_address=True,
            block_devices=[
                autoscaling.BlockDevice(
                    device_name="/dev/xvda",
                    volume=autoscaling.BlockDeviceVolume.ebs(
                        cfg.root_volume_gb,
                        volume_type=autoscaling.EbsDeviceVolumeType.GP3,
                        encrypted=True,
                    ),
                )
            ],
        )
        self.capacity_provider = ecs.AsgCapacityProvider(
            self,
            "Provider",
            auto_scaling_group=self.asg,
            enable_managed_termination_protection=False,
        )
        self.cluster.add_asg_capacity_provider(self.capacity_provider)

        # Appended after the ECS agent config; the agent starts only after user data finishes.
        region = cdk.Stack.of(self).region
        vol_id = self.data_volume.volume_id
        self.asg.user_data.add_commands(
            "set -euo pipefail",
            "TOKEN=$(curl -s -X PUT http://169.254.169.254/latest/api/token "
            '-H "X-aws-ec2-metadata-token-ttl-seconds: 120")',
            'IID=$(curl -s -H "X-aws-ec2-metadata-token: $TOKEN" '
            "http://169.254.169.254/latest/meta-data/instance-id)",
            f"aws ec2 associate-address --region {region} --instance-id $IID "
            f"--allocation-id {self.elastic_ip.attr_allocation_id} --allow-reassociation",
            # a replaced instance may still hold the volume for a minute: wait for 'available'
            "for i in $(seq 1 90); do "
            f"S=$(aws ec2 describe-volumes --region {region} --volume-ids {vol_id} "
            "--query 'Volumes[0].State' --output text); "
            '[ "$S" = available ] && break; sleep 5; done',
            f"aws ec2 attach-volume --region {region} --volume-id {vol_id} "
            "--instance-id $IID --device /dev/sdf",
            f"VOL={vol_id}",
            "DEV=/dev/disk/by-id/nvme-Amazon_Elastic_Block_Store_${VOL//-/}",
            "for i in $(seq 1 60); do [ -e $DEV ] && break; sleep 2; done",
            "blkid $DEV || mkfs -t xfs $DEV",
            "mkdir -p /data",
            'echo "$DEV /data xfs defaults,nofail 0 2" >> /etc/fstab',
            "mount /data",
            # uid/gid 999 = postgres in the official image; run/ holds the unix socket shared
            # between the database and its backup sidecar.
            "install -d -o 999 -g 999 /data/postgres /data/run",
            # Disk and memory metrics for alarms. A failure here must not stop the host booting.
            "( dnf install -y amazon-cloudwatch-agent && "
            f"cat > /opt/aws/amazon-cloudwatch-agent/etc/tsp.json <<'EOF'\n{_CW_AGENT_CONFIG}\nEOF\n"
            "/opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl -a fetch-config -m ec2 "
            "-c file:/opt/aws/amazon-cloudwatch-agent/etc/tsp.json -s ) "
            '|| echo "cloudwatch agent setup failed"',
        )

    def _allow_cloudfront(self, port: int) -> None:
        lookup = cr.AwsCustomResource(
            self,
            "CloudFrontPrefixList",
            on_create=cr.AwsSdkCall(
                service="EC2",
                action="describeManagedPrefixLists",
                parameters={
                    "Filters": [{"Name": "prefix-list-name", "Values": [CLOUDFRONT_PREFIX_LIST]}]
                },
                physical_resource_id=cr.PhysicalResourceId.of("cloudfront-prefix-list"),
            ),
            policy=cr.AwsCustomResourcePolicy.from_sdk_calls(
                resources=cr.AwsCustomResourcePolicy.ANY_RESOURCE
            ),
        )
        prefix_list_id = lookup.get_response_field("PrefixLists.0.PrefixListId")
        # Note: this prefix list counts ~55 entries against the 60-rule security-group quota.
        self.security_group.add_ingress_rule(
            ec2.Peer.prefix_list(prefix_list_id),
            ec2.Port.tcp(port),
            "CloudFront origin-facing",
        )

    @property
    def public_dns(self) -> str:
        """EC2 public DNS name of the Elastic IP (CloudFront needs a hostname, not an IP)."""
        dashed = cdk.Fn.join("-", cdk.Fn.split(".", self.elastic_ip.ref))
        return cdk.Fn.join(
            "", ["ec2-", dashed, ".", cdk.Stack.of(self).region, ".compute.amazonaws.com"]
        )


class AppService(Construct):
    """One container on the host with host networking, logs to CloudWatch and safe rollout.

    ``stop_before_start`` (min healthy 0%, max 100%) is required for anything holding a data
    directory or a fixed host port: the old task stops before the new one starts.
    """

    def __init__(
        self,
        scope: Construct,
        id: str,
        *,
        host: Host,
        name: str,
        image: ecs.ContainerImage,
        port: int | None,
        cpu: int,
        memory_mib: int,
        environment: Mapping[str, str] | None = None,
        secrets: Mapping[str, ecs.Secret] | None = None,
        command: list[str] | None = None,
        health_check: list[str] | None = None,
        volumes: Sequence[tuple[str, str]] = (),
        user: str | None = None,
        log_retention: logs.RetentionDays = logs.RetentionDays.TWO_WEEKS,
    ) -> None:
        super().__init__(scope, id)
        self.task_definition = ecs.Ec2TaskDefinition(
            self, "Task", network_mode=ecs.NetworkMode.HOST
        )
        mounts = []
        for i, (host_path, container_path) in enumerate(volumes):
            self.task_definition.add_volume(name=f"v{i}", host=ecs.Host(source_path=host_path))
            mounts.append(
                ecs.MountPoint(
                    container_path=container_path, source_volume=f"v{i}", read_only=False
                )
            )
        container = self.task_definition.add_container(
            "main",
            image=image,
            cpu=cpu,
            memory_limit_mib=memory_mib,
            command=command,
            user=user,
            environment=dict(environment or {}),
            secrets=dict(secrets or {}),
            essential=True,
            logging=ecs.LogDrivers.aws_logs(stream_prefix=name, log_retention=log_retention),
            health_check=ecs.HealthCheck(
                command=["CMD-SHELL", *health_check] if health_check else ["CMD-SHELL", "exit 0"],
                interval=cdk.Duration.seconds(15),
                timeout=cdk.Duration.seconds(5),
                retries=3,
                start_period=cdk.Duration.seconds(30),
            ),
        )
        container.add_mount_points(*mounts)
        self.container = container
        if port:
            container.add_port_mappings(ecs.PortMapping(container_port=port))
        self.service = ecs.Ec2Service(
            self,
            "Service",
            cluster=host.cluster,
            task_definition=self.task_definition,
            service_name=f"{name}",
            desired_count=1,
            min_healthy_percent=0,
            max_healthy_percent=100,
            circuit_breaker=ecs.DeploymentCircuitBreaker(enable=True, rollback=True),
            capacity_provider_strategies=[
                ecs.CapacityProviderStrategy(
                    capacity_provider=host.capacity_provider.capacity_provider_name, weight=1
                )
            ],
        )
