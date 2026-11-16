# Index Strategy (SQL Server)

Principles
1. **Clustered index = `bigint identity` PK** on every table (narrow, ever-increasing). UUID/`public_id` gets a *non-clustered* unique index.
2. Django creates a non-clustered index per FK; keep them, but **replace** single-column FK indexes with composite ones when a composite starts with the same column (avoid redundancy).
3. Index to the *queries in §2*, not to every column. Review with `sys.dm_db_missing_index_details` and Query Store after real data exists.
4. **Filtered indexes** for nullable-unique and "active/current" subsets (`WHERE col IS NOT NULL`, `WHERE status='active'`). Verify `UniqueConstraint(condition=…)` generation in spike T3/T4.
5. Covering indexes with `INCLUDE` for hot read paths (spike T5 verifies `Index(include=…)` support; otherwise create via `RunSQL` migration).
6. Append-only log tables: indexes minimal (time + owner) to keep inserts cheap; archive instead of partition initially.
7. All text equality indexes are on **normalised** columns (`*_normalised`), not raw text.

## 1. Mandatory uniqueness (constraints)
| Table | Unique |
|---|---|
| User | `phone_e164` (filtered NOT NULL), `email` (filtered NOT NULL), `public_id` |
| Product / Brand / Category | `slug`, `public_id` |
| ProductVariant | `sku`; `(product_id, option_signature)` |
| ProductTranslation | `(product_id, language)` |
| ProductVariantOption | `(variant_id, attribute_id)` |
| ProductSource | `code` |
| ExternalProduct | `(source_id, external_id)` |
| ExternalProductVariant | `(external_product_id, external_variant_id)` |
| ProductMatchCandidate | `(external_product_id, product_id)` |
| ImportJob | `idempotency_key` (filtered) · ImportJobItem `(job_id, external_id)` |
| CurrencyRate | `(base_code, quote_code, provider, rate_at)` |
| PricingRuleSet | `version` |
| InventoryItem | `(variant_id, warehouse_id)` · InventoryMovement `(reference_type, reference_id, movement_type, inventory_item_id)` |
| Cart | filtered `(user_id) WHERE status='active'`, `(session_id) WHERE status='active'` |
| CartItem | `(cart_id, variant_id)` · WishlistItem `(wishlist_id, product_id)` |
| Order | `code`, `price_quote_id`, `(user_id, idempotency_key)` filtered |
| Payment | `idempotency_key` · PaymentTransaction `(provider, provider_txn_id)` filtered · PaymentWebhookEvent `(provider, event_id)` |
| Shipment tracking | `(shipment_id, provider_event_id)` filtered |
| Review | `(user_id, product_id)` |
| NewsletterSubscription | `email` |
| AIMessage | `(conversation_id, seq)` |
| TranslationJob | `(entity_type, entity_id, target_language, source_text_hash)` |

## 2. Query patterns → indexes
| # | Query (source: Phase 0) | Index |
|---|---|---|
| Q1 | **Listing**: filter by category (+brand/source/sale/available/price ≤ X), sort by popular/new/price/discount, paginate | On `ProductListingProjection`: `IX(category_id, is_available, min_final_irr) INCLUDE (max_discount_percent, brand_id, popularity_score)`; `IX(brand_id, min_final_irr)`; `IX(popularity_score DESC)`; `IX(new_until DESC)`; `IX(max_discount_percent DESC) WHERE max_discount_percent > 0` (sale). Category is denormalised onto the projection (`category_id`, `category_path`) so subtree filters use `path LIKE '/1/7/%'` (index seek on prefix). |
| Q2 | Facet counts (brands, sizes, colours, sources) | Counts from projection + `ProductVariantOption (option_id, variant_id)`; consider cached facet table refreshed by Celery if slow |
| Q3 | Product page by slug/public_id | `UK(slug)`, `UK(public_id)`; variants `IX(product_id, is_active)`; images `IX(product_id, sort_order)`; translations `UK(product_id, language)` |
| Q4 | Search `q` (Persian) | `IX(language) INCLUDE(product_id)` + normalised `search_text`; **Full-Text index on `search_text`** only if a suitable language/word breaker is available (spike T9), else `LIKE` on prefix + search port with external engine later |
| Q5 | Wishlist state for N product ids | `WishlistItem UK(wishlist_id, product_id)` |
| Q6 | Recently viewed / wishlist hydrate `ids=` | `UK(public_id)` |
| Q7 | Cart read | `CartItem UK(cart_id, variant_id)`; `Cart` filtered unique on user/session |
| Q8 | Current price of a variant | `VariantPrice PK(variant_id)` |
| Q9 | Price history for staff | `PriceHistory IX(variant_id, created_at DESC)` |
| Q10 | Latest FX rate per pair | `CurrencyRate IX(base_code, quote_code, rate_at DESC) INCLUDE (rate)` |
| Q11 | Repricing scan: variants by source & currency | `ExternalProductVariant IX(canonical_variant_id)`, `ExternalProduct IX(source_id, status)` |
| Q12 | Import upsert by (source, external_id) | the UK |
| Q13 | Match by GTIN | `ExternalProductVariant IX(gtin) WHERE gtin IS NOT NULL`; `Product IX(brand_id, manufacturer_model_code)` |
| Q14 | Review queue | `ProductMatchCandidate IX(status, score DESC)` |
| Q15 | Stock check/reserve | `InventoryItem UK(variant_id, warehouse_id)` |
| Q16 | Expired reservations | `InventoryReservation IX(status, expires_at) WHERE status='active'` |
| Q17 | My orders | `Order IX(user_id, placed_at DESC) INCLUDE (code, status, total_irr)` |
| Q18 | Staff order queues | `Order IX(status, placed_at)`; `OrderItem IX(status, fulfillment_type)`; `ProcurementRecord IX(status)` |
| Q19 | Payment reconciliation | `Payment IX(status, created_at)`; `IX(provider, provider_ref)` |
| Q20 | Shipment tracking | `Shipment IX(tracking_number)`; `IX(order_id)` |
| Q21 | Audit search | `AdminAuditLog IX(object_type, object_id, created_at DESC)`; `IX(actor_id, created_at DESC)` |
| Q22 | AI usage per user/day | `AIUsageRecord IX(user_id, created_at)` / `(session_id, created_at)` |
| Q23 | Import log viewing | `ImportLog IX(job_id, id)` |
| Q24 | Session cleanup | `CustomerSession IX(last_seen_at)` |

## 3. Denormalisation that serves the indexes
- `ProductListingProjection` is mandatory for Q1: price range/sort across variants and sources cannot be indexed through joins.
- `Category.path` + `depth` for subtree filters.
- `ProductTranslation.search_text` normalised (ی/ي, ک/ك, ZWNJ→space, Persian→ASCII digits, lower-case Latin).
Rebuild commands (`manage.py rebuild_listing_projection`) exist for each, and nightly integrity checks compare projection vs sources.

## 4. Maintenance
- Statistics: auto-update on; enable **Query Store**; scheduled index rebuild/reorganise based on fragmentation (>30%).
- RCSI on (see DATABASE_DESIGN §4).
- Retention: `ImportLog` 90 d, `AIMessage` per policy, `CustomerSession` 90 d idle, `OTPChallenge` 7 d.
- Performance budget to test in spike T11: listing p95 < 150 ms @ 100k products/400k variants; product page p95 < 80 ms; reservation under 100 concurrent attempts without oversell.
