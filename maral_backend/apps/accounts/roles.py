"""
Staff roles = Django groups whose permissions are defined here (single source of truth, synced
after every migrate and by `manage.py sync_roles`). Do not edit these groups by hand: changes
are overwritten. Create a separate group for ad-hoc needs.

"Super Admin" is not a group: it is `User.is_superuser` (all permissions, bypasses checks).
"""

from django.contrib.auth.models import Group, Permission

SUPER_ADMIN = "Super Admin"

ROLE_PERMISSIONS: dict[str, list[str]] = {
    "Product Manager": ["catalog_manage", "ai_manage"],
    "Import Manager": ["imports_manage", "imports_review_matches"],
    "Finance Manager": ["pricing_manage", "payments_view", "payments_manage", "refunds_manage", "orders_view"],
    "Order Manager": ["orders_view", "orders_manage", "shipping_manage", "shipments_manage", "customers_view"],
    "Customer Support": ["customers_view", "customers_suspend", "customers_unlock", "orders_view"],
}
ALL_ROLES = [SUPER_ADMIN, *ROLE_PERMISSIONS]


def sync_roles() -> None:
    for role, codenames in ROLE_PERMISSIONS.items():
        group, _ = Group.objects.get_or_create(name=role)
        permissions = Permission.objects.filter(content_type__app_label="accounts", codename__in=codenames)
        missing = set(codenames) - set(permissions.values_list("codename", flat=True))
        if missing:
            raise RuntimeError(f"Permissions not created yet for role {role!r}: {sorted(missing)}")
        group.permissions.set(permissions)


def roles_for(user) -> list[str]:
    roles = list(user.groups.filter(name__in=ROLE_PERMISSIONS).values_list("name", flat=True))
    return ([SUPER_ADMIN] if user.is_superuser else []) + sorted(roles)
