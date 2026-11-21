# Catalogue & storefront API (Phase 4)

Public, anonymous, GET-only. Base: `/api/v1`. Conventions from `API_CONVENTIONS.md` apply (error envelope,
pagination, no trailing slash). The trailing-slash form used in the Phase 4 brief (`/products/`) is **also
accepted** on every route; OpenAPI documents the canonical no-slash form.

## Endpoints

| Method | Path | Purpose | Frontend use |
|---|---|---|---|
| GET | `/storefront/homepage` | categories, brands, sources, trending (discounted), new arrivals | `index.tsx` sections |
| GET | `/categories` | active categories (flat, `parent` slug, subtree `product_count`) | category tiles, filter facet |
| GET | `/categories/{slug}` | category + breadcrumb + children | category pages |
| GET | `/brands` | brands with ≥ 1 visible product | brand strip, filter |
| GET | `/brands/{slug}` | one brand + `product_count` | brand page |
| GET | `/products` | filtered, sorted, paginated cards | `shop.tsx`, wishlist hydrate (`ids=`) |
| GET | `/products/search?q=` | same as `/products`, `q` required, relevance default | search overlay (`page_size=5`) |
| GET | `/products/filters` | facet counts under the active filters | filter sidebar |
| GET | `/products/{slug-or-uuid}` | full product detail | `product.$id.tsx` |
| GET | `/products/related/{id-or-slug}?limit=4` | related cards (max 12) | "you may also like" |
| GET | `/products/recently-viewed?ids=a,b` | hydrate client-held ids, order kept (max 50) | recently viewed strip |

Staff endpoints (`/staff/products…`, `/staff/variants/{id}`, `/staff/images/{id}`) require
`accounts.catalog_manage` (Product Manager, Super Admin) and are audited; see OpenAPI (`staff` tag).

## Adaptations of the brief (deliberate)

* **Paths**: no trailing slash is canonical (approved Phase 1 convention); slash form still works.
* **`/products/filters`** is a separate endpoint (as the brief asks), not an inline `facets` object.
* **`recently-viewed`** is stateless: the existing UI stores viewed ids client-side, so the endpoint hydrates
  ids. The server stores **no browsing history** (privacy decision; see open questions).
* **Product id** in URLs/JSON is the UUID `id` (never the internal bigint). `{slug}` routes also accept the UUID.

## Query parameters (`/products`, `/products/search`, `/products/filters`)

| Param | Meaning |
|---|---|
| `page`, `page_size` | page (default 24, max 100). `page × page_size ≤ 10 000` else 400 `invalid_filter` |
| `q` | text search, 2–100 chars (normalised: Arabic/Persian letters & digits, ZWNJ, case) |
| `category` | slug(s); includes sub-categories. Repeat or comma-separate (max 20 values) |
| `brand`, `source`, `size`, `color` | slug / source code / option code (max 20 values each, OR inside a group, AND across groups) |
| `price_min`, `price_max` | **final** price in the *display currency* (`IRT` default) |
| `sale` / `available` / `is_new` | `true` / `false` |
| `sort` | `popular` (default), `new`, `price_asc`, `price_desc`, `discount`, `relevance` (needs `q`; default when `q` present) |
| `ids` | uuid list (max 50) |
| `lang` / `currency` | `fa` (default) `en` `ar` `tr` / `IRT` (default) `IRR`; headers `Accept-Language`, `Accept-Currency` work too |

Unknown parameters and malformed values return **400 `invalid_filter`** with `error.fields` — never ignored.
`available=true` also restricts `size`/`color` to *sellable* variants; otherwise size/color match any active variant.

## Response shapes

Money is `{"amount": "14720000", "currency": "IRT"}` (decimal string). Stored amounts are IRR; Toman = IRR ÷ 10 is a
display unit only. `original` is the source-store price in its own currency.

Product card (list, search, related, homepage):

```json
{
  "id": "uuid", "slug": "belted-wool-coat",
  "name": "پالتو بلند کمربنددار پشمی", "language": "fa", "is_machine_translated": false,
  "brand": {"id": "uuid", "slug": "atelier-nour", "name": "Atelier Nour"},
  "category": {"id": "uuid", "slug": "women", "name": "پوشاک زنانه"},
  "images": [{"id": "uuid", "url": "…", "thumbnail_url": "…", "alt": "…", "width": 1920, "height": 1088, "attribution": null}],
  "price": {"original": {"amount": "8900", "currency": "TRY"}, "list": {…IRT}, "final": {…IRT},
            "currency": "IRT", "discount_percent": 20, "range": null},
  "source": {"code": "trendyol", "name": "Trendyol", "country_code": "TR", "is_direct_inventory": false},
  "fulfillment_type": "PROXY_PURCHASE", "fulfillment": "order",
  "available": true, "availability_status": "in_stock", "is_new": true,
  "sizes":  [{"code": "xl", "name": "XL", "available": false}],
  "colors": [{"code": "camel", "name": "شتری", "value": "oklch(…)", "available": true}]
}
```

Detail adds: `short_description`, `description`, `seo`, all `images`, `category.breadcrumb`, `specifications`
(`[{code,label,value}]`), `variants[]` (id, sku, size, color, `fulfillment_type`, `source`, `available`,
`availability_status`, `price` incl. `breakdown`, `delivery_estimate`), `delivery_estimate`, `reviews`
(`{count, average, items}`), `related[]`.

Field notes:

* `fulfillment_type` ∈ `DIRECT_INVENTORY | PROXY_PURCHASE | MIXED` (MIXED only at product level).
  `fulfillment` (`stock|order`) is the legacy two-value badge of the existing UI: `stock` only when every sellable
  variant is Maral stock.
* Card price = the **cheapest sellable variant** (cheapest active variant if none is sellable); `range` is non-null
  when variants differ in price. Its discount drives the `sale` filter and the `discount` sort.
* `price.breakdown` is `null` until the pricing phase defines fees/FX. **No currency conversion or margin is computed here.**
* `delivery_estimate` is `null` unless a `DeliveryEstimateRule` exists (source-specific rule wins over the generic one).
  No rules are shipped; the demo loader creates the UI's 2–4 / 14–21 values as *demo data*.
* `reviews` contain only reviews that are `published` **and** carry a publication right. The UI's hard-coded sample
  reviews must be removed by the frontend adapter.
* Localisation fallback: requested → `fa` → `en` → product source language → any. Brands show their canonical Latin
  name unless a translation exists for the *requested* language.

## Frontend mapping (existing React types → API)

| `shop-data.ts` | API |
|---|---|
| `id` | `id` (uuid) · `slug` is the route key |
| `brand` / `name` | `brand.name` / `name` |
| `originalPrice` + `originalCurrency` | `price.original.amount` + `.currency` |
| `priceToman` (list) / `finalPrice()` | `price.list.amount` / `price.final.amount` (**server-computed**, same 1 000-Toman rounding in demo data) |
| `discount` | `price.discount_percent` |
| `category` | `category.slug` |
| `source` ("Trendyol"…) | `source.name` |
| `images[]` | `images[].url` (`thumbnail_url` for cards) |
| `sizes[]` / `colors[]` | `sizes[].name` / `colors[].name` (+ `value` swatch) |
| `available` / `fulfillment` / `isNew` | `available` / `fulfillment` / `is_new` |

`tests/test_catalog_demo_fidelity.py` parses the real `src/lib/shop-data.ts` and asserts the demo API output equals it for all 16 products.

## Caching, limits, errors

* Server cache 60 s per `(path, query, lang, currency)`, invalidated by any catalogue write (version key).
  `Cache-Control: public, max-age=60, stale-while-revalidate=300`, `ETag`/`If-None-Match` → 304, `Vary: Accept-Language, Accept-Currency`.
* `/products/search` is throttled (`THROTTLE_RATE_CATALOG_SEARCH`, default 60/min per client). Other public GETs use the anonymous rate.
* Errors: `400 invalid_filter`, `404 not_found` (hidden products are 404, never 403), `429 rate_limited`.
