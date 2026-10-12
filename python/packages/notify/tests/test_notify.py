import asyncio
import json

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import select, text
from tsp.jobs import JobRegistry, Worker
from tsp.notify import (
    MARKETING,
    Email,
    EmailPreference,
    FcmPush,
    Suppression,
    Templates,
    TransportError,
    mark_read,
    notify_user,
    register_device,
    register_jobs,
    register_with_compliance,
    unread_count,
    user_tokens,
)
from tsp.notify.sms import SnsSms

PEOPLE = {
    "welcome": {"name": "Asha", "cta_url": "https://app.example.com/start"},
    "invite": {
        "inviter": "Ravi",
        "org": "Acme Labs",
        "accept_url": "https://app.example.com/invite?t=abc",
        "days": 7,
    },
    "receipt": {
        "invoice_number": "INV/2627/000001",
        "amount": "INR 1,180.00",
        "description": "Pro plan",
        "invoice_url": "https://x/y.pdf",
    },
    "payment_failed": {"description": "Pro plan", "billing_url": "https://app.example.com/billing"},
    "deletion_scheduled": {"date": "11 November 2026"},
    "marketing_update": {"subject_line": "What's new", "content": "<p>Hello</p>"},
    "lead_confirm": {"confirm_url": "https://app.example.com/api/leads/confirm?token=abc"},
}


def test_every_template_renders_subject_html_and_text():
    t = Templates()
    for name, ctx in PEOPLE.items():
        r = t.render(
            name, {"brand": "Acme", "footer": "Acme Pvt Ltd", "unsubscribe_url": None, **ctx}
        )
        assert r.subject and "{{" not in r.subject and "<html" in r.html and r.text.strip()
        assert "<" not in r.text, name


def test_values_are_html_escaped_and_missing_ones_are_errors():
    t = Templates()
    r = t.render(
        "welcome",
        {
            "brand": "Acme",
            "footer": "",
            "unsubscribe_url": None,
            "name": "<script>alert(1)</script>",
            "cta_url": None,
        },
    )
    assert "<script>" not in r.html and "&lt;script&gt;" in r.html
    with pytest.raises(Exception, match="undefined"):
        t.render(
            "invite", {"brand": "Acme", "footer": "", "unsubscribe_url": None}
        )  # no inviter/org/...


def test_links_survive_in_the_plain_text_part():
    r = Templates().render(
        "invite", {"brand": "A", "footer": "", "unsubscribe_url": None, **PEOPLE["invite"]}
    )
    assert "https://app.example.com/invite?t=abc" in r.text


def test_unsubscribe_tokens_are_tamper_proof(notifier):
    tok = notifier.unsubscribe_token("Asha@Example.com")
    assert notifier.email_from_token(tok) == "asha@example.com"
    body, sig = tok.split(".")
    assert notifier.email_from_token(f"{body}.{'0' * len(sig)}") is None
    assert (
        notifier.email_from_token(tok.replace(body, "eA")) is None
        and notifier.email_from_token("garbage") is None
    )


async def test_transactional_mail_is_sent_once(db, notifier, transport):
    async with db.session() as s:
        mid = await notifier.queue(s, "Asha@Example.com", "welcome", PEOPLE["welcome"])
    async with db.session() as s:
        assert await notifier.deliver(s, mid) == "sent"
        assert await notifier.deliver(s, mid) == "sent"  # a retried job must not send twice
    assert len(transport.sent) == 1
    m = transport.sent[0]
    assert (m.to, m.sender, m.category, m.headers) == (
        "asha@example.com",
        "Acme <no-reply@example.com>",
        "transactional",
        {},
    )
    assert "Welcome to Acme" == m.message.subject
    async with db.session() as s:
        row = (await s.execute(select(Email))).scalar_one()
    assert (row.status, row.provider_message_id) == ("sent", "mid-1")


async def test_outbox_is_transactional(db, notifier):
    with pytest.raises(RuntimeError):
        async with db.session() as s:
            await notifier.queue(s, "a@b.co", "welcome", PEOPLE["welcome"])
            raise RuntimeError("the signup failed")
    async with db.session() as s:
        assert (await s.execute(select(Email))).first() is None
        assert (await s.execute(text("select count(*) from jobs"))).scalar_one() == 0


async def test_a_bad_template_context_fails_at_queue_time(db, notifier):
    async with db.session() as s:
        with pytest.raises(Exception, match="undefined"):
            await notifier.queue(s, "a@b.co", "invite", {})


async def test_bounced_and_complaining_addresses_are_never_mailed(db, notifier, transport):
    async with db.session() as s:
        await notifier.suppress(s, "gone@example.com", "bounce")
        mid = await notifier.queue(s, "gone@example.com", "welcome", PEOPLE["welcome"])
    async with db.session() as s:
        assert await notifier.deliver(s, mid) == "suppressed"
    assert transport.sent == []


async def test_marketing_requires_opt_in_and_carries_one_click_unsubscribe(db, notifier, transport):
    ctx = PEOPLE["marketing_update"]
    async with db.session() as s:
        a = await notifier.queue(s, "fan@example.com", "marketing_update", ctx, category=MARKETING)
    async with db.session() as s:
        assert await notifier.deliver(s, a) == "suppressed"  # nobody consented
        await notifier.set_marketing(s, "fan@example.com", True)
        b = await notifier.queue(s, "fan@example.com", "marketing_update", ctx, category=MARKETING)
    async with db.session() as s:
        assert await notifier.deliver(s, b) == "sent"
    m = transport.sent[0]
    assert m.headers["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    url = m.headers["List-Unsubscribe"].strip("<>")
    assert url.startswith("https://app.example.com/api/email/unsubscribe?token=")
    assert notifier.email_from_token(url.split("token=")[1]) == "fan@example.com"
    assert "Unsubscribe from these emails" in m.message.html


async def test_unsubscribing_stops_marketing_but_not_receipts(db, notifier, transport):
    async with db.session() as s:
        await notifier.set_marketing(s, "fan@example.com", True)
        await notifier.set_marketing(s, "fan@example.com", False)
        mk = await notifier.queue(
            s, "fan@example.com", "marketing_update", PEOPLE["marketing_update"], category=MARKETING
        )
        rc = await notifier.queue(s, "fan@example.com", "receipt", PEOPLE["receipt"])
    async with db.session() as s:
        assert await notifier.deliver(s, mk) == "suppressed"
        assert await notifier.deliver(s, rc) == "sent"
        await notifier.set_marketing(
            s, "fan@example.com", True
        )  # opting back in lifts the unsubscribe
        assert await notifier.marketing_allowed(s, "fan@example.com")


async def test_permanent_errors_fail_transient_errors_retry_through_the_job_queue(
    db, notifier, transport
):
    reg = JobRegistry()
    register_jobs(reg, notifier)
    worker = Worker(reg, db)
    async with db.session() as s:
        bad = await notifier.queue(s, "bad@example.com", "welcome", PEOPLE["welcome"])
        ok = await notifier.queue(s, "ok@example.com", "welcome", PEOPLE["welcome"])
    transport.errors = [
        TransportError("invalid address", permanent=True),
        TransportError("throttled"),
    ]
    await worker.run_once()
    async with db.session() as s:
        statuses = {e.to_addr: e.status for e in (await s.execute(select(Email))).scalars()}
        assert statuses == {
            "bad@example.com": "failed",
            "ok@example.com": "queued",
        }  # transient: still queued for retry
        await s.execute(text("UPDATE jobs SET run_at = now() WHERE status = 'queued'"))
    await worker.run_once()
    async with db.session() as s:
        assert (await s.get(Email, ok)).status == "sent"
        assert (await s.get(Email, bad)).status == "failed"


async def test_ses_bounce_and_complaint_events_suppress(db, notifier):
    permanent = {
        "eventType": "Bounce",
        "bounce": {
            "bounceType": "Permanent",
            "bouncedRecipients": [{"emailAddress": "dead@example.com"}],
        },
    }
    transient = {
        "eventType": "Bounce",
        "bounce": {
            "bounceType": "Transient",
            "bouncedRecipients": [{"emailAddress": "full@example.com"}],
        },
    }
    complaint = {
        "notificationType": "Complaint",
        "complaint": {"complainedRecipients": [{"emailAddress": "Angry@Example.com"}]},
    }
    async with db.session() as s:
        assert await notifier.handle_ses_event(s, json.dumps(permanent)) == 1
        assert await notifier.handle_ses_event(s, json.dumps(transient)) == 0
        assert (
            await notifier.handle_ses_event(
                s, json.dumps({"Type": "Notification", "Message": json.dumps(complaint)})
            )
            == 1
        )  # SNS envelope
    async with db.session() as s:
        got = {r.email: r.reason for r in (await s.execute(select(Suppression))).scalars()}
    assert got == {"dead@example.com": "bounce", "angry@example.com": "complaint"}


async def test_in_app_notifications_and_devices(db):
    async with db.session() as s:
        a = await notify_user(s, "u1", "invite", "You were invited")
        await notify_user(s, "u1", "receipt", "Payment received", url="/billing")
        await notify_user(s, "u2", "x", "other user")
        assert await unread_count(s, "u1") == 2
        assert await mark_read(s, "u1", [a]) == 1 and await unread_count(s, "u1") == 1
        assert await mark_read(s, "u2", [a]) == 0  # cannot read someone else's
        await register_device(s, "u1", "tok-aaaaaaaaaa", "android")
        await register_device(s, "u2", "tok-aaaaaaaaaa", "android")  # same phone, new user
        assert await user_tokens(s, "u1") == [] and await user_tokens(s, "u2") == ["tok-aaaaaaaaaa"]


async def test_compliance_export_and_erasure(db, notifier):
    from types import SimpleNamespace

    reg = SimpleNamespace(ex={}, er={})
    reg.register_exporter = lambda n, f: reg.ex.__setitem__(n, f)
    reg.register_eraser = lambda n, f: reg.er.__setitem__(n, f)

    async def email_of(session, sub):
        return "asha@example.com"

    register_with_compliance(reg, email_of)
    async with db.session() as s:
        await notify_user(s, "u1", "invite", "hello")
        await register_device(s, "u1", "tok-bbbbbbbbbb", "ios")
        await notifier.set_marketing(s, "asha@example.com", True)
        await notifier.suppress(s, "asha@example.com", "bounce")
        await notifier.queue(s, "asha@example.com", "welcome", PEOPLE["welcome"])
    async with db.session() as s:
        data = await reg.ex["notifications"](s, "u1")
        assert (
            data["email"]["marketing_consent"] is True
            and data["email"]["messages"][0]["template"] == "welcome"
        )
        await reg.er["notifications"](s, "u1")
    async with db.session() as s:
        mail = (await s.execute(select(Email))).scalar_one()
        assert mail.to_addr.startswith("deleted:") and mail.context == {}
        assert await s.get(EmailPreference, "asha@example.com") is None
        assert await s.get(Suppression, "asha@example.com") is not None  # a bounce is remembered
        assert (await s.execute(text("select count(*) from notifications"))).scalar_one() == 0
        assert (await s.execute(text("select count(*) from device_tokens"))).scalar_one() == 0


def _service_account():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    return {
        "project_id": "demo-proj",
        "client_email": "svc@demo-proj.iam.gserviceaccount.com",
        "private_key": pem,
    }


async def test_fcm_exchanges_a_token_once_and_reports_dead_devices():
    calls = {"token": 0, "send": []}

    def handler(req: httpx.Request):
        if req.url.host == "oauth2.googleapis.com":
            calls["token"] += 1
            assert b"assertion=" in req.content
            return httpx.Response(200, json={"access_token": "ya29.test", "expires_in": 3600})
        body = json.loads(req.content)
        calls["send"].append((req.url.path, req.headers["authorization"], body["message"]["token"]))
        if body["message"]["token"] == "dead-token":
            return httpx.Response(
                404,
                json={"error": {"status": "NOT_FOUND", "details": [{"errorCode": "UNREGISTERED"}]}},
            )
        return httpx.Response(200, json={"name": "projects/demo-proj/messages/1"})

    push = FcmPush(
        _service_account(),
        http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        clock=lambda: 1_790_000_000,
    )
    res = await push.send(["good-1", "dead-token", "good-2"], "Hi", "There", {"k": "v"})
    assert res.sent == 2 and res.invalid_tokens == ["dead-token"]
    assert calls["token"] == 1  # one OAuth exchange serves the whole batch
    assert calls["send"][0][:2] == ("/v1/projects/demo-proj/messages:send", "Bearer ya29.test")
    await push.send(["good-3"], "Hi", "There")
    assert calls["token"] == 1  # and the next call


def test_sms_uses_dlt_attributes_and_validates_numbers():
    sms = SnsSms("ap-south-1", sender_id="ACMEIN", entity_id="1101234567890")
    attrs = sms.attributes(template_id="1107123456789")
    assert attrs["AWS.SNS.SMS.SMSType"]["StringValue"] == "Transactional"
    assert (
        attrs["AWS.MM.SMS.EntityId"]["StringValue"] == "1101234567890"
        and attrs["AWS.MM.SMS.TemplateId"]["StringValue"] == "1107123456789"
    )
    assert attrs["AWS.SNS.SMS.SenderID"]["StringValue"] == "ACMEIN"
    with pytest.raises(ValueError, match="E.164"):
        asyncio.run(sms.send("9876543210", "hello"))


async def test_sqs_poller_deletes_only_what_it_handled(db, notifier, monkeypatch):
    from tsp.notify import events

    deleted = []

    class FakeSqs:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def receive_message(self, **kw):
            good = {
                "eventType": "Complaint",
                "complaint": {"complainedRecipients": [{"emailAddress": "c@example.com"}]},
            }
            return {
                "Messages": [
                    {"MessageId": "1", "ReceiptHandle": "h1", "Body": json.dumps(good)},
                    {"MessageId": "2", "ReceiptHandle": "h2", "Body": "not json"},
                ]
            }

        async def delete_message(self, **kw):
            deleted.append(kw["ReceiptHandle"])

    class FakeSession:
        def client(self, *a, **k):
            return FakeSqs()

    monkeypatch.setattr(events.aioboto3, "Session", FakeSession)
    assert await events.poll_ses_events(notifier, db, "https://sqs/queue") == 1
    assert deleted == ["h1"]  # the unparseable message stays for the dead-letter queue
    async with db.session() as s:
        assert (await s.get(Suppression, "c@example.com")).reason == "complaint"
