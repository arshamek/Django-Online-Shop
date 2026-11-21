"""
Product image intake. Authorised assets only:

* uploads are validated (size, decoder, format, pixel count), re-encoded (drops EXIF/GPS and any payload
  hidden after the image data) and given a WEBP thumbnail; files go to the default storage (S3-compatible
  object storage in production, see STORAGE_SETTINGS);
* an image taken from a source store may only be stored when that source's terms permit copying
  (`ProductSource.allows_image_copy`); this module never fetches anything over the network itself;
* every image carries a rights record; `origin_url` is reference-only and is never served.
"""

import hashlib
import io

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Max
from PIL import Image, ImageOps, UnidentifiedImageError

from apps.common.exceptions import BusinessRuleViolation, DomainError

from ..models import ImageRights, Product, ProductImage

FORMATS = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp"}
THUMBNAIL_WIDTH = 480


def _limit(name: str, default: int) -> int:
    return int(getattr(settings, name, default))


def source_permits_copy(source) -> bool:
    return bool(source is not None and source.allows_image_copy)


def _encode(image: Image.Image, fmt: str) -> bytes:
    buffer = io.BytesIO()
    if fmt == "JPEG":
        image.convert("RGB").save(buffer, "JPEG", quality=88, optimize=True)
    elif fmt == "PNG":
        image.save(buffer, "PNG", optimize=True)
    else:
        image.save(buffer, "WEBP", quality=88)
    return buffer.getvalue()


def process_image(data: bytes) -> dict:
    if len(data) > _limit("CATALOG_IMAGE_MAX_BYTES", 5 * 1024 * 1024):
        raise DomainError("Image is too large.", code="image_too_large")
    try:
        with Image.open(io.BytesIO(data)) as probe:
            probe.verify()
        image = Image.open(io.BytesIO(data))
        fmt = image.format
        if fmt not in FORMATS:
            raise DomainError("Only JPEG, PNG and WEBP images are accepted.", code="image_format_unsupported")
        if image.width * image.height > _limit("CATALOG_IMAGE_MAX_PIXELS", 36_000_000):
            raise DomainError("Image dimensions are too large.", code="image_too_large")
        image = ImageOps.exif_transpose(image)
        image.load()
    except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError) as exc:
        raise DomainError("The file is not a valid image.", code="image_invalid") from exc

    clean = _encode(image, fmt)
    thumb = image.copy()
    if thumb.width > THUMBNAIL_WIDTH:
        thumb = thumb.resize((THUMBNAIL_WIDTH, round(thumb.height * THUMBNAIL_WIDTH / thumb.width)), Image.LANCZOS)
    return {
        "content": clean,
        "ext": FORMATS[fmt],
        "width": image.width,
        "height": image.height,
        "checksum": hashlib.sha256(clean).hexdigest(),
        "thumbnail": _encode(thumb, "WEBP"),
    }


@transaction.atomic
def add_product_image(
    product: Product,
    data: bytes,
    *,
    rights: str,
    alt: str = "",
    source=None,
    attribution: str = "",
    origin_url: str = "",
    variant=None,
    sort_order: int | None = None,
    is_published: bool = True,
) -> ProductImage:
    if rights not in ImageRights.values:
        raise DomainError(
            "A valid image rights record is required.", code="image_rights_required", fields={"rights": ["Invalid."]}
        )
    if rights == ImageRights.SOURCE_PERMITTED and not source_permits_copy(source):
        raise BusinessRuleViolation(
            "This source's terms do not permit copying its images.", code="image_copy_not_permitted"
        )
    if variant is not None and variant.product_id != product.pk:
        raise DomainError("Variant belongs to another product.", code="validation_error")

    processed = process_image(data)
    if sort_order is None:
        sort_order = (product.images.aggregate(m=Max("sort_order"))["m"] or 0) + 1 if product.images.exists() else 0
    image = ProductImage(
        product=product,
        variant=variant,
        alt=alt,
        sort_order=sort_order,
        width=processed["width"],
        height=processed["height"],
        checksum=processed["checksum"],
        rights=rights,
        source=source,
        attribution=attribution,
        origin_url=origin_url,
        is_published=is_published,
    )
    stem = processed["checksum"][:16]
    image.image.save(f"{stem}.{processed['ext']}", ContentFile(processed["content"]), save=False)
    image.thumbnail.save(f"{stem}-thumb.webp", ContentFile(processed["thumbnail"]), save=False)
    image.save()
    return image


@transaction.atomic
def reorder_images(product: Product, ordered_public_ids: list) -> None:
    images = {str(i.public_id): i for i in product.images.all()}
    wanted = [str(x) for x in ordered_public_ids]
    if len(set(wanted)) != len(wanted) or set(wanted) != set(images):
        raise DomainError("Provide every image of the product exactly once.", code="validation_error")
    for position, public_id in enumerate(wanted):
        if images[public_id].sort_order != position:
            ProductImage.objects.filter(pk=images[public_id].pk).update(sort_order=position)
