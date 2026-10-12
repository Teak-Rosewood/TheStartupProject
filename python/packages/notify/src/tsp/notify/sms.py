"""SMS through Amazon SNS. In India every transactional SMS must use a DLT-registered sender id and template."""

from __future__ import annotations

import re
from typing import Any

import aioboto3

_E164 = re.compile(r"^\+[1-9]\d{7,14}$")


class SnsSms:
    def __init__(
        self,
        region: str | None = None,
        *,
        sender_id: str | None = None,
        entity_id: str | None = None,
    ):
        self._region, self.sender_id, self.entity_id = region, sender_id, entity_id
        self._session = aioboto3.Session()

    def attributes(self, template_id: str | None = None) -> dict[str, dict[str, str]]:
        def s(v: str) -> dict[str, str]:
            return {"DataType": "String", "StringValue": v}

        attrs = {"AWS.SNS.SMS.SMSType": s("Transactional")}
        if self.sender_id:
            attrs["AWS.SNS.SMS.SenderID"] = s(self.sender_id)
        if self.entity_id:  # DLT principal entity id
            attrs["AWS.MM.SMS.EntityId"] = s(self.entity_id)
        if template_id:  # DLT content template id
            attrs["AWS.MM.SMS.TemplateId"] = s(template_id)
        return attrs

    async def send(self, to: str, text: str, *, template_id: str | None = None) -> str:
        if not _E164.match(to):
            raise ValueError("phone number must be in E.164 format, e.g. +919876543210")
        async with self._session.client("sns", region_name=self._region) as sns:
            resp: dict[str, Any] = await sns.publish(
                PhoneNumber=to, Message=text, MessageAttributes=self.attributes(template_id)
            )
        return str(resp["MessageId"])
