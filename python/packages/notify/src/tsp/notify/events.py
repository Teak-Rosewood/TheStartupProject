"""Consume SES bounce/complaint events from SQS (SES -> SNS -> SQS). No public endpoint is involved."""

from __future__ import annotations

import logging
from typing import Any

import aioboto3
from tsp.db import Database

from .mailer import Notifier

log = logging.getLogger("tsp.notify")


async def poll_ses_events(
    notifier: Notifier,
    db: Database,
    queue_url: str,
    *,
    region: str | None = None,
    max_messages: int = 10,
) -> int:
    """Read up to ``max_messages`` events, suppress the affected addresses, delete what was handled.

    A message that cannot be parsed is left for the queue's dead-letter policy rather than lost.
    """
    handled = 0
    async with aioboto3.Session().client("sqs", region_name=region) as sqs:
        resp: dict[str, Any] = await sqs.receive_message(
            QueueUrl=queue_url, MaxNumberOfMessages=max_messages, WaitTimeSeconds=5
        )
        for m in resp.get("Messages", []):
            try:
                async with db.session() as s:
                    await notifier.handle_ses_event(s, m["Body"])
                await sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=m["ReceiptHandle"])
                handled += 1
            except Exception:
                log.exception("could not process SES event %s", m.get("MessageId"))
    return handled
