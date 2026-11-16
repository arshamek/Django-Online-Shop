# Database Design — SQL Server via `mssql-django`

Companion to `ERD.md`. Types are Django field types with the SQL Server mapping in brackets where it matters. "UK" = unique. Every model inherits `TimestampedModel` (`created_at`, `updated_at`, UTC, `datetimeoffset`/`datetime2`) and, if publicly addressable, `public_id` (UUID, **non-clustered** unique index; clustered key stays the `bigint` identity).

## 1. Global conventions
| Topic | Rule |
|---|---|
| PK | `BigAutoField`, clustered. UUIDs are never the clustered key (page splits). |
| Public ids | `public_id UUID` exposed as API `id`; slugs for SEO routes. Frontend demo ids (`p1`…) are replaced by `public_id` (or slug) **[D-8]**. |
| Strings | `CharField/TextField` → `nvarchar` (Unicode). Persian/Arabic safe. Collation: case-insensitive, accent-sensitive, UTF-8-capable or `_100_` collation **[D-4]**; normalise ی/ي, ک/ك, ZWNJ, digits in app code **before** comparing. |
| Money | `DecimalField(max_digits=18, decimal_places=4)` for foreign-currency amounts; **IRR amounts `DecimalField(18, 0)`**; rates `DecimalField(24, 10)`; percentages `(7, 4)`. Never `FloatField`. Every amount has a currency code (column or fixed by `_irr` suffix). `CHECK (amount >= 0)` where applicable. |
| Enums | `CharField` + `choices` + `CHECK (col IN (...))` generated through `CheckConstraint` (no DB enum type in SQL Server). Lookup tables only when staff must edit values. |
| JSON | `JSONField` is stored as `nvarchar(max)` + `ISJSON` check; **no native `json` type via ODBC, `contains` lookups unsupported, nested lookups differ**. Use JSON only for raw/opaque payloads (`raw_attributes`, `signals`, `changes`, redacted provider payloads). Anything filtered/sorted/indexed is a real column. |
| Booleans | `BooleanField` → `bit`. |
| Time | UTC everywhere, `USE_TZ=True`; time-zone/`timedelta` support in the backend is limited → store durations as integer seconds/days, convert in Python. Display in Asia/Tehran at the client. |
| Soft delete | Only: `Product.status=archived`, `ProductVariant.is_active`, `Brand/Category.is_active`, `User.is_active` (anonymise on erasure). Everything else is hard-deleted or immutable. No generic soft-delete mixin. |
| Immutability | Append-only: `PriceHistory`, `InventoryMovement`, `OrderStatusHistory`, `PaymentTransaction`, `ImportLog`, `AdminAuditLog`, `ShipmentTrackingEvent`, `AIMessage`. App-level guard (`save()`/`delete()` raise) + DB permissions for the app role (no UPDATE/DELETE) **[D-5]**; **no triggers** (they break `bulk_create` ID retrieval). |
| Nullable unique | SQL Server treats NULL as a value in `UNIQUE` (only one NULL). Every nullable-unique column uses a **filtered unique index** (`WHERE col IS NOT NULL`). `mssql-django` is expected to do this for `unique=True, null=True`; **verify in spike T3**, else declare `UniqueConstraint(fields, condition=Q(col__isnull=False))`. |

## 2. Tables by app
Columns shown = business-relevant ones. Omitted everywhere: timestamps, `public_id`.

### 2.1 accounts
| Table | Columns / rules |
|---|---|
| `User` (custom, `AUTH_USER_MODEL`) | `phone_e164` (UK filtered), `email` (UK filtered, case-insensitive), `password` (unusable allowed for OTP-only), `user_type` {customer, staff}, `is_active`, `is_staff`, `is_superuser`, `phone_verified_at`, `email_verified_at`, `date_joined`, `last_login`. `CHECK (phone_e164 IS NOT NULL OR email IS NOT NULL)`. `USERNAME_FIELD` decision **[D-6]**. |
| `CustomerProfile` | 1:1 User. `first_name`, `last_name`, `birth_date` (nullable; frontend shows day/month only), `marketing_consent`, `loyalty_tier_id` ➕ nullable. National ID **not stored** unless customs requires it **[Q-customs]** (then encrypted column). |
| `Address` | `user_id`, `label`, `recipient_name`, `recipient_phone`, `country_code`, `province`, `city`, `line1`, `line2`, `postal_code`, `latitude/longitude` (nullable), `is_default`. UK filtered `(user_id) WHERE is_default=1`. Validation per country (Iran: 10-digit postal code). Orders **copy** the address (columns on `Order`), so edits/deletes never alter history. |
| `UserPreference` | 1:1. `language` {fa,en,ar,tr}, `display_currency` {IRT,IRR,…}, notification channel flags. |
| `CustomerSession` | guest identity: `token_hash` (UK; the raw token lives in a cookie/header only), `user_id` nullable (set at login), `language`, `display_currency`, `ip_hash`, `user_agent`, `first_seen_at`, `last_seen_at`, `merged_at`. Purge after N days idle. |
| `OTPChallenge` ➕ | `identifier`, `code_hash`, `purpose`, `expires_at`, `attempts`, `consumed_at`, `ip_hash`. |
| `LoyaltyTier` ➕ | `code`, `name`, thresholds (rules unknown **[Q-loyalty]**). |

### 2.2 brands / categories
| Table | Columns / rules |
|---|---|
| `Brand` | `slug` UK, `name`, `logo_key`, `is_active`. |
| `BrandTranslation` | `(brand_id, language)` UK, `name_local`. |
| `BrandAlias` ➕ | `brand_id`, `alias_normalised` UK. Feeds matching. |
| `Category` | `parent_id` (self FK, NO ACTION), `slug` UK, `path` (materialised, e.g. `/1/7/`), `depth`, `sort_order`, `is_active`, `image_key`. Tree via adjacency + path (SQL Server `hierarchyid` is not available through Django). Frontend today: 6 flat categories. |
| `CategoryTranslation` | `(category_id, language)` UK: `name`, `description`. |
| `ExternalCategoryMapping` ➕ | `(source_id, external_category_path)` UK → `category_id`. |

### 2.3 catalog
| Table | Columns / rules |
|---|---|
| `Product` | `slug` UK, `brand_id` (PROTECT), `category_id` (PROTECT), `status`, `source_language`, `gender`/`audience` (nullable), `new_until` (datetime → drives "جدید" badge), `manufacturer_model_code` (MPN, nullable), `search_hint`. |
| `ProductVariant` | `product_id` (CASCADE→Django-level), `sku` UK, `option_signature` (UK with `product_id`), `fulfillment_policy` {DIRECT_ONLY, PROXY_ONLY, DIRECT_THEN_PROXY}, `preferred_external_variant_id` nullable, `weight_grams`, `is_active`. `fulfillment_type` is *not* stored; it is resolved per order item at checkout. |
| `ProductImage` | `product_id`, `variant_id` nullable, `storage_key`, `alt` (translation optional), `sort_order`, `width/height`, `checksum`, `source_external_product_id` nullable. |
| `ProductAttribute` | `code` UK, `value_type`, `is_variant_axis` (size, colour, volume are axes). |
| `AttributeOption` | `(attribute_id, code)` UK, `swatch` (colour value, e.g. frontend `oklch()` token), `sort_order`. |
| `AttributeOptionTranslation` ➕ | `(option_id, language)` UK `label` (e.g. «مشکی»/Black). |
| `ProductAttributeValue` | `product_id` or `variant_id`, `attribute_id`, `value_text/number/bool` or `option_id`. Specs shown in the "مشخصات" tab. |
| `ProductVariantOption` | `(variant_id, attribute_id)` UK → `option_id`. |
| `ProductTranslation` | `(product_id, language)` UK, `name`, `short_description`, `description`, `seo_title`, `seo_description`, `search_text` (normalised), `status`, `origin` {source, machine, human}, `source_text_hash`, `provider`, `model`, `reviewed_by_id`. |
| `SizeChart` ➕ | `category_id`/`brand_id` scope, `unit`, rows as `SizeChartRow` (`size_label`, `chest_cm`, `waist_cm`…). Replaces the one generic table. |
| `ProductListingProjection` ➕ | 1:1 Product (`product_id` PK). `min_final_irr`, `max_final_irr`, `max_discount_percent`, `is_available`, `fulfillment_summary` {stock, order, mixed}, `has_stock_variant`, `source_codes` (bitmask or child table), `popularity_score`, `refreshed_at`. **Written only** by `catalog.services.refresh_projection`; rebuildable from source tables. |

### 2.4 product_sources
| Table | Columns / rules |
|---|---|
| `ProductSource` | `code` UK, `name`, `country_code`, `base_currency`, `integration_type`, `is_active`, `rate_limit_per_min`, `credential_ref`, `terms_note` (licence/permission record). |
| `ExternalProduct` | `(source_id, external_id)` UK; `external_url`; `raw_title`, `raw_brand`, `raw_category_path`, `raw_description`, `raw_attributes` (JSON), `payload_hash`; `canonical_product_id` nullable (PROTECT); `match_status`, `match_confidence`, `matched_by`, `matched_at`; `status` {active, delisted, error}; `first_seen_at`, `last_seen_at`. |
| `ExternalProductVariant` | `(external_product_id, external_variant_id)` UK; `sku`, `gtin` (indexed), `size_raw`, `color_raw`; `canonical_variant_id` nullable; `current_price_id`, `current_availability_id` (nullable pointers). |
| `SourcePrice` | append-only: `external_variant_id`, `list_amount`, `sale_amount`, `currency_code`, `observed_at`. Insert only when value changed (hash). |
| `SourceAvailability` | append-only: `external_variant_id`, `status`, `quantity_hint`, `observed_at`. |
| `ExternalBrandMapping` / `ExternalSizeMapping` ➕ | map raw vocab → `Brand` / `AttributeOption`. |
| `ProductMatchCandidate` ➕ | `(external_product_id, product_id)` UK; `score`, `signals` (JSON), `status`, `reviewed_by_id`, `reviewed_at`. |
| `ProductMergeLog` ➕ | `from_product_id`, `into_product_id`, `performed_by_id`, `mapping_before` (JSON), `reverted_at`. |
Why pointer columns instead of "current" flags: avoids a filtered unique index on a flag and makes "latest price" an O(1) join.

### 2.5 imports
| Table | Columns / rules |
|---|---|
| `ImportJob` | `source_id`, `job_type` {full, incremental, single, repair}, `status`, `params` (JSON), `idempotency_key` UK (filtered), `requested_by_id`, `scheduled_for`, `started_at`, `finished_at`, counters (`n_seen`, `n_created`, `n_updated`, `n_unchanged`, `n_failed`), `cancel_requested`, `celery_task_id`, `attempt`. |
| `ImportJobItem` | `(job_id, external_id)` UK, `action`, `status`, `error_code`, `payload_hash`, `external_product_id` nullable. |
| `ImportLog` | `job_id`, `item_id` nullable, `level`, `message`, `context` (JSON). Retention job (e.g. 90 days). |

### 2.6 translations
| Table | Columns / rules |
|---|---|
| `TranslationJob` ➕ | `entity_type`, `entity_id`, `source_language`, `target_language`, `source_text_hash`, `status`, `provider`, `model`, `tokens_in/out`, `cost_amount`, `cost_currency`, `attempt`, `last_error`. UK `(entity_type, entity_id, target_language, source_text_hash)` → idempotent. |
Translations themselves live in `*Translation` tables of their owners (type-safe FKs, no generic relation).

### 2.7 currencies / pricing
| Table | Columns / rules |
|---|---|
| `Currency` | `code` PK (ISO 4217 + `IRT`), `name`, `decimal_places`, `base_currency` nullable (IRT→IRR), `base_factor` (10), `is_source`, `is_display`, `is_active`. |
| `CurrencyRate` | `base_code`, `quote_code`, `rate`, `provider`, `is_manual`, `rate_at`, `created_by_id`. Append-only; "current" = latest `rate_at` ≤ now per pair (covered by index). `CHECK (rate > 0)`. |
| `PricingRuleSet` ➕ | `version` UK, `status`, `effective_from`, `published_by_id`, `notes`. Published sets immutable. |
| `PricingRule` | `rule_set_id`, `scope` {global, source, brand, category, product}, `scope_id`, `priority`, `component_kind` {source_shipping, duty, service_fee, payment_fee, markup, discount, rounding}, `percent`, `fixed_irr`, `basis`, `min_irr`, `max_irr`, `rounding_step_irr`, `active_from/to`. |
| `VariantPrice` ➕ | 1:1 variant. `list_irr`, `final_irr`, `discount_percent`, `source_price_id`, `rate_id`, `rule_set_id`, `computed_at`. |
| `PriceHistory` | append-only: `variant_id`, `old_final_irr`, `new_final_irr`, `cause`, `source_price_id`, `rate_id`, `rule_set_id`, `created_at`. |
| `PriceQuote` | `owner_type` {cart, order}, `owner_id`, `user_id`/`session_id`, `display_currency`, `status` {open, locked, expired}, `expires_at`, `rule_set_id`, `subtotal_irr`, `shipping_irr`, `discount_irr`, `total_irr`. Locked quotes immutable. |
| `PriceQuoteItem` | see ERD. All numeric snapshot columns `NOT NULL`. `CHECK (final_line_irr = final_unit_irr * quantity)`. |
| `PriceQuoteComponent` | `item_id`, `kind`, `basis`, `percent`, `amount_irr`, `rule_id` nullable. |

### 2.8 inventory
| Table | Columns / rules |
|---|---|
| `Warehouse` | `code` UK, `name`, `country_code`, `city`, `is_active`. |
| `InventoryItem` | `(variant_id, warehouse_id)` UK; `on_hand`, `reserved`, `safety_stock`; `CHECK (on_hand >= 0 AND reserved >= 0 AND reserved <= on_hand)`. Reservation = single atomic `UPDATE … SET reserved = reserved + :q WHERE on_hand - reserved - safety_stock >= :q` (rows affected = 0 → insufficient). |
| `InventoryMovement` | append-only: `inventory_item_id`, `movement_type` {receipt, reserve, release, commit, adjust, return}, `quantity_delta`, `reference_type`, `reference_id`, `reason`, `actor_id`. UK `(reference_type, reference_id, movement_type, inventory_item_id)` for idempotent order events. |
| `InventoryReservation` ➕ | `inventory_item_id`, `holder_type`, `holder_id`, `quantity`, `expires_at`, `status` {active, committed, released, expired}. Expiry job releases stock. |

### 2.9 cart / wishlist
| Table | Columns / rules |
|---|---|
| `Cart` | `user_id` or `session_id` (`CHECK` exactly one), `status`, `expires_at`, `merged_into_id`. UK filtered: one `active` cart per user and per session. |
| `CartItem` | `public_id` (= API `line_id`), `(cart_id, variant_id)` UK, `quantity` `CHECK (>0)`. **No stored price** — priced live from `VariantPrice`; locked only in a `PriceQuote`. |
| `Wishlist` | `user_id` or `session_id`; UK filtered like cart. |
| `WishlistItem` | `(wishlist_id, product_id)` UK. Guest → user merge on login (`merged_at`). |

### 2.10 orders
| Table | Columns / rules |
|---|---|
| `Order` | `code` UK (`MRL-######`, widens past 6 digits; generated from a locked counter row, not random), `user_id` nullable only if guest checkout allowed **[Q-guest]**, `price_quote_id` (UK, PROTECT), customer snapshot (`customer_name/phone/email`), shipping address snapshot columns, `shipping_method_code`, `shipping_label`, money totals (IRR), `display_currency`, `status`, `payment_status`, `placed_at`, `idempotency_key` (UK filtered with user/session), `cancelled_at/reason`. |
| `OrderItem` | `order_id`, `variant_id` (PROTECT), `price_quote_item_id` (PROTECT), snapshot columns (name, brand, sku, size, colour, image_key), `fulfillment_type`, `source_id`/`external_variant_id` nullable, `quantity`, `unit_price_irr`, `line_total_irr`, `status`. |
| `OrderStatusHistory` | append-only; `order_id`, `order_item_id` nullable, `from_status`, `to_status`, `actor_type` {system, customer, staff}, `actor_id`, `reason`, `created_at`. |
| `ProcurementRecord` ➕ | `order_item_id` (1:1 for PROXY), `status`, `source_order_ref`, `actual_cost_amount/currency`, `purchased_at`, `buyer_id`, `notes`. Enables margin reporting. |
| `OrderCodeCounter` ➕ | single-row table updated under lock (UPDLOCK) to issue sequential codes. (SQL Server `SEQUENCE` is not exposed by Django ORM.) |

### 2.11 payments
| Table | Columns / rules |
|---|---|
| `Payment` | `order_id` (PROTECT), `provider`, `amount_irr`, `currency_code`, `status`, `idempotency_key` UK, `provider_ref`, `captured_at`, `expires_at`. |
| `PaymentAttempt` | `(payment_id, attempt_no)` UK, `status`, `redirect_url`, `request_hash`, `error_code`, `started_at`, `completed_at`. |
| `PaymentTransaction` | append-only ledger; `(provider, provider_txn_id)` UK filtered; `signature_verified`; `raw_payload` redacted JSON (**never** card data). |
| `PaymentWebhookEvent` ➕ | `(provider, event_id)` UK; `signature_valid`; `payload_hash`; `processed_at`. |
| `Refund` | see ERD; `CHECK (amount_irr > 0)`; service enforces Σ refunds ≤ captured. |

### 2.12 shipping
| Table | Columns / rules |
|---|---|
| `ShippingZone` / `ShippingZoneRegion` | `zone_type`; region rows `(country_code, province?, city?)`. |
| `ShippingMethod` | `zone_id`, `code`, `carrier`, `cost_irr`, `free_over_irr` (demo: 200,000,000 IRR = 20M Toman), `min_days`, `max_days`, `is_active`. Demo values: standard 4,500,000 IRR, express 9,500,000 IRR **(unconfirmed)**. |
| `DeliveryEstimateRule` ➕ | `(fulfillment_type, source_id nullable, zone_id)` → `min_days`, `max_days` (demo: stock 2–4, order 14–21). |
| `Shipment` | `order_id`, `method_id`, `leg_type`, `carrier`, `tracking_number`, `status`, `shipped_at`, `delivered_at`. |
| `ShipmentItem` ➕ | `(shipment_id, order_item_id)` UK, `quantity`. |
| `ShipmentTrackingEvent` | append-only; `(shipment_id, provider_event_id)` UK filtered. |

### 2.13 reviews
`Review` (`(user_id, product_id)` UK, `order_item_id` nullable → "verified purchase", `rating CHECK 1..5`, `title`, `body`, `language`, `status`), `ReviewMedia` (`storage_key`, `media_type`), `ReviewModeration` (`review_id`, `moderator_id`, `action`, `reason`). Visibility policy **[Q-reviews]**.

### 2.14 ai_assistant
`AIConversation`, `AIMessage` (`(conversation_id, seq)` UK, `content`, token counts, `safety_flags`), `AIRecommendation` (`message_id`, `product_id`, `rank`, `reason`, `clicked_at`), `AIUsageRecord` (provider, model, tokens, `cost_amount/cost_currency`; basis for budget limits and per-user throttles). Retention/redaction job for message content **[Q-ai]**.

### 2.15 notifications
`NotificationTemplate` (`code`, `channel`, `language`, body), `OutboundMessage` (`user_id` nullable, `channel`, `status`, `provider_ref`, `attempts`), `NewsletterSubscription` ➕ (`email` UK, `confirmed_at`, `unsubscribed_at`, `source`).

### 2.16 staff / audit / settings
`Role` (wrapper over Django `Group`: `code`, `description`, `is_system`), `RolePermissionGrant` (documents the matrix; effective permissions = Django `Permission` via the group), `StaffProfile` ➕ (`user_id`, `requires_2fa`, `last_password_change`), `AdminAuditLog` (append-only), `SystemSetting` (`key` UK, JSON `value`, `version`, `updated_by_id`; **never secrets**).

## 3. Delete behaviour
SQL Server rejects multiple cascade paths (error 1785) and `mssql-django` does not support Django 6.1 `DB_CASCADE/DB_SET_NULL/DB_SET_DEFAULT` (system check `fields.E324`). Rules:
1. Use Django-level `on_delete` only; FKs are created as `NO ACTION` in the DB.
2. `PROTECT` for anything financial or historical: Order*, Payment*, Refund, PriceQuote*, PriceHistory, InventoryMovement, Review→Product, OrderItem→Variant.
3. `CASCADE` (Django collector) only for owned children: Product→{Image, Translation, AttributeValue}, Variant→{Option}, Cart→Item, Wishlist→Item, Conversation→Message.
4. `SET_NULL` for optional references (ExternalProduct.canonical_product, CartItem none).
5. Raw SQL deletes bypass Django cascades → forbidden for domain tables; use services. Deleting a `Product` that has orders is blocked by `PROTECT`; use `archived`.
6. Self-FK (`Category.parent`) uses `PROTECT`; subtree moves by service (updates `path`).
7. GDPR-style erasure = anonymise `User`/`Address`/`CustomerProfile` fields in place; orders keep only what accounting law requires.

## 4. Transactions and concurrency
- `ATOMIC_REQUESTS=False`; services use `transaction.atomic()` explicitly and publish events with `on_commit`.
- Enable **`READ_COMMITTED_SNAPSHOT ON`** (row versioning) so reads don't block writes; use `select_for_update()` (maps to `UPDLOCK, ROWLOCK`) only on rows being mutated (cart, quote, order, payment, counter). `select_for_update(of=…)` is unsupported.
- Stock and counters use single-statement conditional updates; unique constraints are the final guard against races.
- Celery tasks are idempotent: keyed by unique constraints (`TranslationJob`, `ImportJobItem`, `InventoryMovement`, `PaymentTransaction`).
- `bulk_create(ignore_conflicts=True)` is **unsupported** (no `ON CONFLICT`): use `update_or_create`/`MERGE`-style raw SQL in a reviewed helper, or insert-then-catch `IntegrityError`.

## 5. Features not available / limited on SQL Server and the alternative
| Feature | Status (per Microsoft docs, Nov 2026) | Alternative in this design |
|---|---|---|
| `DB_CASCADE`, `DB_SET_NULL`, `DB_SET_DEFAULT` (Django 6.1) | unsupported (multiple cascade paths) | Django-level `on_delete`, PROTECT-first |
| `bulk_create(ignore_conflicts=True)` | unsupported | explicit upsert helper |
| `JSONField` `contains` lookup | unsupported; nested lookups differ; stored as `nvarchar` | key-path lookups, promote hot fields to columns |
| `select_for_update(of=…)` | unsupported | lock the single table needed |
| `BitAnd/BitOr/BitXor`, `Avg` on `DurationField`, ORDER BY inside subqueries | unsupported/limited | compute in Python or raw SQL; store durations as ints |
| Time zones & timedeltas | limited | UTC-only storage, convert at edges |
| Add/Remove constraint in one migration | can conflict | one constraint operation per migration |
| `hierarchyid`, PostgreSQL-only features (arrays, `ILIKE`, trigram, `pg_trgm`, GIN) | n/a | materialised path, normalised columns, search port |
| Composite PKs (Django 5.2+) | partial | not used (surrogate keys) |
| Native `json` type (SQL Server 2025) | not exposed by ODBC driver | `nvarchar(max)` |
