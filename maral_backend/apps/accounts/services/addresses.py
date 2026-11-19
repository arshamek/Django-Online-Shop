from django.conf import settings
from django.db import transaction

from apps.common.exceptions import BusinessRuleViolation

from ..models import Address, User


def _lock_user(user: User) -> None:
    """Serialise address mutations per user so the single-default invariant holds under concurrency."""
    User.objects.select_for_update().get(pk=user.pk)


def create_address(user: User, data: dict) -> Address:
    with transaction.atomic():
        _lock_user(user)
        if Address.objects.filter(user=user).count() >= settings.MAX_ADDRESSES_PER_USER:
            raise BusinessRuleViolation(
                f"You can save at most {settings.MAX_ADDRESSES_PER_USER} addresses.", code="address_limit_reached"
            )
        first = not Address.objects.filter(user=user).exists()
        make_default = first or bool(data.get("is_default"))
        if make_default:
            Address.objects.filter(user=user, is_default=True).update(is_default=False)
        data = {**data, "is_default": make_default}
        return Address.objects.create(user=user, **data)


def update_address(address: Address, data: dict) -> Address:
    with transaction.atomic():
        _lock_user(address.user)
        make_default = data.pop("is_default", None)
        for field, value in data.items():
            setattr(address, field, value)
        if make_default and not address.is_default:
            Address.objects.filter(user=address.user, is_default=True).update(is_default=False)
            address.is_default = True
        address.save()
        return address


def delete_address(address: Address) -> None:
    with transaction.atomic():
        _lock_user(address.user)
        was_default = address.is_default
        user = address.user
        address.delete()
        if was_default:
            successor = Address.objects.filter(user=user).order_by("-created_at", "-id").first()
            if successor is not None:
                successor.is_default = True
                successor.save(update_fields=["is_default", "updated_at"])
