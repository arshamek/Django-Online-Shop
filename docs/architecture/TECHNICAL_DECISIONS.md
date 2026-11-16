# Technical Decisions

Format: **ID — decision · status · recommendation · alternatives · consequences.**
Status: `Proposed` (my recommendation, needs your approval) · `Needs input` (cannot be decided without you) · `Fixed` (already in your instructions).

## 1. Decision register
| ID | Decision | Status | Recommendation | Alternatives / consequences |
|---|---|---|---|---|
| D-0 | Modular Django monolith, DRF, Celery+Redis, Docker, S3-compatible storage | Fixed | as instructed | — |
| D-1 | Django & Python versions | Proposed | **Django 5.2 LTS + Python 3.12** (supported by `mssql-django` 2.0; Python 3.12 also satisfies Django 6.x later). Plan the move to the next Django LTS once `mssql-django` supports it. | Django 6.1 now: newest features but short support window and no DB-level cascades on SQL Server anyway. Pin exact versions in the spike; lock with `uv`/`pip-tools`. |
| D-2 | DB driver | Proposed | **pyodbc + ODBC Driver 18** (default, longest track record). Re-evaluate `mssql-python` (new in `mssql-django` 2.0) after spike T13. | `mssql-python`: simpler deployment but newer; no MARS → `QuerySet.iterator()` limits. |
| D-3 | SQL Server edition, hosting, licensing; dev/CI container | Needs input | Dev/CI: SQL Server 2022/2025 Developer in Linux container. Production: a licensed edition or Azure SQL / managed service — **Express has hard database-size and memory limits**; confirm the budget. | Check container support on your developers' hardware (ARM laptops). |
| D-4 | Collation & Unicode | Proposed | `nvarchar` everywhere; database collation case-insensitive/accent-sensitive with a UTF-8-capable or `_100_` version; normalise Persian/Arabic letter variants, ZWNJ and digits in application code before storing search/compare columns. | Binary collation would break case-insensitive email/slug uniqueness. Spike T9 validates sorting and equality of Persian text. |
| D-5 | Append-only enforcement | Proposed | App-level guards + **separate DB role without UPDATE/DELETE** on log/ledger tables in production. No triggers. | Triggers break `bulk_create` ID retrieval in the backend. |
| D-6 | Authentication | Needs input | HttpOnly session cookie + CSRF; **SMS OTP** primary (checkout already collects Iranian mobile), optional password later; mandatory 2FA for staff. Custom `User` model with nullable-unique phone/email, created **before the first migration**. | JWT access+refresh if cross-site/mobile app; SMS provider and cost needed. |
| D-7 | API toolkit & formats | Proposed | DRF + `drf-spectacular`; money as `{amount: "decimal string", currency}`; error envelope; see `API_CONVENTIONS.md`. | GraphQL rejected (no requirement; adds complexity). |
| D-8 | Public identifiers | Proposed | `public_id` UUID in API; slug for SEO routes (product/category/brand); bigint internal. Frontend loader accepts id or slug. | Using `slug` as `id` is possible but Persian slugs are fragile. |
| D-9 | Search | Proposed | Normalised `search_text` + SQL (`LIKE`/optional Full-Text if a suitable language is available) behind a `SearchBackend` port; plan an external engine (OpenSearch/Meilisearch) only if relevance/latency need it. | Spike T9 decides whether SQL Server Full-Text is usable for Persian. |
| D-10 | Translation | Proposed | Async machine translation via provider port → `ProductTranslation.status=machine`, shown only per configurable policy (`needs_review` hidden for `ar/tr/en` until approved?). Retranslate only when `source_text_hash` changes. Persian is the primary language: if source text is Turkish/Arabic/English, `fa` is generated first. | Human review for brand-sensitive categories (beauty/perfume). **Which provider/model and budget: needs input.** |
| D-11 | Accounting & payment currency | Needs input | **IRR** is the system-of-record currency; `IRT` = IRR/10 display. Payments/refunds in IRR (gateway must support it). Foreign-currency charging not designed. | If you want foreign-currency payments for international customers, `Payment.currency_code` and quotes need extra work (fields already carry a currency). Alias used in other docs: *D-payments*. |
| D-12 | Sensitive customer data (national ID, customs docs) | Needs input | Do not store unless legally required; if required: field-level encryption, restricted role, audit on read. | Affects `CustomerProfile` and checkout. |
| D-13 | Repository layout | Needs input | Separate repo (or `backend/` in a monorepo) with **no changes to frontend files**; frontend integration done later behind a data-mode flag. | Monorepo simplifies shared OpenAPI types; separate repo isolates Lovable sync (history rewrite warning in `AGENTS.md`). |
| D-14 | Browser → API path | Needs input | Browser calls Django directly on a same-site subdomain (cookies + CORS allowlist). | Via TanStack server functions (BFF): hides API origin, adds a hop and CSRF layer. |
| D-15 | Price/quote lock duration; order code format | Needs input | Quote TTL configurable (default 15 min); order code `MRL-` + sequential number (widens beyond 6 digits). | Random 6-digit codes collide; frontend shows `MRL-######`. |
| D-16 | Fulfilment per variant, derived product badge | Proposed | per-variant policy; product summary = `stock` if any variant sellable from stock, else `order`; mixed carts split into shipments. | Matches frontend badge; needs your confirmation **[Q-stock]**. |
| D-17 | Imports | Proposed | Provider ports + mocks first; real adapters only with documented permission (official API/licensed feed). | Scraping and anti-bot circumvention are excluded by your rules. |
| D-18 | Task framework | Proposed | Celery with per-domain queues, `acks_late`, retry with exponential backoff + jitter, DLQ-style `failed` status tables, job-status records, soft/hard time limits, Flower or Prometheus metrics. | Redis as broker is acceptable; persistence/HA of Redis to be decided. |
| D-19 | Observability | Proposed | JSON logs (`structlog`), request id, OpenTelemetry traces optional, health + readiness, Sentry-style error reporting. | — |
| D-20 | CI | Proposed | GitHub Actions: lint (ruff), types (mypy/pyright on services), import-linter contracts, pytest against SQL Server container, OpenAPI diff, migration check (`makemigrations --check`). | — |

## 2. Open business questions referenced by the design
| Ref | Question |
|---|---|
| Q-pricing | FX source/frequency, formula components (shipping, duty, fees, margin), rounding step, discount ownership, quote validity |
| Q-import | Which official APIs/feeds exist for Trendyol & Namshi under your licence; curated vs mirrored catalogue; sync frequency; unmatched-product policy |
| Q-stock | Stock tracking granularity, reservation timing, mixed carts, meaning of "available" and "stock/order" badge |
| Q-orders | Final status list/labels, cancellation and return windows, notifications per status |
| Q-guest | Guest checkout allowed in production? Phone verification required? |
| Q-customs | Customs/duty responsibility and customer ID requirements |
| Q-reviews | Who may review; moderation; show on launch? |
| Q-ai | AI provider/model, data it may use, retention, budget caps |
| Q-loyalty | Meaning of «عضو طلایی»; any loyalty program |
| Q-cms | Is home/hero/offer content to be managed from the backend? |
| Q-payments | Gateway(s), prepayment/deposit, refund methods, settlement timing |

## 3. `mssql-django` compatibility — what is verified and what is not
**Verified by reading official sources (Microsoft Learn pages, GitHub release notes, PyPI) — not by running code.** My sandbox has no SQL Server and its network allow-list excludes Microsoft's container registry, so **nothing below has been executed by me**. Treat the spike (§4) as the real verification.
| Item | Finding (sources: Microsoft Learn "mssql-django" support-lifecycle/limitations/what's-new, GitHub release notes 1.7, 1.8.0, 2.0, PyPI) |
|---|---|
| Package status | Official Microsoft-supported backend; latest line **2.0 (Sept 2026)**. |
| Versions | 2.0: Django **5.2, 6.0, 6.1**; Python **3.10–3.14** (Django 6.x needs 3.12+); SQL Server **2017–2025**, Azure SQL DB/MI, Fabric. Older versions (1.8.x) cover Django 3.2–6.1 and Python 3.8+. |
| Drivers | pyodbc + Microsoft ODBC Driver 18 (default; 17 fallback; FreeTDS supported); `mssql-python` optional per database in 2.0. |
| Platforms | Ubuntu 22.04/24.04, Debian 11/12, Windows (x64/ARM64). |
| Fixes policy | Fixes ship only in the newest release — pin and upgrade deliberately. |
| Unsupported/limited | DB-level `on_delete` (`DB_CASCADE/DB_SET_NULL/DB_SET_DEFAULT`, Django 6.1) — SQL Server multiple-cascade-path rule (error 1785, check `fields.E324`); `bulk_create(ignore_conflicts=True)`; `JSONField` `contains` lookup and nested-lookup differences, JSON stored as `nvarchar` (no native `json` via ODBC); `select_for_update(of=…)`; `Avg` on `DurationField`; bitwise aggregates; ORDER BY in subqueries; limited time-zone/timedelta support; add/remove-constraint conflicts in one migration; partial CompositePrimaryKey (Django 5.2+); two known bugs: pattern lookups with `F()` not escaping `[ ]` (#573), `JSONField key__iexact=None` (#574). |
| Features available | Encrypted connections, Always Encrypted, Entra auth, MARS (pyodbc), connection pooling (pyodbc) |
**Consequences already reflected in the design:** Django-level cascades with PROTECT-first; no composite PKs; JSON limited to opaque payloads; no triggers; explicit upsert helper; UTC integers for durations; filtered unique indexes for nullable-unique.
Not verified at all (only the spike can answer): Django 5.2 + DRF + Celery combination on this backend, filtered-unique generation for nullable unique columns, `Index(include=…)`, Persian Full-Text support, `select_for_update` lock hints and deadlock behaviour, performance at target volume.

## 4. Compatibility spike (to run only after approval; ~2–3 days)
Deliverable: `docs/database/COMPATIBILITY_REPORT.md` + a throwaway `spike/` project (not production code). CI container: SQL Server Developer on Linux, ODBC Driver 18.
| Test | What | Pass criterion |
|---|---|---|
| T1 Migrations | apply all draft models forward/backward from empty DB; `makemigrations --check` clean; custom `User` before first migration | no manual SQL needed except documented `RunSQL` |
| T2 Foreign keys | full FK graph incl. self FK and orders→quote→order items; `manage.py check` has no `fields.E324` | no error 1785 |
| T3 Nullable unique | `User.phone/email`, `Address.is_default` filtered: insert many NULLs | N rows with NULL succeed; duplicates fail |
| T4 Constraints | `UniqueConstraint(condition=…)`, `CheckConstraint` (enums, `reserved<=on_hand`, `final_line=unit*qty`); add/remove in separate migrations | constraints present in `sys.*` and enforced |
| T5 Indexes | `Index(include=…)`, descending, filtered; generated DDL inspected | index definitions match INDEX_STRATEGY |
| T6 Decimals | round-trip `(18,4)`, `(24,10)`, `(18,0)`; `Sum/Avg` aggregates; rounding helpers; no float coercion | exact equality with Python `Decimal` on 10⁶ random values |
| T7 JSON | store/retrieve Persian JSON; key-path filter; `contains` raises `NotSupportedError`; `ISJSON` check | behaves as documented |
| T8 Transactions & concurrency | nested atomic/savepoints, `on_commit`, `select_for_update`, conditional-UPDATE reservation with 100 concurrent workers, deadlock retry helper, RCSI on | zero oversells; deadlocks handled |
| T9 Persian text | equality/sort/LIKE on normalised vs raw text; email case-insensitivity; Full-Text availability for Persian | documented go/no-go for D-9 |
| T10 Deletes & bulk | PROTECT/CASCADE via ORM; large cascade timing; `bulk_create` ID return; `ignore_conflicts` failure mode; upsert helper | matches DATABASE_DESIGN §3–4 |
| T11 Performance | seed 100k products/400k variants/1M price rows; listing, product page, repricing batch, import upsert; `OFFSET/FETCH` deep pages vs keyset | targets in INDEX_STRATEGY §4 |
| T12 Celery/Redis | idempotent tasks with DB constraints; worker connection lifecycle; retry/backoff | no duplicate rows under redelivery |
| T13 Drivers | pyodbc vs `mssql-python` on T6/T8/T11 | decision for D-2 |
**Go/no-go:** SQL Server stays if T1–T4, T6, T8 pass and T11 meets targets. If a blocker appears I will report the workaround cost and, honestly, whether PostgreSQL would remove it — your call. The domain code avoids database-specific features so that option is not foreclosed.

## 5. Risks
| Risk | Mitigation |
|---|---|
| Pricing formula unknown → rework of `pricing` | rules are data (`PricingRule`), formula components pluggable; start with mock rule set |
| Source access not granted | mock providers + manual CSV/staff import path that does not scrape |
| Listing performance on SQL Server | projection table + targeted indexes + spike T11 early |
| Persian search quality | normalisation + search port; external engine path |
| Lovable-generated frontend changes under us | integration only through a data-access layer behind a flag, after approval; contract tests |
| Scope creep from 23 apps | create each app in the phase that needs it |
| Legal/payment constraints on cross-border purchasing | outside engineering; flagged for your legal/finance review |

## 6. Proposed implementation roadmap (after approval)
| Phase | Scope |
|---|---|
| 2 | Project skeleton, Docker, settings, custom `User`, `common`, CI, **compatibility spike** and report |
| 3 | Currencies, pricing engine (mock rules), brands, categories, catalogue models + read API (seeded with the 16 demo products) |
| 4 | Product sources, mock providers, imports, matching/review queue, translation pipeline |
| 5 | Auth/OTP, profile, addresses, cart, wishlist |
| 6 | Quotes, inventory, orders, shipping |
| 7 | Payments (fake gateway → real after decision), refunds |
| 8 | Reviews, notifications, AI assistant (grounded), staff/audit hardening |
| 9 | Frontend integration (needs separate approval), performance, security review |
**No implementation starts until you approve this design and answer the *Needs input* items.**
