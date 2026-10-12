"""Alarms (SNS email) and the monthly budget. See docs/aws/08."""

from __future__ import annotations

import aws_cdk as cdk
import aws_cdk.aws_budgets as budgets
import aws_cdk.aws_cloudwatch as cloudwatch
import aws_cdk.aws_cloudwatch_actions as actions
import aws_cdk.aws_sns as sns
import aws_cdk.aws_sns_subscriptions as subs
from constructs import Construct

from .config import EnvConfig


def _alarm(
    scope: Construct,
    id: str,
    metric: cloudwatch.IMetric,
    *,
    threshold: float,
    description: str,
    topic: sns.ITopic,
    periods: int = 3,
    missing: cloudwatch.TreatMissingData | None = None,
) -> cloudwatch.Alarm:
    alarm = cloudwatch.Alarm(
        scope,
        id,
        metric=metric,
        threshold=threshold,
        evaluation_periods=periods,
        comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
        treat_missing_data=missing or cloudwatch.TreatMissingData.NOT_BREACHING,
        alarm_description=description,
    )
    alarm.add_alarm_action(actions.SnsAction(topic))
    return alarm


class Alarms(Construct):
    """Host health, capacity and cost alarms for the request path (ap-south-1)."""

    def __init__(self, scope: Construct, id: str, *, config: EnvConfig, asg_name: str) -> None:
        super().__init__(scope, id)
        self.topic = sns.Topic(
            self, "Topic", topic_name=f"{config.prefix}-alarms", enforce_ssl=True
        )
        self.topic.add_subscription(subs.EmailSubscription(config.alarm_email))

        five_min = cdk.Duration.minutes(5)
        asg_dim = {"AutoScalingGroupName": asg_name}
        _alarm(
            self,
            "StatusCheck",
            cloudwatch.Metric(
                namespace="AWS/EC2",
                metric_name="StatusCheckFailed",
                dimensions_map=asg_dim,
                statistic="Maximum",
                period=cdk.Duration.minutes(1),
            ),
            threshold=0,
            periods=2,
            topic=self.topic,
            description="EC2 status check failing: the host is unhealthy (the group replaces it)",
        )
        _alarm(
            self,
            "Cpu",
            cloudwatch.Metric(
                namespace="AWS/EC2",
                metric_name="CPUUtilization",
                dimensions_map=asg_dim,
                statistic="Average",
                period=five_min,
            ),
            threshold=80,
            periods=3,
            topic=self.topic,
            description="CPU above 80% for 15 minutes: time to scale (docs/aws/10)",
        )
        _alarm(
            self,
            "Disk",
            cloudwatch.Metric(
                namespace="TSP/Host",
                metric_name="disk_used_percent",
                dimensions_map={"path": "/data"},
                statistic="Maximum",
                period=five_min,
            ),
            threshold=80,
            periods=2,
            topic=self.topic,
            description="/data above 80%: Postgres will run out of space",
        )
        _alarm(
            self,
            "Memory",
            cloudwatch.Metric(
                namespace="TSP/Host",
                metric_name="mem_used_percent",
                statistic="Maximum",
                period=five_min,
            ),
            threshold=90,
            periods=3,
            topic=self.topic,
            description="Host memory above 90%: move to a bigger instance or to RDS",
        )

        budgets.CfnBudget(
            self,
            "Budget",
            budget=budgets.CfnBudget.BudgetDataProperty(
                budget_name=f"{config.prefix}-monthly",
                budget_type="COST",
                time_unit="MONTHLY",
                budget_limit=budgets.CfnBudget.SpendProperty(amount=config.budget_usd, unit="USD"),
            ),
            notifications_with_subscribers=[
                budgets.CfnBudget.NotificationWithSubscribersProperty(
                    notification=budgets.CfnBudget.NotificationProperty(
                        notification_type=kind,
                        comparison_operator="GREATER_THAN",
                        threshold=pct,
                        threshold_type="PERCENTAGE",
                    ),
                    subscribers=[
                        budgets.CfnBudget.SubscriberProperty(
                            subscription_type="EMAIL", address=config.alarm_email
                        )
                    ],
                )
                for kind, pct in (
                    ("ACTUAL", 50),
                    ("ACTUAL", 80),
                    ("ACTUAL", 100),
                    ("FORECASTED", 100),
                )
            ],
        )


class EdgeAlarms(Construct):
    """CloudFront error alarm. CloudFront metrics live in us-east-1, so this has its own topic."""

    def __init__(
        self, scope: Construct, id: str, *, config: EnvConfig, distribution_id: str
    ) -> None:
        super().__init__(scope, id)
        topic = sns.Topic(
            self, "Topic", topic_name=f"{config.prefix}-edge-alarms", enforce_ssl=True
        )
        topic.add_subscription(subs.EmailSubscription(config.alarm_email))
        _alarm(
            self,
            "Errors5xx",
            cloudwatch.Metric(
                namespace="AWS/CloudFront",
                metric_name="5xxErrorRate",
                dimensions_map={"DistributionId": distribution_id, "Region": "Global"},
                statistic="Average",
                period=cdk.Duration.minutes(5),
            ),
            threshold=5,
            periods=3,
            topic=topic,
            description="CloudFront 5xx rate above 5% for 15 minutes: users are seeing errors",
        )
