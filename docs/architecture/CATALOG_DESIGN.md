# Catalogue design (Phase 4)

## Apps and models

| App | Models |
|---|---|
| `categories` | `Category` (adjacency + materialised `path`), `CategoryTranslation` |
| `brands` | `Brand`, `BrandTranslation`, `BrandAlias` |
| `product_sources` | `ProductSource` (code, country, base currency, integration type, `allows_image_copy`, `terms_note`) |
| `catalog` | `Product`, `ProductTranslation`, `ProductVariant`, `ProductVariantOption`, `ProductAttribute(+Translation)`, `AttributeOption(+Translation)`, `ProductAttributeValue` (specs), `VariantPrice`, `VariantAvailability`, `DeliveryEstimateRule`, `ProductImage`, `ProductListingProjection`, `ProductListingSource` |
| `reviews` | `Review` (moderation status + publication right) |

Service layer: `services/projection.py`, `services/images.py`, `services/publishing.py`, `services/variants.py`;
read side: `selectors.py`, `params.py`, `presenters.py`, `search/`; HTTP: `views/public.py`, `views/staff.py`.

## Fulfillment types

`ProductVariant.fulfillment_type` is stored per variant: `DIRECT_INVENTORY` (Maral stock) or `PROXY_PURCHASE`
(bought from the source store on order). A direct-inventory variant keeps its **origin source** (the UI shows a
Trendyol/Namshi badge on stocked items); a proxy purchase cannot use Maral's own source. A product may mix both
(`MIXED`).

## Visibility

A product is public iff `ProductListingProjection` exists. The projection is written by
`services.projection.refresh_projection` only when: status `active`, brand active, category **and its ancestors**
active, ≥ 1 active variant with a price. It is refreshed on commit by signals (product, variant, price,
availability; brand/category via Celery), rebuildable with `manage.py rebuild_catalog_projection`, and holds
the denormalised columns listing queries filter/sort on (indexed: category+availability+price, brand+price,
popularity, new_until, discount, category path). **Bulk writers that bypass signals (`QuerySet.update`, imports) must
call `refresh_many()`.**

## Search

`SearchBackend` port (`apps/catalog/search/base.py`), selected by `CATALOG_SEARCH_BACKEND`. The database backend
matches every token (AND) against normalised localised titles, brand name / local name / aliases, category names and
exact SKU, via `IN`/`EXISTS` (no row duplication), ranked exact title (100) > brand (60) > title prefix (50) > partial.
Limits: 2–100 chars, 6 tokens. **Known limit:** `LIKE '%x%'` cannot use an index; fine for a starter catalogue,
and the reason the port exists (swap in a search engine at scale). Brand/category names are matched in their stored
spelling (titles are normalised).

## Images

Files live in the default storage (S3-compatible when `STORAGE_BACKEND=s3`, unsigned public URLs, optional CDN domain).
Upload: size/pixel caps, decoder verification, JPEG/PNG/WEBP only, **re-encode** (drops EXIF/GPS), 480 px WEBP thumbnail.
Every image needs a `rights` record (`owned | licensed | source_permitted`); `source_permitted` is refused unless
`ProductSource.allows_image_copy`. The service never fetches over the network; `origin_url` is staff reference only and is
never served. Only published images that hold a stored file are returned.

## Deliberately not in this phase

* Currency conversion, margins, price history → pricing phase (`VariantPrice` is written manually today, `origin=manual`).
* Real stock/reservations, supplier availability observations → inventory/imports phases (`VariantAvailability` is the catalogue-visible state).
* `ExternalProduct`, source price history, import matching → imports phase.
* Size charts, wishlist state (`in_wishlist`), review submission, CMS banners/hero copy.
* Staff API covers products, translations, variants (+price/availability), images and publishing; brands, categories,
  sources, attributes and delivery rules are managed in Django Admin (same `catalog_manage` permission).

## Verification status — read this

* Tests (`tests/test_catalog_*.py`, 184 cases) run on **SQLite** (`TEST_DATABASE=sqlite`). They have **not** been run against SQL Server
  (no `mssql-django`/ODBC driver was available). Constraints use filtered unique indexes instead of `nulls_distinct`, no cascade-path
  risk (Django cascades in Python), index keys are < 900 bytes. Run the suite against SQL Server before relying on it.
* 8 pre-existing tests fail in SQLite mode (`mssql` backend not importable; Phase 3 NULL-uniqueness semantics). They fail identically before Phase 4.

## Open questions (no silent rules were invented)

1. **Recently viewed**: keep client-side ids, or store server-side history for logged-in users (privacy/retention decision)?
2. **Machine translations** are shown and flagged (`CATALOG_SHOW_MACHINE_TRANSLATIONS`). Should unreviewed text be hidden on the storefront?
3. **Reviews**: collection, verified-purchase rules and imported-review licences (publication rights are enforced, collection is not built).
4. **Delivery estimates**: the real business ranges per fulfillment type/source (the UI's 2–4 / 14–21 are demo data only).
5. **Mixed products**: should the storefront expose a per-variant fulfillment choice or keep the single badge?
6. **Popularity**: `popularity_score` is 0 until a ranking job exists; `popular` sort falls back to newest.
7. **Banners/hero copy** (`index.tsx`) are static frontend content; is a CMS wanted?
