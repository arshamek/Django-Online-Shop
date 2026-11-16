# API Requirements

Everything here is **derived from the frontend as it exists**. Items marked ⚠ depend on unresolved business decisions (see FRONTEND_GAPS.md §3). Nothing is implemented.

## 1. Backend feature list
1. **Catalog**: products, variants (size/color), images, brands, categories, source stores, fulfillment mode, availability, discount, "new" flag, popularity ranking.
2. **Search & filtering**: text (Persian), facets with counts, sorting, pagination.
3. **Pricing**: original price + currency → final Toman price, discount, price breakdown ⚠ (FX, margin, fees not defined).
4. **Customer auth & profile**: ⚠ method undecided (checkout collects Iranian mobile → OTP is the natural fit).
5. **Addresses**: CRUD (frontend only shows one read-only).
6. **Wishlist**: per user (guest merge ⚠).
7. **Cart**: lines with variant + quantity, server re-pricing ⚠ (server vs client-held cart for guests).
8. **Checkout / shipping quote**: shipping options, free-shipping threshold, delivery estimates.
9. **Orders**: create, list, detail/tracking, statuses ⚠.
10. **Payments**: gateway integration, callback, status ⚠ (frontend shows "test payment" only).
11. **Reviews** ⚠ (frontend shows placeholders only).
12. **AI assistant**: conversational product search/compare returning product references.
13. **Newsletter subscription**.
14. **Storefront config**: shipping thresholds/costs, languages, currency display, banners.
15. **Localization**: fa/en/ar/tr content and strings ⚠ (what is translated — UI only, or product data too?).
16. **Admin/back-office** (not in the frontend, required to operate): product import (Trendyol/Namshi), FX rates, margins, stock, orders, proxy-purchase workflow ⚠.

## 2. API mapping matrix
Base path proposal: `/api/v1`. Auth: **Public** / **Optional** (guest or user) / **User**.

| Frontend Page | Component | Data Required | HTTP | Endpoint | Auth |
|---|---|---|---|---|---|
| Global | Header / banner | free-shipping threshold, currency, languages | GET | `/config/storefront` | Public |
| Global | Mega menu, Home categories, Shop filter | categories | GET | `/categories` | Public |
| Global | Mega menu, Home brands, Shop filter | brands (+counts) | GET | `/brands` | Public |
| Global | SearchOverlay | top 5 matches (name, brand, image) | GET | `/products?q=&page_size=5` | Public |
| Home | Trending | discounted products | GET | `/products?sale=true&page_size=8&sort=popular` | Public |
| Home | New carousel | new products | GET | `/products?is_new=true` | Public |
| Home | Source tiles | stores list | GET | `/products` facets (`facets.sources`) | Public |
| Home | Newsletter | email | POST | `/newsletter/subscriptions` | Public |
| Shop | Listing + filters + sort + show more | filter params, page | GET | `/products?category=&brand=&source=&sale=&available=&size=&color=&price_max=&q=&sort=&page=` | Public |
| Shop | Filter options | facets (sizes, colors, sources, price range, counts) | GET | same response `facets` | Public |
| Product | Detail | product, variants, images, specs, price breakdown, size chart, delivery estimate | GET | `/products/{id}` | Public |
| Product | Related | 4 related | GET | `/products/{id}/related` | Public |
| Product | Recently viewed / wishlist hydrate | products by ids | GET | `/products?ids=p1,p2` | Public |
| Product | Reviews tab | reviews ⚠ | GET | `/products/{id}/reviews` | Public |
| Product / Quick view / Wishlist | Add to cart | variant, qty | POST | `/cart/items` | Optional |
| Product / Card | Wishlist toggle | product id | PUT / DELETE | `/wishlist/{product_id}` | User ⚠ |
| Cart drawer | Cart lines & totals | cart | GET | `/cart` | Optional |
| Cart drawer | Qty/remove | line id, qty | PATCH / DELETE | `/cart/items/{line_id}` | Optional |
| Checkout | Shipping options & totals | cart, address (city) | POST | `/checkout/quote` | Optional |
| Checkout | Place order | customer, address, shipping, notes | POST | `/orders` | Optional ⚠ |
| Checkout | Start payment | order id | POST | `/orders/{id}/payments` | Optional ⚠ |
| Account | Profile | user | GET / PATCH | `/me` | User |
| Account | Orders | list | GET | `/orders` | User |
| (missing) | Order detail/tracking | order | GET | `/orders/{id}` | User |
| Account | Addresses | list/CRUD | GET / POST / PATCH / DELETE | `/me/addresses[/{id}]` | User |
| Account | Wishlist | list | GET | `/wishlist` | User |
| Account / Header | Language | preference | PATCH | `/me` (`language`) | User |
| Account | Logout | – | POST | `/auth/logout` | User |
| (missing) | Login/Register | phone → OTP | POST | `/auth/otp/request`, `/auth/otp/verify`, `/auth/refresh` ⚠ | Public |
| AI chat | Message | text, conversation id, context product ids | POST | `/assistant/messages` | Optional |

Resource reuse: search overlay, home sections, related, wishlist hydration and recently-viewed all reuse `/products` with parameters — no extra endpoints.

## 3. Page state requirements (data / loading / error / empty / auth)
| Page / component | Data source | Static or dynamic | Loading state needed | Error state needed | Empty state | Exists in frontend today? |
|---|---|---|---|---|---|---|
| Header nav/menu | categories, brands, config | dynamic (cacheable) | none (SSR/prefetch) | fall back to static nav | – | – |
| Search overlay | `/products?q` | dynamic | inline spinner | inline "try again" | "no results" (missing) | only chips when empty query |
| Home sections | products, categories, brands | dynamic + CMS copy | skeleton cards | per-section hide/retry | hide section | none |
| Shop list | `/products` | dynamic | skeleton grid, filter pending | retry banner | "no products" ✔ exists | empty ✔ only |
| Product page | `/products/{id}` | dynamic | skeleton | 404 ✔ / retry | – | 404 ✔ (Persian) |
| Related / recent | `/products` | dynamic | skeleton | hide section | hide ✔ (conditional) | hide ✔ |
| Cart drawer | `/cart` | dynamic | skeleton lines, per-line pending | line error (stock/price changed) | empty ✔ | empty ✔ |
| Checkout | quote/order | dynamic | button spinner | field + server errors; payment failure | empty cart ✔ | empty cart ✔ |
| Account profile/orders/addresses | `/me`… | dynamic | skeleton | retry | "no orders yet" (missing) | none |
| Wishlist | `/wishlist` | dynamic | skeleton | retry | empty ✔ | empty ✔ |
| AI chat | `/assistant/messages` | dynamic | typing indicator ✔ | "assistant unavailable" (missing) | greeting + chips ✔ | typing ✔ |
| Newsletter | POST | – | button pending | error text | – | success ✔ |

## 4. Proposed response examples
Conventions (proposal): money as **integers in Toman**; currency codes ISO-4217 (Toman shown as `IRT`); ids stay strings; images are absolute URLs; error envelope `{ "error": { "code", "message", "fields"? } }`; pagination `{count, page, page_size, results}`.

### 4.1 `GET /products?category=women&sort=new&page=1&page_size=8`
```json
{
  "count": 5, "page": 1, "page_size": 8,
  "results": [
    {
      "id": "p1", "slug": "atelier-nour-belted-wool-coat",
      "brand": { "id": "atelier-nour", "name": "Atelier Nour" },
      "name": "پالتو بلند کمربنددار پشمی",
      "category": "women",
      "images": [{ "url": "https://cdn.example/p1-1.jpg", "alt": "" }, { "url": "https://cdn.example/p1-2.jpg", "alt": "" }],
      "price": {
        "original": { "amount": 8900, "currency": "TRY" },
        "list_toman": 18400000,
        "discount_percent": 20,
        "final_toman": 14720000
      },
      "source": { "id": "trendyol", "name": "Trendyol", "country": "TR" },
      "fulfillment": "order",
      "available": true,
      "is_new": true,
      "in_wishlist": false
    }
  ],
  "facets": {
    "categories": [{ "id": "women", "count": 5 }],
    "brands": [{ "id": "lumiere", "name": "Lumière", "count": 3 }],
    "sources": [{ "id": "trendyol", "count": 9 }, { "id": "namshi", "count": 5 }, { "id": "maral", "count": 2 }],
    "sizes": ["XS", "S", "M", "L"],
    "colors": [{ "id": "black", "name": "مشکی", "value": "oklch(0.2 0 0)" }],
    "price_range": { "min": 2450000, "max": 46000000 }
  }
}
```

### 4.2 `GET /products/p1`
```json
{
  "id": "p1", "slug": "atelier-nour-belted-wool-coat",
  "brand": { "id": "atelier-nour", "name": "Atelier Nour" },
  "name": "پالتو بلند کمربنددار پشمی",
  "description": "…",
  "category": { "id": "women", "label": "پوشاک زنانه" },
  "images": [{ "url": "…", "alt": "" }],
  "colors": [{ "id": "camel", "name": "شتری", "value": "oklch(0.65 0.08 65)" }],
  "sizes": ["S", "M", "L", "XL"],
  "variants": [
    { "id": "p1-camel-s", "size": "S", "color": "camel", "available": true },
    { "id": "p1-camel-m", "size": "M", "color": "camel", "available": true }
  ],
  "price": { "original": { "amount": 8900, "currency": "TRY" }, "list_toman": 18400000,
             "discount_percent": 20, "final_toman": 14720000,
             "breakdown": null },
  "source": { "id": "trendyol", "name": "Trendyol", "country": "TR" },
  "fulfillment": "order",
  "delivery_estimate": { "min_days": 14, "max_days": 21, "unit": "working_days" },
  "available": true, "is_new": true,
  "specifications": [{ "label": "جنس", "value": "…" }],
  "size_chart": { "unit": "cm", "columns": ["سینه", "کمر"], "rows": [{ "size": "S", "values": [88, 70] }] },
  "rating": null, "in_wishlist": false
}
```
`price.breakdown` is `null` until you decide whether to show FX/fees (the current UI does not).

### 4.3 `GET /cart`
```json
{
  "id": "c_8f2a", 
  "items": [
    { "line_id": "ci_1", "product_id": "p2", "variant_id": "p2-cream-m", "name": "یقه‌اسکی بافت گیس‌باف",
      "brand": "Lumière", "image": "https://cdn.example/p2-1.jpg", "size": "M", "color": "کرم",
      "quantity": 2, "unit_price_toman": 5200000, "line_total_toman": 10400000,
      "fulfillment": "stock", "available": true, "price_changed": false }
  ],
  "totals": { "item_count": 2, "subtotal_toman": 10400000, "shipping_estimate_toman": 450000,
              "total_toman": 10850000, "currency": "IRT" },
  "warnings": []
}
```

### 4.4 `POST /checkout/quote`
Request: `{ "cart_id": "c_8f2a", "address": { "city": "تهران", "postal_code": "1234567890" } }`
```json
{ "shipping_options": [
    { "id": "standard", "label": "ارسال استاندارد", "cost_toman": 450000, "free": false },
    { "id": "express",  "label": "ارسال سریع",     "cost_toman": 950000, "free": false } ],
  "totals": { "subtotal_toman": 10400000, "shipping_toman": 450000, "total_toman": 10850000 } }
```

### 4.5 `POST /orders` → `201`
Request: `{ "cart_id": "c_8f2a", "customer": { "name": "…", "phone": "09120000000", "email": null },
"address": { "city": "…", "line": "…", "postal_code": "1234567890" }, "shipping_option": "standard" }`
```json
{ "id": "ord_01", "code": "MRL-519034", "status": "pending_payment",
  "totals": { "subtotal_toman": 10400000, "shipping_toman": 450000, "total_toman": 10850000 },
  "payment": { "status": "unpaid", "redirect_url": null } }
```

### 4.6 `GET /orders` (account tab)
```json
{ "results": [ { "code": "MRL-482910", "placed_at": "2026-10-04T09:30:00Z", "status": "delivered",
                 "status_label": "تحویل شده", "total_toman": 18400000 } ] }
```
(Frontend displays Jalali dates — server returns ISO-8601 UTC; conversion stays in the client.)

### 4.7 `POST /assistant/messages`
Request: `{ "conversation_id": null, "message": "یه کتونی زیر ۵ میلیون پیشنهاد بده.", "context_product_ids": [] }`
```json
{ "conversation_id": "conv_1",
  "reply": { "text": "این دو کتانی زیر ۵ میلیون تومان هستند…", "product_ids": ["p5", "p7"] } }
```
Frontend renders cards from product data, so the API may return full compact product objects instead of ids.

### 4.8 `GET /config/storefront`
```json
{ "currency": "IRT", "free_shipping_from_toman": 20000000,
  "shipping_options": [{ "id": "standard", "cost_toman": 450000 }, { "id": "express", "cost_toman": 950000 }],
  "languages": ["fa", "en", "ar", "tr"], "default_language": "fa" }
```

### 4.9 Error
```json
{ "error": { "code": "validation_error", "message": "…", "fields": { "phone": ["شماره موبایل معتبر نیست."] } } }
```

## 5. Cross-cutting requirements
- **CORS / CSRF**: frontend is an SSR app (TanStack Start/Nitro) with CSRF middleware on its own server functions; decision needed whether the browser calls Django directly or through the Start server (affects cookies vs tokens, CORS).
- **i18n**: send `Accept-Language`; product copy translation scope undecided.
- **Persian text search**: normalization of ی/ي, ک/ك, ZWNJ, Persian/Latin digits (frontend currently does naive `toLowerCase().includes`).
- **Idempotency** for order creation and payment start; **price re-validation** at order time.
- **Pagination** contract compatible with "show more".
- **Caching**: catalog endpoints cacheable; cart/orders private.
- **Media**: product images served from a media/CDN origin.
