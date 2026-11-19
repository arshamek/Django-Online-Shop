from django.contrib.auth.backends import ModelBackend
from django.contrib.auth.hashers import check_password, make_password

_dummy_hash: str | None = None


class IdentifierBackend(ModelBackend):
    """
    `authenticate(username=<email or phone>, password=...)` for the Django admin and shell. Honours the same
    progressive lockout as the API login (a locked account simply fails to authenticate here).
    The API itself uses services.auth.authenticate_credentials for richer, distinct errors.
    """

    def authenticate(self, request, username=None, password=None, **kwargs):
        global _dummy_hash
        from .services import lockout
        from .services.auth import find_user

        identifier = username or kwargs.get("email")
        if not identifier or password is None:
            return None
        user, canonical = find_user(identifier)
        key = lockout.identifier_key(canonical)
        if user is not None and user.is_locked:
            return None
        if user is None:
            _dummy_hash = _dummy_hash or make_password("timing-equaliser")
            check_password(password, _dummy_hash)
            lockout.register_failure(request, None, key)
            return None
        if user.check_password(password) and self.user_can_authenticate(user):
            lockout.register_success(user, key)
            return user
        lockout.register_failure(request, user, key)
        return None
