"""DynamoDB table for key-based data. On-demand billing: $0 idle, no capacity planning."""

from __future__ import annotations

import aws_cdk as cdk
import aws_cdk.aws_dynamodb as dynamodb
from constructs import Construct

from .config import EnvConfig


class Store(Construct):
    def __init__(self, scope: Construct, id: str, *, config: EnvConfig) -> None:
        super().__init__(scope, id)
        self.table = dynamodb.Table(
            self,
            "Table",
            table_name=f"{config.prefix}-app",
            partition_key=dynamodb.Attribute(name="pk", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="sk", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=True
            ),
            time_to_live_attribute="expires_at",
            deletion_protection=True,
            removal_policy=cdk.RemovalPolicy.RETAIN,
        )
