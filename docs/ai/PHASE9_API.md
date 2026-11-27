# Phase 9 – API contracts

Base: `/api/v1`. A trailing slash is optional (`/ai/chat` and `/ai/chat/` are the same route). Authentication is the
project's managed session cookie; **every assistant endpoint requires a logged-in customer** (401/403 otherwise).
Responses are `Cache-Control: private, no-store`. Errors use the project envelope `{"error": {"code", "message", ...}}`.

Money: customer-facing amounts are **whole Toman as strings** (`"currency": "IRT"`). Marketplace amounts keep their own
currency (`TRY`, `AED`, ...). Budgets in requests are whole Toman integers.

## Customer endpoints

| Method | Path | Throttle scope | Purpose |
|---|---|---|---|
| POST | `/ai/chat` | `ai_chat` (20/min) | One assistant turn in a conversation |
| GET | `/ai/conversations` | `ai_read` | My conversations (latest 50) |
| GET / DELETE | `/ai/conversations/{id}` | `ai_read` | One conversation with its messages / delete it permanently |
| POST | `/ai/recommendations` | `ai_chat` | Stateless search + ranking (nothing stored) |
| POST | `/ai/compare` | `ai_chat` | Compare 2–4 products |
| POST / GET | `/ai/purchase-inquiries` | `ai_inquiry` (10/hour) / `ai_read` | Create / list my purchase inquiries |
| GET / DELETE | `/ai/purchase-inquiries/{id}` | `ai_read` | One inquiry / cancel it |

Status codes: `200`, `201` (inquiry), `204` (conversation deleted), `400` validation, `404` not yours / not found,
`409` duplicate inquiry, `422` business rule (`code` says which), `429` (`ai_usage_limit`, `ai_conversation_limit`,
`inquiry_limit` or throttle), `503` `ai_disabled`.

### POST /ai/chat

Request:
```json
{
  "message": "یه کتونی نایک برای پیاده‌روی می‌خوام، زیر ۵ میلیون تومان.",
  "language": "auto",                 // optional: fa | en | ar | tr | auto. A value other than auto forces the reply language
  "conversation_id": "uuid",          // optional: omit to start a new conversation
  "budget": {"min": 0, "max": 5000000},                       // optional, whole Toman; overrides the text
  "filters": {"colors": ["black"], "sizes": ["42"], "min_price": 0, "max_price": 5000000},   // optional
  "preferred_sources": ["maral", "trendyol", "namshi"],       // optional
  "product_references": [{"source": "trendyol", "id": "uuid"}] // optional: "this product" for compare/availability/select
}
```
`message` ≤ 4000 characters (the staff config trims further, default 1000).

Response (real output, trimmed; `…` marks omitted repeated objects):
```json
{
  "conversation_id": "f3f17d8d-8265-4bf9-b15a-ad5304547413",
  "message_id": 2,
  "language": "fa",
  "intent": "search",
  "assistant_message": "۲ مورد مرتبط پیدا کردم:\n۱. کتونی نایک ایر زوم پیاده‌روی — مارال، ۴٬۰۰۰٬۰۰۰ تومان (مطابق با: برند، دسته‌بندی، کاربری، بودجه)\n۲. Nike Air Zoom Running Sneaker — ترندیول، ۲٬۴۷۵٬۰۰۰ تومان (مطابق با: برند، دسته‌بندی، بودجه)\nنتایج ترندیول از اطلاعات ذخیره‌شده‌ی قبلی است، نه جستجوی زنده، و موجودی آن‌ها هنوز تأیید نشده است. جستجوی ترندیول: نیازمند مجوز رسمی است. جستجوی نمشی: نیازمند مجوز رسمی است. کدام‌یک را ترجیح می‌دهید؟",
  "products": [
    {
      "ref": {"source": "maral", "id": "a9a8a745-…"},
      "kind": "internal",
      "source": {"code": "maral", "name": "Maral", "label": "maral"},
      "title": "کتونی نایک ایر زوم پیاده‌روی", "brand": "Nike",
      "prices": {
        "original": {"amount": "100", "currency": "TRY", "observed_at": null},
        "estimated_maral": {"amount": "4000000", "currency": "IRT", "status": "listed", "provisional": false,
                            "missing_costs": [], "blockers": [], "warnings": [], "rule_version": null},
        "confirmed": null
      },
      "availability": {"status": "IN_STOCK", "last_known": null, "confidence": "high", "verified_at": "2026-11-26T21:16:08Z", "basis": "maral_inventory"},
      "sizes": [{"code": "42", "name": "42", "available": true}],
      "colors": [{"code": "black", "name": "black", "available": true}],
      "snapshot": null,
      "maral": {"slug": "product-1", "detail_api_path": "/api/v1/products/product-1", "purchasable_in_cart": true},
      "match": {"matched": ["brand", "category", "use:walking", "budget"], "unmet": [], "score": 10.0},
      "warnings": []
    },
    {
      "ref": {"source": "trendyol", "id": "0819ea0b-…"},
      "kind": "imported_snapshot",
      "source": {"code": "trendyol", "name": "Trendyol", "label": "trendyol"},
      "title": "Nike Air Zoom Running Sneaker", "brand": "Nike",
      "prices": {
        "original": {"amount": "1000", "currency": "TRY", "list_amount": "1000", "observed_at": "2026-11-26T21:16:08Z"},
        "estimated_maral": {"amount": "2475000", "currency": "IRT", "status": "estimated", "provisional": false,
                            "missing_costs": [], "blockers": [], "warnings": ["no_rounding_rule"],
                            "rule_version": 1, "calculated_at": "2026-11-26T21:16:08Z", "fx": {"base": "TRY", "quote": "IRR", "rate": "15000.0000000000", "…": "…"}},
        "confirmed": null
      },
      "availability": {"status": "NOT_VERIFIED", "last_known": "in_stock", "confidence": "medium", "verified_at": null,
                       "basis": "stored_snapshot_not_live", "observed_at": "2026-11-26T21:16:08Z", "note": "not_verified_with_the_marketplace"},
      "sizes": ["42"], "colors": ["black"],
      "snapshot": {"is_snapshot": true, "live_marketplace_result": false, "data_origin": "live", "price_observed_at": "2026-11-26T21:16:08Z", "price_age_hours": 0.0, "stale": false},
      "maral": {"slug": null, "detail_api_path": null, "purchasable_in_cart": false},
      "images": [], "images_note": "image_urls_withheld_source_terms", "product_url": null,
      "match": {"matched": ["brand", "category", "budget"], "unmet": [], "score": 6.0},
      "warnings": []
    }
  ],
  "applied_filters": {"category_concept": "sneakers", "brands": ["Nike"], "keywords": [], "intended_use": ["walking"],
                      "colors": [], "sizes": [], "budget": {"min": null, "max": "5000000", "currency": "IRT"},
                      "sort": "relevance", "sources": ["maral", "trendyol", "namshi"]},
  "relaxed_filters": [],
  "provider_status": [
    {"source": "maral", "status": "AVAILABLE", "mode": "internal_catalog", "searched": true, "result_count": 1, "live_search": "n/a", "live_search_status": null, "snapshot_search_status": null},
    {"source": "trendyol", "status": "AUTHORIZATION_REQUIRED", "mode": "imported_snapshot", "searched": true, "result_count": 1,
     "live_search": "disabled", "live_search_status": "AUTHORIZATION_REQUIRED", "snapshot_search_status": "AVAILABLE", "detail": "Stored import snapshots were searched (not a live marketplace search). …"},
    {"source": "namshi", "status": "AUTHORIZATION_REQUIRED", "mode": "imported_snapshot", "searched": true, "result_count": 0, "…": "…"}
  ],
  "clarification": null,
  "assumptions": [],
  "comparison": null, "selection": null, "availability_check": null,
  "prompt_injection_suspected": false,
  "demo_mode": true,
  "provider": {"name": "mock", "model": "deterministic-template", "mode": "demo", "configured": true, "verified": false, "note": "demo_mode_deterministic_assistant"},
  "ai_fallback": false
}
```

#### Field meanings
* `intent`: `search`, `compare`, `availability`, `select`, `greeting`, `clarify` (the assistant needs more information).
* `products[].kind`: `internal` (Maral catalogue) or `imported_snapshot` (stored Trendyol/Namshi data). The source is always in
  `source.code`.
* `prices.estimated_maral.status`: `listed` (Maral's published price), `estimated` (Phase 6 engine, all configured costs known),
  `provisional` (shown amount excludes unknown costs / source price is stale – read `missing_costs`, `warnings`),
  `incomplete` / `unavailable` (**no `amount`**; read `blockers`). `confirmed` is always `null` here.
* `availability.status`: `IN_STOCK`, `OUT_OF_STOCK`, `UNKNOWN`, `NOT_VERIFIED`. Snapshots are never `IN_STOCK`.
* `provider_status[].status` is the source's *live* capability (`AVAILABLE`, `NOT_CONFIGURED`, `AUTHORIZATION_REQUIRED`,
  `TEMPORARILY_UNAVAILABLE`, `RATE_LIMITED`, `UNSUPPORTED`, `ERROR`). For Trendyol/Namshi it is currently
  `AUTHORIZATION_REQUIRED`; `snapshot_search_status` says whether the **stored-snapshot** search worked. `searched: true` means a
  real search over stored data succeeded, never that the marketplace was queried.
* `relaxed_filters`: criteria (`size`, `color`) that nothing matched and that were relaxed; products then carry `match.unmet`.
* `assumptions`: e.g. `toman_assumed_from_magnitude`, `colloquial_toman_means_million`, `approximate_budget_plus_minus_20_percent`.
* `demo_mode`: `true` when the deterministic assistant answered. `provider.verified` is true only after a successful staff provider test.
* `ai_fallback`: the model failed or its text was rejected; the (equally factual) template text was used.

#### Clarification (no search is run)
```json
{"intent": "clarify", "products": [],
 "assistant_message": "بودجه‌تان به تومان است یا ریال؟ لطفاً مبلغ را با واحد بنویسید، مثلاً «۵ میلیون تومان».",
 "clarification": {"reason": "currency_unclear", "question": "…", "options": ["toman", "rial"]}}
```
`reason`: `currency_unclear`, `foreign_currency` (options `["toman"]`), `need_product_description`. The next message in the same
conversation completes the request («۵ میلیون تومان»); the earlier terms are kept.

#### Follow-ups (same `conversation_id`)
«ارزون‌ترشو پیدا کن» (`applied_filters.sort = "price_asc"`) · «مدل مشکی رو نشون بده» · «فقط ترندیول» ·
«نمشی هم بگرد» · «این‌ها رو مقایسه کن» → `comparison` · «سایز ۴۲ موجوده؟» → `availability_check` · «اولی رو می‌خوام» → `selection`.

`comparison`:
```json
{"products": [{"ref": {"source": "trendyol", "id": "…"}, "title": "…", "source": "trendyol"}],
 "rows": [{"attribute": "estimated_price", "values": [{"amount": "2475000", "currency": "IRT"}, null]}, {"attribute": "availability", "values": [{"status": "NOT_VERIFIED", "last_known": "in_stock", "confidence": "medium"}, …]}],
 "cheapest_estimated": {"source": "trendyol", "id": "…"},
 "null_means": "not provided by the source; it is not guessed"}
```
Rows: `source, brand, category, estimated_price, price_status, original_price, availability, sizes, colors, snapshot_age`.

`availability_check`: `{"sizes": ["42"], "lines": ["…per product sentence…"]}`. A size that is not among the stored variants is
reported as "no information", never as available.

`selection` (product chosen; **nothing is ordered**):
```json
{"type": "internal_product", "product_ref": {"source": "maral", "id": "…"}, "next_step": "add_to_cart", "cart_endpoint": "/api/v1/cart/items",
 "variants": [{"variant_id": "uuid", "size": "42", "color": "black", "availability": "IN_STOCK"}], "order_created": false}
{"type": "purchase_inquiry", "product_ref": {"source": "trendyol", "id": "…"}, "next_step": "create_purchase_inquiry",
 "inquiry_endpoint": "/api/v1/ai/purchase-inquiries", "options": {"sizes": ["42"], "colors": ["black"]},
 "required": ["product_ref", "quantity", "size", "color"], "order_created": false, "payment_created": false}
```

### GET /ai/conversations → `{"results": [{"id", "title", "language", "created_at", "updated_at", "message_count"}]}`
### GET /ai/conversations/{id} → the same fields + `"messages": [{"id", "role": "user|assistant", "content", "language", "data", "demo_mode", "created_at"}]`
### DELETE /ai/conversations/{id} → `204`; messages are deleted with it.

### POST /ai/recommendations
`{"query": "کتونی نایک زیر ۵ میلیون تومان", "language": "fa", "budget": {...}, "filters": {...}, "preferred_sources": [...]}` →
`{"language", "products", "provider_status", "applied_filters", "relaxed_filters", "clarification", "assumptions", "demo_mode"}`
(no conversation is stored, no model is called). `query` is required.

### POST /ai/compare
`{"product_references": [{"source": "maral", "id": "…"}, {"source": "trendyol", "id": "…"}]}` (2–4) →
`{"language", "products", "comparison", "demo_mode"}`. Products are re-read from the database; fewer than two resolvable products → `422 compare_needs_two`.

### POST /ai/purchase-inquiries  (a request for staff, **not an order**)
```json
{"product_ref": {"source": "trendyol", "id": "uuid"}, "size": "42", "color": "black", "quantity": 1, "note": "optional ≤500", "conversation_id": "uuid (optional)"}
```
`201`:
```json
{"id": "uuid", "source_code": "trendyol", "title": "Nike Air Zoom Running Sneaker", "brand": "Nike", "size": "42", "color": "black", "quantity": 1,
 "customer_note": "", "status": "new",
 "prices": {"original": {"amount": "1000", "currency": "TRY"}, "estimated_maral": {"amount": "2475000", "currency": "IRT", "status": "estimated", "rule_version": 1}, "confirmed": null},
 "snapshot_observed_at": "…", "availability_seen": "in_stock", "created_at": "…", "updated_at": "…", "is_order": false}
```
Rules: only Trendyol/Namshi products from active, enabled sources (fixture rows excluded unless staff allow); `size`/`color` are
required when the listing has them and must be among the stored variants (`422 size_not_offered`, `color_not_offered`,
`size_required`, `color_required`); `quantity` 1–10; one open inquiry per product/size/colour (`409 inquiry_duplicate`); ≤ 20 open
inquiries (`429 inquiry_limit`). Statuses: `new`, `under_review`, `feasible`, `not_feasible`, `closed`, `cancelled`.
`DELETE` cancels while `new`/`under_review` (`422 inquiry_not_cancellable` otherwise).

## Staff endpoints (permission `accounts.ai_manage`: Super Admin, Product Manager)

| Method | Path | Purpose |
|---|---|---|
| GET / PATCH | `/staff/ai/config` | Provider (`mock`/`anthropic`), model, enabled flag, supported languages, enabled sources, extra instructions, `daily_message_limit`, `max_input_chars`, `context_messages`, `retention_days`, `max_results`, `price_max_age_hours`, `provider_timeout_seconds`, `allow_fixture_snapshots`, `expose_marketplace_urls` (image URLs follow the source's `allows_image_copy`; there is no override) |
| GET | `/staff/ai/status` | Provider (mode, configured, verified, last test), per-source status (`live_search_status`, stored/fixture listing counts, `live_verified: false`), usage and error statistics (24 h / 7 d, by status and error code, tokens, injection flags), open inquiries |
| POST | `/staff/ai/provider-test` | One real call to the configured provider; records `last_provider_test_*`. In demo mode it tests nothing and says so. |
| GET | `/staff/ai/purchase-inquiries?status=` | Inquiries incl. `source_url`, `external_id`, `customer_id`, check stamps |
| GET / PATCH | `/staff/ai/purchase-inquiries/{id}` | PATCH `{"status", "price_checked", "availability_checked", "feasibility_checked", "staff_notes"}` |

Inquiry workflow: `new → under_review → feasible | not_feasible → closed`. `feasible` requires all three check stamps
(`422 inquiry_recheck_required`). `not_feasible`, `closed`, `cancelled` are terminal. The API key is not part of the config; it
is read from `AI_API_KEY`.
