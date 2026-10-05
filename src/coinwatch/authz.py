"""Roles and permission checks enforced before a service side effect.

Roles are bundles of permission strings from spec section 3. A missing
permission raises ``NotAuthorized``. This module does not touch the database.
``require`` reads ``actor.role``; the actor is a user row or any object with
that string attribute.
"""

from coinwatch.errors import NotAuthorized

ROLES: frozenset[str] = frozenset({"admin", "trader", "viewer", "auditor"})

ALL_PERMISSIONS: frozenset[str] = frozenset(
    {
        "coins.read",
        "coins.manage",
        "ticks.read",
        "bots.read",
        "bots.control",
        "strategies.read",
        "strategies.write",
        "trades.read",
        "trades.execute",
        "wallets.read",
        "wallets.manage",
        "alerts.read",
        "alerts.manage",
        "inference.invoke",
        "settings.manage",
        "users.read",
        "users.manage",
        "audit.read",
    }
)

_VIEWER_EXCLUDED: frozenset[str] = frozenset({"audit.read", "users.read"})

_VIEWER_PERMISSIONS: frozenset[str] = frozenset(
    permission
    for permission in ALL_PERMISSIONS
    if permission.endswith(".read") and permission not in _VIEWER_EXCLUDED
)

_TRADER_PERMISSIONS: frozenset[str] = frozenset(
    {
        "coins.read",
        "ticks.read",
        "bots.read",
        "bots.control",
        "strategies.read",
        "strategies.write",
        "trades.read",
        "trades.execute",
        "wallets.read",
        "alerts.read",
        "inference.invoke",
    }
)

_ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "admin": ALL_PERMISSIONS,
    "trader": _TRADER_PERMISSIONS,
    "viewer": _VIEWER_PERMISSIONS,
    "auditor": _VIEWER_PERMISSIONS | frozenset({"audit.read"}),
}


def permissions_for(role: str) -> frozenset[str]:
    """Return the permissions granted to ``role``.

    An unknown role grants nothing.
    """
    return _ROLE_PERMISSIONS.get(role, frozenset())


def require(actor: object, permission: str) -> None:
    """Raise ``NotAuthorized`` when ``actor`` lacks ``permission``."""
    role = getattr(actor, "role", None)
    if not isinstance(role, str) or permission not in permissions_for(role):
        raise NotAuthorized(permission)
