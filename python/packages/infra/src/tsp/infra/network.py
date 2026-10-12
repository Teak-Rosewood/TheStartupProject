"""VPC without a NAT gateway: public subnets for the host, isolated subnets reserved for RDS/ElastiCache."""

from __future__ import annotations

import aws_cdk.aws_ec2 as ec2
from constructs import Construct


class Network(Construct):
    def __init__(self, scope: Construct, id: str, *, availability_zones: list[str]) -> None:
        super().__init__(scope, id)
        self.vpc = ec2.Vpc(
            self,
            "Vpc",
            # Explicit AZs keep synth offline and deterministic (no context lookup, no credentials).
            availability_zones=availability_zones,
            nat_gateways=0,  # a NAT gateway is ~$32/mo; the host uses its own public IP instead
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="public", subnet_type=ec2.SubnetType.PUBLIC, cidr_mask=24
                ),
                ec2.SubnetConfiguration(
                    name="isolated", subnet_type=ec2.SubnetType.PRIVATE_ISOLATED, cidr_mask=24
                ),
            ],
            # Gateway endpoints are free and keep S3 / DynamoDB traffic off the internet.
            gateway_endpoints={
                "S3": ec2.GatewayVpcEndpointOptions(service=ec2.GatewayVpcEndpointAwsService.S3),
                "DynamoDB": ec2.GatewayVpcEndpointOptions(
                    service=ec2.GatewayVpcEndpointAwsService.DYNAMODB
                ),
            },
        )
