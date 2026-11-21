"""
Demo catalogue = the 16 fictitious products of the existing React mock (src/lib/shop-data.ts), loaded through the
same models and services the real catalogue uses. Prices, brands and availability are FICTITIOUS demo data.

Images are generated placeholders (owned by Maral) unless `assets_dir` points at the frontend's demo images.
Delivery-estimate rules reproduce the numbers hard-coded in the UI (2-4 / 14-21 working days) and are demo-only.
"""

import io
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from django.db import transaction
from django.utils import timezone
from PIL import Image, ImageDraw

from apps.brands.models import Brand, BrandTranslation
from apps.categories.models import Category, CategoryTranslation
from apps.product_sources.models import IntegrationType, ProductSource
from apps.reviews.models import PublicationRights, Review, ReviewStatus

from .models import (
    AttributeOption,
    AttributeOptionTranslation,
    DeliveryEstimateRule,
    FulfillmentType,
    Product,
    ProductAttribute,
    ProductAttributeTranslation,
    ProductAttributeValue,
    ProductStatus,
    ProductTranslation,
    ProductVariant,
    ProductVariantOption,
    VariantAvailability,
    VariantPrice,
)
from .services import images as image_service
from .services import projection
from .signals import suspend_projection_refresh

CATEGORIES = [
    ("women", "پوشاک زنانه", "Women", "کالکشن‌های فصل از طراحان و برندهای محبوب جهان.", "women.jpg"),
    ("men", "پوشاک مردانه", "Men", "دوخت دقیق، پارچه‌های ممتاز و استایلی بی‌زمان.", "men.jpg"),
    ("shoes", "کفش", "Shoes", "از کتانی‌های روزمره تا کفش‌های رسمی چرم.", "shoes.jpg"),
    ("bags", "کیف", "Bags", "کیف‌های چرمی و دست‌دوز برای هر موقعیت.", "bag.jpg"),
    ("accessories", "اکسسوری", "Accessories", "جزئیاتی که استایل تو را کامل می‌کنند.", "accessories.jpg"),
    ("beauty", "آرایشی", "Beauty", "عطر و مراقبت پوست از برندهای منتخب.", "perfume.jpg"),
]
COLORS = {
    "black": ("مشکی", "Black", "oklch(0.2 0 0)"),
    "cream": ("کرم", "Cream", "oklch(0.94 0.02 85)"),
    "camel": ("شتری", "Camel", "oklch(0.65 0.08 65)"),
    "brown": ("قهوه‌ای", "Brown", "oklch(0.42 0.06 50)"),
    "navy": ("سرمه‌ای", "Navy", "oklch(0.3 0.06 260)"),
    "white": ("سفید", "White", "oklch(0.98 0 0)"),
    "gold": ("طلایی", "Gold", "oklch(0.78 0.11 85)"),
}
SOURCES = [
    ("maral", "Maral", "IR", "IRT", IntegrationType.INTERNAL, 0),
    ("trendyol", "Trendyol", "TR", "TRY", IntegrationType.MANUAL, 1),
    ("namshi", "Namshi", "AE", "AED", IntegrationType.MANUAL, 2),
]
# slug, brand, fa name, en name, original price, currency, list price (toman), discount, category, source,
# image files, sizes, colours, available, fulfillment, is_new, fa description
P = [
    (
        "belted-wool-coat",
        "Atelier Nour",
        "پالتو بلند کمربنددار پشمی",
        "Belted Long Wool Coat",
        8900,
        "TRY",
        18_400_000,
        20,
        "women",
        "trendyol",
        ["p-trench", "women"],
        ["S", "M", "L", "XL"],
        ["camel", "black"],
        True,
        "order",
        True,
        "پالتویی بلند از ترکیب پشم و کشمیر با کمربند هم‌رنگ و یقه کت‌شلواری؛ انتخابی بی‌زمان برای روزهای سرد.",
    ),
    (
        "cable-knit-turtleneck",
        "Lumière",
        "یقه‌اسکی بافت گیس‌باف",
        "Cable-Knit Turtleneck",
        2400,
        "TRY",
        5_200_000,
        0,
        "women",
        "trendyol",
        ["women", "p-trench"],
        ["XS", "S", "M", "L"],
        ["cream", "camel"],
        True,
        "stock",
        False,
        "بافت درشت و نرم با یقه بلند و فرم آزاد؛ مناسب ست با شلوار پاچه‌گشاد.",
    ),
    (
        "leather-handbag",
        "Maison Sable",
        "کیف دستی چرم طبیعی",
        "Natural Leather Handbag",
        1450,
        "AED",
        38_500_000,
        15,
        "bags",
        "namshi",
        ["bag", "p-blackbag"],
        ["One Size"],
        ["camel", "brown"],
        True,
        "order",
        True,
        "کیف دستی ساختارمند از چرم گاوی تمام‌دانه با یراق برنجی و بند قابل تنظیم.",
    ),
    (
        "quilted-chain-bag",
        "Noir Atelier",
        "کیف دوشی کوئیلت با زنجیر",
        "Quilted Chain Shoulder Bag",
        980,
        "AED",
        26_900_000,
        25,
        "bags",
        "namshi",
        ["p-blackbag", "bag"],
        ["One Size"],
        ["black", "cream"],
        True,
        "stock",
        False,
        "کیف کوئیلت‌دوزی شده با زنجیر طلایی؛ قابل استفاده به‌صورت دوشی و کراس‌بادی.",
    ),
    (
        "chunky-beige-sneakers",
        "Strada",
        "کتانی چانکی بژ",
        "Chunky Beige Sneakers",
        3100,
        "TRY",
        4_650_000,
        10,
        "shoes",
        "trendyol",
        ["p-sneaker", "shoes"],
        ["38", "39", "40", "41", "42", "43"],
        ["cream", "white"],
        True,
        "stock",
        True,
        "کتانی سبک با زیره فوم‌دار و ترکیب جیر و مش تنفسی برای استفاده روزمره.",
    ),
    (
        "suede-chelsea-boots",
        "Oxford & Co",
        "بوت چلسی جیر",
        "Suede Chelsea Boots",
        5200,
        "TRY",
        9_800_000,
        0,
        "shoes",
        "trendyol",
        ["p-boots", "shoes"],
        ["40", "41", "42", "43", "44"],
        ["brown", "black"],
        True,
        "order",
        False,
        "بوت چلسی کلاسیک از جیر ایتالیایی با کش پهلو و زیره چرمی دوخت گودیر.",
    ),
    (
        "white-leather-sneakers",
        "Strada",
        "کتانی چرمی سفید مینیمال",
        "Minimalist White Leather Sneakers",
        2800,
        "TRY",
        4_200_000,
        30,
        "shoes",
        "trendyol",
        ["shoes", "p-sneaker"],
        ["39", "40", "41", "42", "43", "44"],
        ["white", "black"],
        False,
        "order",
        False,
        "کتانی کم‌ارتفاع از چرم نرم با طراحی تمیز و بدون لوگو.",
    ),
    (
        "charcoal-wool-overcoat",
        "Hollis",
        "پالتو پشمی مردانه ذغالی",
        "Charcoal Wool Overcoat",
        1890,
        "AED",
        32_000_000,
        20,
        "men",
        "namshi",
        ["men", "p-shirt"],
        ["M", "L", "XL", "XXL"],
        ["black", "navy"],
        True,
        "order",
        True,
        "پالتو تک‌سینه با برش راسته از پشم مرینو؛ ستون اصلی کمد زمستانی.",
    ),
    (
        "oxford-polo-set",
        "Hollis",
        "ست پیراهن آکسفورد و پولو بافت",
        "Oxford Shirt & Knit Polo Set",
        1650,
        "TRY",
        3_600_000,
        0,
        "men",
        "trendyol",
        ["p-shirt", "men"],
        ["S", "M", "L", "XL"],
        ["white", "navy"],
        True,
        "stock",
        False,
        "پیراهن آکسفورد یقه‌دکمه‌دار به همراه پولو بافت پنبه‌ای.",
    ),
    (
        "silk-chain-scarf",
        "Soie Persane",
        "روسری ابریشم طرح زنجیر",
        "Silk Chain-Print Scarf",
        340,
        "AED",
        7_400_000,
        10,
        "accessories",
        "namshi",
        ["p-scarf", "accessories"],
        ["90×90"],
        ["camel", "cream"],
        True,
        "stock",
        False,
        "روسری ابریشم خالص با لبه دست‌دوز و طرح زنجیر در تناژ کاراملی.",
    ),
    (
        "classic-gold-watch",
        "Ora",
        "ساعت مچی طلایی کلاسیک",
        "Classic Gold Wrist Watch",
        2100,
        "AED",
        46_000_000,
        12,
        "accessories",
        "namshi",
        ["accessories", "p-scarf"],
        ["One Size"],
        ["gold"],
        True,
        "order",
        False,
        "ساعت مستطیلی با بند فلزی آبکاری طلا و صفحه اعداد رومی.",
    ),
    (
        "amber-wood-parfum",
        "Ambre Studio",
        "ادوپرفیوم عنبر و چوب",
        "Amber & Wood Eau de Parfum",
        4200,
        "TRY",
        6_900_000,
        15,
        "beauty",
        "trendyol",
        ["p-perfume", "accessories"],
        ["50ml", "100ml"],
        ["gold"],
        True,
        "stock",
        True,
        "رایحه‌ای گرم با نت‌های عنبر، صندل و وانیل؛ ماندگاری بالا.",
    ),
    (
        "pleated-wide-trousers",
        "Lumière",
        "شلوار پاچه‌گشاد پلیسه",
        "Pleated Wide-Leg Trousers",
        1900,
        "TRY",
        3_950_000,
        20,
        "women",
        "maral",
        ["women", "hero"],
        ["S", "M", "L"],
        ["cream", "black"],
        True,
        "stock",
        False,
        "شلوار فاق‌بلند با پلیسه جلو از کرپ سنگین و ریزش زیبا.",
    ),
    (
        "champagne-satin-set",
        "Atelier Nour",
        "ست ساتن شامپاینی",
        "Champagne Satin Set",
        6200,
        "TRY",
        12_700_000,
        0,
        "women",
        "trendyol",
        ["hero", "women"],
        ["XS", "S", "M", "L"],
        ["cream", "camel"],
        True,
        "order",
        True,
        "ست ساتن براق با کت پشمی بلند؛ برای مهمانی‌های عصرانه.",
    ),
    (
        "black-tuxedo-blazer",
        "Noir Atelier",
        "کت تاکسیدو زنانه مشکی",
        "Black Tuxedo Blazer",
        2250,
        "AED",
        41_500_000,
        18,
        "women",
        "namshi",
        ["offer", "hero"],
        ["S", "M", "L"],
        ["black"],
        False,
        "order",
        False,
        "کت تاکسیدو با یقه ساتن و سرشانه ساختارمند؛ قدرت در سادگی.",
    ),
    (
        "silk-moisturizing-cream",
        "Ambre Studio",
        "کرم مرطوب‌کننده ابریشمی",
        "Silk Moisturizing Cream",
        1300,
        "TRY",
        2_450_000,
        0,
        "beauty",
        "maral",
        ["p-perfume", "p-scarf"],
        ["50ml"],
        ["cream"],
        True,
        "stock",
        False,
        "کرم سبک با پپتید و اسکوالان برای پوستی نرم و درخشان.",
    ),
]
SIZE_ORDER = [
    "XS",
    "S",
    "M",
    "L",
    "XL",
    "XXL",
    "38",
    "39",
    "40",
    "41",
    "42",
    "43",
    "44",
    "50ml",
    "100ml",
    "90×90",
    "One Size",
]
BRAND_FA = {"Soie Persane": "ابریشم پارسی", "Lumière": "لومیر"}
PALETTE = ["#c9b8a3", "#8a7968", "#d9cfc4", "#4b4a55", "#b08d6e", "#e8e0d5"]


def final_toman(list_toman: int, discount: int) -> int:
    """Mirror of `finalPrice` in src/lib/shop-data.ts: round to the nearest 1,000 Toman."""
    return round(list_toman * (100 - discount) / 100 / 1000) * 1000


def _placeholder(index: int, size=(240, 300)) -> bytes:
    image = Image.new("RGB", size, PALETTE[index % len(PALETTE)])
    ImageDraw.Draw(image).rectangle((10, 10, size[0] - 10, size[1] - 10), outline="#ffffff", width=3)
    buffer = io.BytesIO()
    image.save(buffer, "JPEG")
    return buffer.getvalue()


def _image_bytes(stem: str, index: int, assets_dir: Path | None) -> bytes:
    if assets_dir:
        path = assets_dir / f"{stem}.jpg"
        if path.exists():
            return path.read_bytes()
    return _placeholder(index)


def _opt_name_fa(code: str) -> str:
    return "تک‌سایز" if code == "one-size" else code.upper() if code.isalpha() else code


def load_demo_catalog(*, assets_dir: Path | None = None, with_images: bool = True, with_reviews: bool = True) -> dict:
    with transaction.atomic(), suspend_projection_refresh():
        created = _load(assets_dir, with_images, with_reviews)
    projection.refresh_many(created)
    from . import cache

    cache.bump_version()
    return {"products": len(created), "variants": ProductVariant.objects.filter(product_id__in=created).count()}


def _load(assets_dir, with_images, with_reviews) -> list:
    now = timezone.now()
    sources = {}
    for code, name, country, currency, kind, order in SOURCES:
        sources[code], _ = ProductSource.objects.update_or_create(
            code=code,
            defaults={
                "name": name,
                "country_code": country,
                "base_currency": currency,
                "integration_type": kind,
                "sort_order": order,
                "terms_note": "Demo source; no live integration.",
            },
        )
    size_attr, _ = ProductAttribute.objects.get_or_create(
        code="size", defaults={"value_type": "option", "is_variant_axis": True, "sort_order": 1}
    )
    color_attr, _ = ProductAttribute.objects.get_or_create(
        code="color", defaults={"value_type": "option", "is_variant_axis": True, "sort_order": 2}
    )
    material_attr, _ = ProductAttribute.objects.get_or_create(
        code="material", defaults={"value_type": "text", "sort_order": 3}
    )
    for attr, fa, en in ((size_attr, "سایز", "Size"), (color_attr, "رنگ", "Color"), (material_attr, "جنس", "Material")):
        ProductAttributeTranslation.objects.update_or_create(attribute=attr, language="fa", defaults={"name": fa})
        ProductAttributeTranslation.objects.update_or_create(attribute=attr, language="en", defaults={"name": en})

    categories = {}
    for order, (slug, fa, en, desc, _img) in enumerate(CATEGORIES):
        categories[slug], _ = Category.objects.update_or_create(
            slug=slug, defaults={"sort_order": order, "is_active": True}
        )
        CategoryTranslation.objects.update_or_create(
            category=categories[slug], language="fa", defaults={"name": fa, "description": desc}
        )
        CategoryTranslation.objects.update_or_create(category=categories[slug], language="en", defaults={"name": en})

    brands = {}
    for brand_name in sorted({row[1] for row in P}):
        slug = brand_name.lower().replace("&", "and").replace("è", "e").replace("é", "e").replace(" ", "-")
        brands[brand_name], _ = Brand.objects.update_or_create(
            slug=slug, defaults={"name": brand_name, "is_active": True}
        )
        if brand_name in BRAND_FA:
            BrandTranslation.objects.update_or_create(
                brand=brands[brand_name], language="fa", defaults={"name_local": BRAND_FA[brand_name]}
            )

    color_options, size_options = {}, {}
    for order, (code, (fa, en, swatch)) in enumerate(COLORS.items()):
        color_options[code], _ = AttributeOption.objects.update_or_create(
            attribute=color_attr, code=code, defaults={"swatch": swatch, "sort_order": order}
        )
        AttributeOptionTranslation.objects.update_or_create(
            option=color_options[code], language="fa", defaults={"name": fa}
        )
        AttributeOptionTranslation.objects.update_or_create(
            option=color_options[code], language="en", defaults={"name": en}
        )

    def size_option(label: str):
        code = label.lower().replace(" ", "-")
        if code not in size_options:
            order = SIZE_ORDER.index(label)
            size_options[code], _ = AttributeOption.objects.update_or_create(
                attribute=size_attr, code=code, defaults={"sort_order": order}
            )
            AttributeOptionTranslation.objects.update_or_create(
                option=size_options[code], language="fa", defaults={"name": _opt_name_fa(code)}
            )
            AttributeOptionTranslation.objects.update_or_create(
                option=size_options[code], language="en", defaults={"name": label}
            )
        return size_options[code]

    for fulfillment, lo, hi in ((FulfillmentType.DIRECT_INVENTORY, 2, 4), (FulfillmentType.PROXY_PURCHASE, 14, 21)):
        DeliveryEstimateRule.objects.update_or_create(
            fulfillment_type=fulfillment,
            source=None,
            defaults={"min_days": lo, "max_days": hi, "note": "DEMO values copied from the UI mock"},
        )

    created = []
    for rank, row in enumerate(P):
        (
            slug,
            brand,
            fa_name,
            en_name,
            orig,
            cur,
            list_toman,
            disc,
            cat,
            src,
            files,
            sizes,
            colors,
            available,
            fulfil,
            is_new,
            fa_desc,
        ) = row
        product, _ = Product.objects.update_or_create(
            slug=slug,
            defaults={
                "brand": brands[brand],
                "category": categories[cat],
                "status": ProductStatus.ACTIVE,
                "popularity_score": float(100 - rank),
                "new_until": now + timedelta(days=30) if is_new else None,
                "published_at": now,
            },
        )
        ProductTranslation.objects.update_or_create(
            product=product,
            language="fa",
            defaults={"name": fa_name, "description": fa_desc, "short_description": fa_desc[:100]},
        )
        ProductTranslation.objects.update_or_create(
            product=product,
            language="en",
            defaults={"name": en_name, "description": f"{en_name} by {brand}. (Demo text.)"},
        )
        fulfillment_type = FulfillmentType.DIRECT_INVENTORY if fulfil == "stock" else FulfillmentType.PROXY_PURCHASE
        for s_index, size in enumerate(sizes):
            for c_index, color in enumerate(colors):
                sku = f"{slug}-{size.lower().replace(' ', '-')}-{color}".upper()
                variant, _ = ProductVariant.objects.update_or_create(
                    sku=sku,
                    defaults={
                        "product": product,
                        "fulfillment_type": fulfillment_type,
                        "source": sources[src],
                        "sort_order": s_index * 10 + c_index,
                        "is_active": True,
                    },
                )
                for attr, option in ((size_attr, size_option(size)), (color_attr, color_options[color])):
                    ProductVariantOption.objects.get_or_create(
                        variant=variant, attribute=attr, defaults={"option": option}
                    )
                list_t = list_toman * 16 // 10 if (slug == "amber-wood-parfum" and size == "100ml") else list_toman
                VariantPrice.objects.update_or_create(
                    variant=variant,
                    defaults={
                        "original_amount": Decimal(orig),
                        "original_currency": cur,
                        "list_irr": Decimal(list_t * 10),
                        "final_irr": Decimal(final_toman(list_t, disc) * 10),
                        "discount_percent": Decimal(disc),
                    },
                )
                # demo twist: XL of the first coat is sold out, everything else follows the product flag
                in_stock = available and not (slug == "belted-wool-coat" and size == "XL")
                VariantAvailability.objects.update_or_create(
                    variant=variant, defaults={"status": "in_stock" if in_stock else "out_of_stock"}
                )
        if slug in ("belted-wool-coat", "leather-handbag"):
            ProductAttributeValue.objects.get_or_create(
                product=product,
                attribute=material_attr,
                language="fa",
                defaults={"value_text": "پشم و کشمیر" if "coat" in slug else "چرم گاوی تمام‌دانه"},
            )
            ProductAttributeValue.objects.get_or_create(
                product=product,
                attribute=material_attr,
                language="en",
                defaults={"value_text": "Wool & cashmere" if "coat" in slug else "Full-grain cow leather"},
            )
        if with_images and not product.images.exists():
            for position, stem in enumerate(files):
                image_service.add_product_image(
                    product,
                    _image_bytes(stem, rank + position, assets_dir),
                    rights="owned",
                    alt="",
                    sort_order=position,
                )
        created.append(product.pk)

    if with_reviews:
        coat = Product.objects.get(slug="belted-wool-coat")
        Review.objects.get_or_create(
            product=coat,
            author_name="سارا",
            defaults={
                "rating": 5,
                "title": "عالی",
                "body": "کیفیت دوخت فوق‌العاده بود.",
                "status": ReviewStatus.PUBLISHED,
                "publication_rights": PublicationRights.CUSTOMER_CONSENT,
                "verified_purchase": True,
            },
        )
        Review.objects.get_or_create(
            product=coat,
            author_name="Reza",
            defaults={
                "rating": 4,
                "body": "Nice fit.",
                "language": "en",
                "status": ReviewStatus.PUBLISHED,
                "publication_rights": PublicationRights.CUSTOMER_CONSENT,
            },
        )
        Review.objects.get_or_create(
            product=coat,
            author_name="no-rights",
            defaults={
                "rating": 1,
                "body": "hidden: no publication right",
                "status": ReviewStatus.PUBLISHED,
                "publication_rights": PublicationRights.NONE,
            },
        )
        Review.objects.get_or_create(
            product=coat,
            author_name="pending",
            defaults={
                "rating": 2,
                "body": "hidden: pending",
                "status": ReviewStatus.PENDING,
                "publication_rights": PublicationRights.CUSTOMER_CONSENT,
            },
        )

    return created
