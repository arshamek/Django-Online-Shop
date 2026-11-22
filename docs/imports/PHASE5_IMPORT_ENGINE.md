# Phase 5: product import engine

Code: `maral_backend/apps/product_sources/providers/` (provider layer), `apps/product_sources/models.py`
(source listings), `apps/imports/` (jobs, pipeline, API). Access terms: `PROVIDER_ACCESS_REQUIREMENTS.md`.
Existing "WebScrapper": `WEBSCRAPPER_ASSESSMENT.md` (not found in the repository).

## What is and is not real

* Built and tested: the provider interface, Trendyol and Namshi mappers, fixture transports, authorisation gate,
  rate limiting, the 12-step pipeline, deduplication, Celery job management, staff API.
* **Not verified live:** no Trendyol or Namshi service was contacted. Both providers run on synthetic fixtures
  (`providers/fixtures/`, each flagged `SYNTHETIC`). `LIVE_VERIFIED = False` on both.
* **Not built (later phases):** translation and price calculation. The importer queues hand-off tasks through
  ports (`services/ports.py`); with no handler configured they report `queued: false` and nothing is invented.
  Products wait in `TRANSLATION_PENDING` / `PRICING_PENDING` until staff (existing catalogue API) or the future
  handlers supply reviewed translations and `VariantPrice` rows. The importer never converts currency or sets a
  customer price.

## Provider interface

`BaseProductProvider` (`providers/base.py`): `discover`, `get_product`, `get_variants`, `get_prices`,
`get_stock`, `get_images`, `get_source_url`, `sync`, plus `capabilities`, `assert_ready`. Providers return
provider-neutral DTOs (`providers/dto.py`); a malformed record is returned as a `RejectedItem` so one bad product
does not reject its page. Operations a source's authorised access cannot support raise `UnsupportedOperation`
(e.g. Trendyol category discovery). Transports return native payloads; fixture and live transports share the
mapper, so fixtures exercise the real mapping code. `registry.use_provider()` swaps a provider in tests.

## Pipeline

| # | Step | Where |
|---|---|---|
| 1-2 | create job, select provider | `services/jobs.create_job` (auth gate, param checks, idempotency, concurrency) |
| 3 | retrieve | provider `discover` / `get_product` |
| 4 | validate | provider (structure) + `services/validation` (content) |
| 5 | normalise | `services/normalize` |
| 6 | detect duplicates | `services/matching`, `listing` (unique source+external id, payload hash) |
| 7 | save source product | `services/listing` (raw payload, variants, append-only price/availability) |
| 8 | match or create canonical | `services/pipeline.finish_import`, `services/canonical` |
| 9 | variants and prices | `canonical.sync_canonical_variants` (source prices stay in source currency) |
| 10-11 | queue translation / pricing | `readiness.refresh_readiness` -> `ports` -> Celery |
| 12 | publication | `services/publication.publish` (rules in `readiness.publication_blockers`) |

Unknown brand, category, or size/colour words are never guessed. The listing waits in `PENDING_REVIEW`; staff
approval can teach a mapping (`ExternalBrandMapping`, `ExternalCategoryMapping`, `ExternalOptionMapping`).

## Product statuses (`ExternalProduct.pipeline_status`)

`DISCOVERED -> PROCESSING -> (PENDING_REVIEW) -> IMPORTED -> TRANSLATION_PENDING -> PRICING_PENDING ->
READY_TO_PUBLISH -> PUBLISHED`, with `FAILED` and `ARCHIVED` reachable per `services/statuses.ALLOWED` (an
illegal jump raises). Listing lifecycle (`status`: active/delisted/error) and match state (`match_status`) are
separate fields.

Publication requires: matched canonical product; reviewed `fa` translation (`IMPORTS_REQUIRED_LANGUAGES`; machine
or stale text does not count); every active variant priced; at least one published image
(`IMPORTS_REQUIRE_IMAGE`); the listing still active at the source; the catalogue's own `publish_blockers`; and
non-fixture data. Auto-publish is off (`IMPORTS_AUTO_PUBLISH=false`): staff publish `READY_TO_PUBLISH` items.

## Deduplication

One canonical `catalog.Product` can have many listings. Tiers (`services/matching.py`):

* **T1, auto-link:** a GTIN shared with a linked listing where at least one side's source treats its barcode as a
  real GTIN, brand agrees, exactly one product involved; or brand + manufacturer code.
* **T2, staff review:** brand matches and title/category/colour/size/gender signals score >= 0.55, or a GTIN match
  that is not deterministic (both barcodes untrusted, brand conflict, or two products involved).
* **T3, weak:** candidates recorded (>= 0.35), never auto-link, never force review.
* **Vetoes:** gender, category root, disjoint trusted GTINs. Image similarity is not implemented.

Trendyol's `barcode` is a seller barcode and `productMainId` a seller code, so Trendyol alone never auto-links
by GTIN and never supplies a manufacturer code.

## Jobs (Celery, queue `imports`)

Types: full, incremental (changed since), single, bulk, category (where supported), sync (prices/stock of known
listings), repair. Triggers: manual (staff), scheduled (`IMPORTS_SCHEDULES`, beat every 15 min, idempotent per
window).

* A job runs in time-boxed slices (`IMPORTS_SLICE_SECONDS`) and re-queues itself; the cursor is checkpointed
  per page and each item has a unique `(job, external_id)` row, so retries, redeliveries and recovery never
  repeat finished work.
* Rate limit (local ceiling or upstream 429): job goes `WAITING` until the delay the source gave; no sleeping,
  and an upstream 429 cools off every worker for that source. Timeouts/5xx: exponential back-off (30 s doubling,
  cap 15 min), `IMPORTS_MAX_ATTEMPTS` then `retries_exhausted`. 401/403 and bad configuration fail immediately.
* One bad product becomes a failed item; the job ends `PARTIALLY_FAILED`. All failing -> `FAILED`.
* Cancel: immediate for queued/waiting jobs, cooperative between items for running ones; finished work is kept.
* Retry (failed, partially failed, cancelled): same job, failed items are re-fetched first, then discovery resumes
  from the cursor. `imports.recover_stale` (every 5 min) re-queues jobs whose worker vanished.
* Progress, per-item results, append-only logs (redacted, 90-day retention) and a summary
  (counts, duration, errors by code, products by status) are on the job.

## Staff API (`/api/v1/staff/imports/...`)

| Endpoint | Method | Permission |
|---|---|---|
| `sources` (mode, authorisation, capabilities) | GET | `imports_manage` |
| `jobs` start import / list | POST / GET | `imports_manage` |
| `jobs/{id}` status; `/logs`; `/items` | GET | `imports_manage` |
| `jobs/{id}/cancel`, `/retry` | POST | `imports_manage` |
| `products` list, `products/{id}` preview | GET | `imports_manage` |
| `products/{id}/approve`, `/reject` | POST | `imports_review_matches` |
| `products/{id}/publish` | POST | `imports_manage` |

All require an authenticated staff user. Start/cancel/retry/approve/reject/publish are audited. `Idempotency-Key`
header supported on start. Decision for you: Import Manager can publish. If publishing should need
`catalog_manage` (Product Manager) instead, change `ProductPublishView.required_permissions`.

## Settings (all in `config/settings/base.py`, env in `.env.example`)

`IMPORTS_PROVIDER_MODE` (fixture|live), `IMPORTS_ALLOW_FIXTURE_PUBLISH`, `IMPORTS_AUTO_PUBLISH`,
`IMPORTS_CREDENTIALS`, `IMPORTS_SUPPORTED_CURRENCIES`, `IMPORTS_REQUIRED_LANGUAGES`, `IMPORTS_TRANSLATION_HANDLER`,
`IMPORTS_PRICING_HANDLER`, `IMPORTS_UNMATCHED_POLICY` (create_draft|review), match thresholds, paging/slice/
retry/retention values, `IMPORTS_SCHEDULES`. Per source: `ProductSource.rate_limit_per_min`, `credential_ref`.

## Tests

`tests/test_imports_providers.py`, `_pipeline.py`, `_jobs.py`, `_api.py`, `_smoke.py`: 143 tests using fixtures,
`InMemoryProvider` and `ScriptedTransport`. They cover provider failures, invalid and missing data, duplicates,
rate limits, retry/back-off, idempotency, partial imports, network timeouts, cancellation, recovery, schedules,
authorisation and the HTTP guard rails. Run: `TEST_DATABASE=sqlite pytest tests/test_imports_*.py`. They ran on
SQLite only; the SQL Server migration path was not exercised.

## Open decisions

1. How Maral will legitimately obtain Trendyol/Namshi catalogue data (see access doc). Until decided, live mode
   stays off.
2. Whether Import Manager may publish (above).
3. Translation and pricing handlers (next phases) and SAR support if Namshi data uses it.
4. Fixture publish guard: production must keep `IMPORTS_ALLOW_FIXTURE_PUBLISH=false` (the default).
