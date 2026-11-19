import io
import uuid

from django.conf import settings
from django.core.files.base import ContentFile
from PIL import Image, ImageOps, UnidentifiedImageError
from rest_framework.exceptions import ValidationError

from apps.audit import services as audit

from ..models import CustomerProfile, User
from ..normalizers import normalize_text

ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP"}
AVATAR_SIZE = 512


def get_profile(user: User) -> CustomerProfile:
    profile, _ = CustomerProfile.objects.get_or_create(user=user)
    return profile


def update_profile(request, user: User, data: dict) -> CustomerProfile:
    profile = get_profile(user)
    changed = []
    for field in ("first_name", "last_name"):
        if field in data:
            setattr(user, field, normalize_text(data[field]))
            changed.append(field)
    if changed:
        user.save(update_fields=changed)
    for field in ("language", "currency", "birth_date"):
        if field in data:
            setattr(profile, field, data[field])
            changed.append(field)
    profile.save()
    if changed:
        audit.record("account.profile.updated", request=request, target=user, fields=sorted(changed))
    return profile


def _process_image(uploaded) -> bytes:
    if uploaded.size > settings.AVATAR_MAX_BYTES:
        raise ValidationError({"avatar": [f"Image must be at most {settings.AVATAR_MAX_BYTES // 1024} KB."]})
    data = uploaded.read(settings.AVATAR_MAX_BYTES + 1)
    try:
        probe = Image.open(io.BytesIO(data))
        if probe.format not in ALLOWED_FORMATS:
            raise ValidationError({"avatar": ["Only JPEG, PNG or WebP images are accepted."]})
        if probe.width * probe.height > settings.AVATAR_MAX_PIXELS:
            raise ValidationError({"avatar": ["Image dimensions are too large."]})
        image = Image.open(io.BytesIO(data))
        image.load()  # full decode: catches truncated/corrupt files
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, SyntaxError) as exc:
        raise ValidationError({"avatar": ["The file is not a valid image."]}) from exc
    image = ImageOps.exif_transpose(image)
    if image.mode in ("RGBA", "LA", "P"):
        rgba = image.convert("RGBA")
        background = Image.new("RGB", rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba.split()[-1])
        image = background
    else:
        image = image.convert("RGB")
    image = ImageOps.fit(image, (AVATAR_SIZE, AVATAR_SIZE))
    out = io.BytesIO()
    image.save(out, format="WEBP", quality=85)  # re-encoding drops EXIF/GPS and any appended payload
    return out.getvalue()


def set_avatar(request, user: User, uploaded) -> CustomerProfile:
    content = _process_image(uploaded)
    profile = get_profile(user)
    old = profile.avatar.name if profile.avatar else None
    profile.avatar.save(f"{uuid.uuid4().hex}.webp", ContentFile(content), save=False)
    profile.save()
    if old:
        profile.avatar.storage.delete(old)
    audit.record("account.avatar.changed", request=request, target=user)
    return profile


def remove_avatar(request, user: User) -> None:
    profile = get_profile(user)
    if profile.avatar:
        name = profile.avatar.name
        profile.avatar = None
        profile.save()
        profile.avatar.storage.delete(name)
        audit.record("account.avatar.removed", request=request, target=user)
