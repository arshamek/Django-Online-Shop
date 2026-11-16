# API Conventions — `/api/v1`

Status: proposal. Refines the Phase 0 `API_REQUIREMENTS.md` (where they differ, **this document wins**; differences are listed in §14).
Stack: Django REST Framework + `drf-spectacular` (OpenAPI 3, Swagger UI at `/api/docs/`, schema at `/api/schema/`).

## 1. Principles
1. Every endpoint maps to a frontend requirement (Phase 0 matrix) or a documented staff operation (§12). No speculative endpoints.
2. Resource-oriented, plural nouns, JSON only (`application/json; charset=utf-8`).
3. The frontend stays visually unchanged: response shapes are designed so a thin adapter can map to the existing `Product`/`CartItem` types.
4. Server is authoritative for prices, availability, totals and statuses. The browser never sends a price.
5. Backward compatible within `v1`: additive changes only; breaking changes ⇒ `/api/v2/`.

## 2. Versioning & URLs
- URL versioning: `/api/v1/…`. Staff API: `/api/v1/staff/…`. Webhooks: `/api/v1/webhooks/{provider}/` (no auth header; signature verified).
- Trailing slash omitted; kebab-case path segments; query params `snake_case`.
- Deprecation: `Deprecation` and `Sunset` response headers + changelog; minimum 6 months overlap.

## 3. Identifiers, time, numbers
| Item | Convention |
|---|---|
| ids | `id` = `public_id` UUID string (or slug where noted). Internal bigint never exposed. Demo ids (`p1`) disappear **[D-8]**. |
| timestamps | ISO 8601 UTC with `Z` (`2026-11-17T09:30:00Z`). Jalali formatting is client-side. |
| money | object `{ "amount": "14720000", "currency": "IRT" }`. `amount` is a **decimal string** (no JS float risk). Allowed currencies: `IRT`, `IRR` (display), plus source currencies where relevant. The `Accept-Currency`/`?currency=` param selects display currency (default `IRT`). |
| percentages | integers or decimal strings, named `*_percent`. |
| booleans/enums | enums are lowercase snake_case strings; documented in OpenAPI. |
| language codes | `fa`, `en`, `ar`, `tr`. |

## 4. Request headers
| Header | Use |
|---|---|
| `Accept-Language` | content language (also `?lang=`); fallback chain `requested → fa → en → source` |
| `X-Request-ID` | optional; echoed in responses and logs |
| `Idempotency-Key` | **required** on `POST /orders`, `POST /orders/{id}/payments`, refund creation; same key + same body ⇒ same response; same key + different body ⇒ `409 idempotency_conflict` |
| `X-Guest-Token` | opaque guest-session token for anonymous cart/wishlist/AI (also delivered as HttpOnly cookie when same-site) |
| `Authorization` | only if token auth is chosen **[D-6]** |
| `If-None-Match` | ETag revalidation for catalogue GETs |

## 5. Authentication & authorisation (outline — final method **[D-6]**)
- Levels: **Public**, **Optional** (guest or user), **User**, **Staff** (role/permission based).
- Recommended: HttpOnly + Secure + SameSite=Lax session cookie, CSRF token header for unsafe methods, SMS-OTP login (`/auth/otp/request`, `/auth/otp/verify`). If the frontend is served on another registrable domain, or a mobile client is added, short-lived access token + rotating refresh token instead.
- Object-level checks: every selector for carts, orders, addresses, payments filters by owner (user or guest session); other users' ids return `404`, not `403`.
- Staff endpoints require `is_staff` + named permission (e.g. `orders.change_order_status`); 2FA required for staff **[D-6]**.
- No endpoint trusts a client-supplied user id, price, total, payment status, or role.

## 6. Success responses
- **Single resource**: the object itself (no envelope). `201` + `Location` on create, `204` on delete.
- **Collections**:
```json
{ "count": 125, "page": 1, "page_size": 24, "next": "…?page=2", "previous": null, "results": [ … ] }
```
- Optional `facets` object on `/products`. Optional `meta` for server hints (e.g. `price_changed`).
- `page_size` default 24, max 100. Frontend "show more" = increasing `page`. Deep paging uses `cursor` on staff/feeds (keyset) because `OFFSET` degrades on SQL Server.

## 7. Errors
Always the same envelope; `message` is localised, `code` is stable.
```json
{ "error": { "code": "validation_error", "message": "…", "fields": { "phone": ["…"] }, "request_id": "…" } }
```
| HTTP | `code` examples |
|---|---|
| 400 | `validation_error`, `invalid_filter`, `invalid_cursor` |
| 401 | `not_authenticated`, `otp_invalid`, `otp_expired` |
| 403 | `permission_denied`, `csrf_failed` |
| 404 | `not_found` |
| 409 | `idempotency_conflict`, `out_of_stock`, `price_changed`, `quote_expired`, `cart_changed`, `already_exists` |
| 422 | `business_rule_violation` (e.g. `variant_unavailable`, `address_not_serviceable`) |
| 429 | `rate_limited` (+ `Retry-After`) |
| 5xx | `server_error`, `upstream_unavailable` (payment/AI/provider) |
`409 price_changed` returns the new quote so the UI can re-confirm.

## 8. Filtering, sorting, search (`GET /products`)
| Param | Meaning | Frontend source |
|---|---|---|
| `category` (repeatable / comma list; includes subtree) | category slug/id | `cat` |
| `brand` (repeatable) | brand slug/id | `brand` |
| `source` (repeatable) | `trendyol`, `namshi`, `maral` (= direct inventory) | source filter |
| `sale=true` | has discount | `sale` |
| `available=true` | sellable now | "فقط کالاهای موجود" |
| `is_new=true` | within `new_until` | "تازه‌ها" |
| `size`, `color` (repeatable; option codes) | variant options | size/colour |
| `price_min`, `price_max` | in display currency, final price | slider |
| `q` | text search (Persian-normalised) | search |
| `ids` | comma list of ids (max 50) | recently viewed / wishlist hydrate |
| `sort` | `popular` (default), `new`, `price_asc`, `price_desc`, `discount` | frontend `popular/new/low/high/discount` (adapter maps `low→price_asc`, `high→price_desc`) |
| `page`, `page_size` | pagination | "show more" |
Unknown filter ⇒ `400 invalid_filter` (silently ignoring hides bugs). Facets are computed under the *other* active filters.

## 9. Rate limiting (DRF throttles on Redis)
| Scope | Suggested start (tune later) |
|---|---|
| anonymous read | 120/min per IP |
| authenticated | 300/min per user |
| search/suggest | 60/min |
| OTP request | 3 per 10 min per phone + 10/hour per IP |
| checkout/payment | 20/min |
| AI messages | 20/min + daily token/cost budget per user/session |
Headers: `RateLimit-Limit`, `RateLimit-Remaining`, `Retry-After`.

## 10. Caching & performance
`GET /products`, `/categories`, `/brands`, `/config/storefront`: `Cache-Control: public, max-age=60, stale-while-revalidate=300` + `ETag`; `Vary: Accept-Language, Accept-Currency`. Cart/orders/me/payments: `Cache-Control: private, no-store`. Invalidate catalogue cache on `price_changed`/`product_imported` events.

## 11. Endpoint catalogue (customer-facing)
| Area | Method & path | Auth | Frontend requirement |
|---|---|---|---|
| Config | `GET /config/storefront` | Public | free-shipping banner, languages, currencies |
| Catalogue | `GET /categories` | Public | nav, tiles, filters |
| | `GET /brands` | Public | brand strip, filters |
| | `GET /products` | Public | home sections, shop list, search overlay, related (`?related_to=`), hydrate (`?ids=`) |
| | `GET /products/{id}` (id or slug) | Public | product page, quick view |
| | `GET /products/{id}/reviews` | Public | reviews tab **[Q-reviews]** |
| | `POST /products/{id}/reviews` | User | (not in frontend yet) |
| Auth | `POST /auth/otp/request`, `POST /auth/otp/verify`, `POST /auth/logout`, `GET /auth/csrf` | Public/User | (login screen missing) **[D-6]** |
| Me | `GET/PATCH /me` | User | account profile, language |
| | `GET/POST /me/addresses`, `PATCH/DELETE /me/addresses/{id}` | User | addresses, checkout |
| Wishlist | `GET /wishlist`; `PUT/DELETE /wishlist/items/{product_id}` | Optional | heart toggle, wishlist page |
| Cart | `GET /cart`; `POST /cart/items`; `PATCH/DELETE /cart/items/{line_id}`; `POST /cart/merge` | Optional | drawer, add/qty/remove, guest→user |
| Checkout | `POST /checkout/quote` | Optional | shipping options + locked totals (creates `PriceQuote`) |
| Orders | `POST /orders` (Idempotency-Key) | Optional **[Q-guest]** | place order |
| | `GET /orders`, `GET /orders/{id}` | User (or guest token) | order list, tracking |
| | `POST /orders/{id}/cancel` | User | (future) |
| Payments | `POST /orders/{id}/payments` (Idempotency-Key) | Optional | start payment → redirect |
| | `GET /payments/{id}` | Optional | result page polling (**never** trust browser params) |
| Webhooks | `POST /webhooks/payments/{provider}` | Signature | gateway callback |
| Newsletter | `POST /newsletter/subscriptions` | Public | home form |
| AI | `POST /assistant/messages`; `GET /assistant/conversations/{id}` | Optional | chat |

## 12. Staff / administrative operations (not in storefront)
All under `/api/v1/staff/…`, permission-gated and audited.
| Operation | Endpoint (sketch) |
|---|---|
| Sources | `GET/PATCH /staff/sources` |
| Imports | `POST /staff/import-jobs`, `GET /staff/import-jobs/{id}` (+ items, logs), `POST …/{id}/cancel` |
| Matching | `GET /staff/match-candidates`, `POST …/{id}/approve|reject`, `POST /staff/products/{id}/merge`, `…/unlink` |
| Catalogue | CRUD products/variants/images/translations; translation review queue |
| Currency | `GET/POST /staff/currency-rates` (manual entry) |
| Pricing | rule sets: create draft, `POST …/{id}/publish`, `GET /staff/pricing/preview` |
| Inventory | adjustments, receipts, reservations view |
| Orders | queues, status transitions, procurement updates |
| Payments | reconcile, `POST /staff/refunds` (+ approve) |
| Shipping | shipments, tracking events |
| Reviews | moderation queue |
| Settings & roles | `GET/PATCH /staff/settings`, roles/permissions |
| Audit | `GET /staff/audit-logs` |
(Django Admin may cover simple CRUD in early phases; the API above is the contract for a future staff UI.)

## 13. Representative shapes (money as objects)
**Product list item**
```json
{ "id": "6f1c…", "slug": "atelier-nour-belted-wool-coat",
  "name": "پالتو بلند کمربنددار پشمی", "brand": { "id": "…", "name": "Atelier Nour" },
  "category": { "id": "…", "slug": "women" },
  "images": [{ "url": "https://cdn…/1.jpg", "alt": "" }],
  "price": { "final": {"amount":"14720000","currency":"IRT"}, "list": {"amount":"18400000","currency":"IRT"}, "discount_percent": 20 },
  "source": { "code": "trendyol", "name": "Trendyol" },
  "fulfillment": "order", "available": true, "is_new": true, "in_wishlist": false }
```
`fulfillment` (`stock|order`) and `available` are **derived** values for the existing UI **[Q-stock]**; `source` = preferred listing's source or `maral`.
**Cart**
```json
{ "id": "…", "items": [ { "line_id": "…", "variant_id": "…", "product": {…compact…}, "size": "M", "color": {"code":"cream","name":"کرم"},
  "quantity": 2, "unit_price": {"amount":"5200000","currency":"IRT"}, "line_total": {"amount":"10400000","currency":"IRT"},
  "fulfillment": "stock", "available": true, "price_changed": false } ],
  "totals": { "subtotal": {…}, "shipping_estimate": {…}, "total": {…} }, "warnings": [] }
```
**Quote** returns `quote_id`, `expires_at`, `shipping_options[]`, per-line locked prices, totals; `POST /orders` takes `quote_id` — never prices.

## 14. Differences from Phase 0 proposal
| Phase 0 | Now |
|---|---|
| `final_toman: 14720000` (int) | money object with decimal string + currency |
| `/products/{id}/related` | `GET /products?related_to={id}` (one fewer resource) |
| `POST /assistant/messages` only | unchanged; plus `GET /assistant/conversations/{id}` |
| cart/product ids `p1`/`c_8f2a` | UUID `public_id` |
| sort `low/high` | `price_asc/price_desc` (adapter maps) |
| payment `POST /orders/{id}/payments` | unchanged, now with mandatory idempotency + server verification only |

## 15. OpenAPI / documentation duties
- Generated with `drf-spectacular`; CI fails on schema warnings and on **undocumented** breaking diffs (`oasdiff`-style check).
- Every serializer field has description/example; error envelope and auth schemes declared; staff endpoints tagged `staff` and hidden from the public schema in production.
- Contract tests: recorded fixtures from the 16 demo products must validate against the schema.
