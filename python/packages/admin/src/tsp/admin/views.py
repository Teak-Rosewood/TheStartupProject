"""Read-only model views. Staff never edit rows directly: writes go through the reasoned, audited
staff-actions page, so every change has a reason and an audit entry."""

from __future__ import annotations

from sqladmin import ModelView
from tsp.accounts import ApiKey, Invite, Membership, Org, User
from tsp.audit import AuditLog
from tsp.compliance import Consent, DataRequest
from tsp.flags import Flag
from tsp.metering import UsageRecord
from tsp.payments import Invoice, Payment, Refund, Subscription, Transfer


class _ReadOnly(ModelView):
    can_create = False
    can_edit = False
    can_delete = False
    can_export = False
    page_size = 50
    page_size_options = [25, 50, 100]


class UserView(_ReadOnly, model=User):
    name, name_plural, identity, icon = "User", "Users", "user", "fa-solid fa-user"
    column_list = [
        User.sub,
        User.email,
        User.name,
        User.created_at,
        User.last_seen_at,
        User.disabled_at,
        User.deleted_at,
    ]
    column_searchable_list = [User.email, User.name, User.sub]
    column_sortable_list = [User.created_at, User.last_seen_at]
    column_default_sort = [(User.created_at, True)]
    column_details_exclude_list = [User.attribution]


class OrgView(_ReadOnly, model=Org):
    name, name_plural, identity, icon = (
        "Organization",
        "Organizations",
        "org",
        "fa-solid fa-building",
    )
    column_list = [Org.id, Org.name, Org.slug, Org.plan, Org.created_at, Org.deleted_at]
    column_searchable_list = [Org.name, Org.slug]
    column_default_sort = [(Org.created_at, True)]


class MembershipView(_ReadOnly, model=Membership):
    name, name_plural, identity, icon = (
        "Membership",
        "Memberships",
        "membership",
        "fa-solid fa-users",
    )
    column_list = [Membership.org_id, Membership.user_sub, Membership.role, Membership.created_at]
    column_searchable_list = [Membership.org_id, Membership.user_sub]


class InviteView(_ReadOnly, model=Invite):
    name, name_plural, identity, icon = "Invite", "Invites", "invite", "fa-solid fa-envelope"
    column_list = [
        Invite.org_id,
        Invite.email,
        Invite.role,
        Invite.expires_at,
        Invite.accepted_at,
        Invite.revoked_at,
    ]
    column_details_exclude_list = [Invite.token_hash]


class ApiKeyView(_ReadOnly, model=ApiKey):
    name, name_plural, identity, icon = "API key", "API keys", "api-key", "fa-solid fa-key"
    column_list = [
        ApiKey.org_id,
        ApiKey.name,
        ApiKey.prefix,
        ApiKey.scopes,
        ApiKey.last_used_at,
        ApiKey.revoked_at,
    ]
    column_details_exclude_list = [ApiKey.key_hash]  # the hash never leaves the database


class ConsentView(_ReadOnly, model=Consent):
    name, name_plural, identity, icon = (
        "Consent",
        "Consents",
        "consent",
        "fa-solid fa-file-signature",
    )
    column_list = [
        Consent.user_sub,
        Consent.document,
        Consent.version,
        Consent.accepted_at,
        Consent.source,
    ]
    column_searchable_list = [Consent.user_sub]
    column_default_sort = [(Consent.accepted_at, True)]


class DataRequestView(_ReadOnly, model=DataRequest):
    name, name_plural, identity, icon = (
        "Data request",
        "Data requests",
        "data-request",
        "fa-solid fa-inbox",
    )
    column_list = [
        DataRequest.id,
        DataRequest.user_sub,
        DataRequest.kind,
        DataRequest.status,
        DataRequest.requested_at,
        DataRequest.scheduled_for,
    ]
    column_searchable_list = [DataRequest.user_sub, DataRequest.id]
    column_default_sort = [(DataRequest.requested_at, True)]
    column_details_exclude_list = [DataRequest.result_key]


class AuditView(_ReadOnly, model=AuditLog):
    name, name_plural, identity, icon = (
        "Audit entry",
        "Audit log",
        "audit-log",
        "fa-solid fa-clipboard-list",
    )
    column_list = [
        AuditLog.ts,
        AuditLog.actor_sub,
        AuditLog.actor_role,
        AuditLog.action,
        AuditLog.target_type,
        AuditLog.target_id,
        AuditLog.ip,
    ]
    column_searchable_list = [AuditLog.action, AuditLog.actor_sub, AuditLog.target_id]
    column_default_sort = [(AuditLog.ts, True)]


class PaymentView(_ReadOnly, model=Payment):
    name, name_plural, identity, icon = (
        "Payment",
        "Payments",
        "payment",
        "fa-solid fa-indian-rupee-sign",
    )
    column_list = [
        Payment.id,
        Payment.org_id,
        Payment.provider,
        Payment.plan_code,
        Payment.amount,
        Payment.currency,
        Payment.status,
        Payment.created_at,
    ]
    column_searchable_list = [
        Payment.id,
        Payment.org_id,
        Payment.provider_payment_id,
        Payment.provider_order_id,
    ]
    column_default_sort = [(Payment.created_at, True)]


class SubscriptionView(_ReadOnly, model=Subscription):
    name, name_plural, identity, icon = (
        "Subscription",
        "Subscriptions",
        "subscription",
        "fa-solid fa-repeat",
    )
    column_list = [
        Subscription.org_id,
        Subscription.plan_code,
        Subscription.provider,
        Subscription.status,
        Subscription.current_period_end,
        Subscription.cancel_at_period_end,
    ]
    column_searchable_list = [Subscription.org_id, Subscription.provider_subscription_id]


class InvoiceView(_ReadOnly, model=Invoice):
    name, name_plural, identity, icon = "Invoice", "Invoices", "invoice", "fa-solid fa-file-invoice"
    column_list = [
        Invoice.number,
        Invoice.kind,
        Invoice.org_id,
        Invoice.issued_at,
        Invoice.currency,
        Invoice.total,
    ]
    column_searchable_list = [Invoice.number, Invoice.org_id]
    column_default_sort = [(Invoice.issued_at, True)]


class RefundView(_ReadOnly, model=Refund):
    name, name_plural, identity, icon = "Refund", "Refunds", "refund", "fa-solid fa-rotate-left"
    column_list = [
        Refund.payment_id,
        Refund.provider_refund_id,
        Refund.amount,
        Refund.status,
        Refund.created_at,
    ]


class TransferView(_ReadOnly, model=Transfer):
    name, name_plural, identity, icon = "Transfer", "Transfers", "transfer", "fa-solid fa-share"
    column_list = [
        Transfer.payment_id,
        Transfer.seller_org_id,
        Transfer.amount,
        Transfer.status,
        Transfer.created_at,
    ]


class FlagView(_ReadOnly, model=Flag):
    name, name_plural, identity, icon = "Feature flag", "Feature flags", "flag", "fa-solid fa-flag"
    column_list = [
        Flag.key,
        Flag.enabled,
        Flag.rollout_percent,
        Flag.client_visible,
        Flag.updated_by,
        Flag.updated_at,
    ]
    column_searchable_list = [Flag.key, Flag.description]


class UsageView(_ReadOnly, model=UsageRecord):
    name, name_plural, identity, icon = "Usage record", "Usage", "usage-record", "fa-solid fa-gauge"
    column_list = [
        UsageRecord.ts,
        UsageRecord.org_id,
        UsageRecord.kind,
        UsageRecord.model,
        UsageRecord.units_in,
        UsageRecord.units_out,
        UsageRecord.credits,
        UsageRecord.status,
    ]
    column_searchable_list = [UsageRecord.org_id, UsageRecord.request_id]
    column_default_sort = [(UsageRecord.ts, True)]


VIEWS = [
    UserView,
    OrgView,
    MembershipView,
    InviteView,
    ApiKeyView,
    ConsentView,
    DataRequestView,
    AuditView,
    PaymentView,
    SubscriptionView,
    InvoiceView,
    RefundView,
    TransferView,
    FlagView,
    UsageView,
]
