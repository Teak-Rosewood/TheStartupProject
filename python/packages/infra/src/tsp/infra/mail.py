"""Email: an SES domain identity with DKIM, two configuration sets, and bounce/complaint events in SQS."""

from __future__ import annotations

import aws_cdk as cdk
import aws_cdk.aws_ecs as ecs
import aws_cdk.aws_iam as iam
import aws_cdk.aws_secretsmanager as secretsmanager
import aws_cdk.aws_ses as ses
import aws_cdk.aws_sns as sns
import aws_cdk.aws_sns_subscriptions as subs
import aws_cdk.aws_sqs as sqs
from constructs import Construct

from .config import EnvConfig


class Mail(Construct):
    def __init__(self, scope: Construct, id: str, *, config: EnvConfig) -> None:
        super().__init__(scope, id)
        n = config.notify
        stack = cdk.Stack.of(self)
        self.identity = ses.EmailIdentity(
            self,
            "Domain",
            identity=ses.Identity.domain(n.domain),
            mail_from_domain=f"mail.{n.domain}",
        )
        # Marketing and transactional mail get separate reputations: a complaint about a newsletter
        # must not push your receipts into spam folders.
        self.sets = {
            kind: ses.ConfigurationSet(
                self,
                kind.title(),
                configuration_set_name=f"{config.prefix}-{kind}",
                reputation_metrics=True,
                sending_enabled=True,
                tls_policy=ses.ConfigurationSetTlsPolicy.REQUIRE,
            )
            for kind in ("transactional", "marketing")
        }
        topic = sns.Topic(
            self, "Events", topic_name=f"{config.prefix}-ses-events", enforce_ssl=True
        )
        dlq = sqs.Queue(
            self,
            "EventsDlq",
            retention_period=cdk.Duration.days(14),
            enforce_ssl=True,
            encryption=sqs.QueueEncryption.SQS_MANAGED,
        )
        self.queue = sqs.Queue(
            self,
            "EventsQueue",
            queue_name=f"{config.prefix}-ses-events",
            enforce_ssl=True,
            encryption=sqs.QueueEncryption.SQS_MANAGED,
            retention_period=cdk.Duration.days(4),
            visibility_timeout=cdk.Duration.seconds(60),
            dead_letter_queue=sqs.DeadLetterQueue(max_receive_count=5, queue=dlq),
        )
        topic.add_subscription(subs.SqsSubscription(self.queue))
        for kind, cs in self.sets.items():
            cs.add_event_destination(
                f"{kind.title()}Events",
                destination=ses.EventDestination.sns_topic(topic),
                events=[ses.EmailSendingEvent.BOUNCE, ses.EmailSendingEvent.COMPLAINT],
            )

        self.secret = secretsmanager.Secret(
            self,
            "NotifySecret",
            description=f"{config.prefix} signing key for email unsubscribe links",
            generate_secret_string=secretsmanager.SecretStringGenerator(
                password_length=48, exclude_punctuation=True
            ),
            removal_policy=cdk.RemovalPolicy.RETAIN,
        )
        self.environment = {
            "SES_FROM": f"{n.brand} <{n.from_address}>",
            "SES_CONFIG_SET_TRANSACTIONAL": self.sets["transactional"].configuration_set_name,
            "SES_CONFIG_SET_MARKETING": self.sets["marketing"].configuration_set_name,
            "SES_EVENTS_QUEUE_URL": self.queue.queue_url,
            "APP_URL": f"https://{config.domain.app}",
            "BRAND_NAME": n.brand,
            "EMAIL_FOOTER": n.footer,
        }
        self.secrets = {"NOTIFY_SECRET": ecs.Secret.from_secrets_manager(self.secret)}
        self._identity_arn = stack.format_arn(
            service="ses", resource="identity", resource_name=n.domain
        )
        self._set_arns = [
            stack.format_arn(
                service="ses", resource="configuration-set", resource_name=cs.configuration_set_name
            )
            for cs in self.sets.values()
        ]

        # DNS records to add at your registrar (DKIM, and the custom MAIL FROM domain).
        for i, (name, value) in enumerate(
            [
                (self.identity.dkim_dns_token_name1, self.identity.dkim_dns_token_value1),
                (self.identity.dkim_dns_token_name2, self.identity.dkim_dns_token_value2),
                (self.identity.dkim_dns_token_name3, self.identity.dkim_dns_token_value3),
            ],
            start=1,
        ):
            cdk.CfnOutput(self, f"DkimName{i}", value=name)
            cdk.CfnOutput(self, f"DkimValue{i}", value=value)

    def grant(self, role: iam.IRole) -> None:
        role.add_to_principal_policy(
            iam.PolicyStatement(
                actions=["ses:SendEmail"], resources=[self._identity_arn, *self._set_arns]
            )
        )
        self.queue.grant_consume_messages(role)
