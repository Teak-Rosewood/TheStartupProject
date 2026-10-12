"""Who may do what. Roles are Cognito groups of the *staff* pool, so customers can never hold one."""

from __future__ import annotations

ROLES = ("support", "finance", "superadmin")  # lowest to highest privilege
_RANK = {r: i for i, r in enumerate(ROLES)}

# (view identity, action) pairs; "*" matches anything. superadmin gets everything.
_READ = ("list", "details")
GRANTS: dict[str, set[tuple[str, str]]] = {
    "support": {
        (view, act)
        for view in (
            "user",
            "org",
            "membership",
            "invite",
            "consent",
            "data-request",
            "api-key",
            "audit-log",
        )
        for act in _READ
    }
    | {(view, act) for view in ("payment", "subscription", "flag", "usage-record") for act in _READ}
    | {("staff-actions", "*")},
    "finance": {
        (view, act)
        for view in (
            "user",
            "org",
            "membership",
            "payment",
            "subscription",
            "invoice",
            "refund",
            "transfer",
        )
        for act in _READ
    }
    | {("finance-tools", "*"), ("staff-actions", "*"), ("metrics", "*")},
    "superadmin": {("*", "*")},
}
# Reasoned write actions on the staff-actions page: which roles may run them.
STAFF_ACTIONS: dict[str, set[str]] = {
    "disable_user": {"support", "superadmin"},
    "enable_user": {"support", "superadmin"},
    "sign_out_user": {"support", "superadmin"},
    "execute_deletion": {"superadmin"},
    "cancel_deletion": {"support", "superadmin"},
    "refund_payment": {"finance", "superadmin"},
    "flag_on": {"support", "superadmin"},
    "flag_off": {"support", "superadmin"},
    "flag_rollout": {"superadmin"},
}
# Support may only flip kill switches; every other flag is a superadmin decision.
KILL_SWITCH_ONLY = {"support"}


def role_for_groups(groups: list[str]) -> str | None:
    """Highest staff role among the user's groups, or None if they hold none."""
    held = [g for g in groups if g in _RANK]
    return max(held, key=_RANK.__getitem__) if held else None


def allowed(role: str | None, identity: str, action: str) -> bool:
    if role is None:
        return False
    grants = GRANTS.get(role, set())
    return bool({(identity, action), ("*", action), (identity, "*"), ("*", "*")} & grants)


def can_run(role: str | None, staff_action: str) -> bool:
    return role is not None and role in STAFF_ACTIONS.get(staff_action, set())
