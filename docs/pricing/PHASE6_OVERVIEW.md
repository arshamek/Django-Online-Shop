# Phase 6: translation, currency exchange and pricing engine

Read `PHASE6_DECISIONS.md` first (what is decided, what the owner must decide, what outside information is missing).
Worked numbers: `PHASE6_CALCULATION_EXAMPLES.md`.

## 1. Apps and where things live

| App | Responsibility |
|---|---|
| `apps/currencies` | `Currency`, `ExchangeRateSource`, `ExchangeRate` (append-only history), `RateRoute` (+ `RateRouteSource`), `RateFetchLog`; provider interface + adapters; resolver; refresh/validation/health; manual rates |
| `apps/pricing` | `PricingRuleSet` (versioned) + `PricingRule`, the Decimal engine, `PriceQuote*` (immutable), `PriceHistory`, repricing, APIs |
| `apps/translations` | `TranslationJob`, `TranslationCache`, `TranslationRecord`, `ProtectedTerm`; masking; provider interface + adapters; service; queue + recovery |

Wiring into earlier phases (additive only): `IMPORTS_TRANSLATION_HANDLER` / `IMPORTS_PRICING_HANDLER` now point at the new handlers
(the test settings keep them `None` so Phase 5 tests are unchanged); storefront prices are written to `VariantPrice` (origin `pricing_engine`);
translations are written to the existing `ProductTranslation`, `CategoryTranslation`, `AttributeOptionTranslation`, `ProductAttributeTranslation`
and `ProductAttributeValue` rows. No existing table was altered.

## 2. Currencies and exchange rates

* **IRR** is the accounting currency; **IRT** (Toman) is a display unit, `1 IRT = 10 IRR` (`Currency.base_factor`). Seeded: IRR, IRT, TRY, AED, USD, EUR.
* A **rate source** is *where* numbers come from (`kind`: market reference / official / bureau / actually-obtainable / manual). Adapters:
  `manual` (never polled), `fixture` (synthetic, refused unless `CURRENCY_ALLOW_FIXTURE_PROVIDER`), `http_json` (configurable endpoint, allow-listed host,
  https only, key from env `CURRENCY_CRED_<REF>`, numbers parsed straight to Decimal). **No live provider was contacted**; `LIVE_VERIFIED = False`.
* A **route** answers "which rate do we use for `TRY -> IRR` when *buying goods*?" It lists sources in priority order and a **maximum age**.
  No route means no rate (`no_rate_route`); nothing is ever assumed. Two purposes: `purchase` (cost side) and `display` (showing other currencies to customers).
* Resolution: first source (by priority) with a *fresh* accepted rate wins; a manual source placed first acts as an override and falls through
  automatically when its rate expires. Nothing fresh: `rate_stale`, or `rate_missing`; a route may explicitly allow a flagged stale fallback.
* Every observation is validated: timestamp present, timezone-aware, not in the future (`CURRENCY_FUTURE_TOLERANCE_SECONDS`), not older than the source limit,
  not older than what is recorded, positive, finite, and optionally within `max_jump_percent` of the previous value (else **quarantined** for staff review).
* A source quoting in Toman is normalised to rial when resolved (`quoted_in: IRT`).
* Health: `healthy / degraded / failing / unknown / disabled` from consecutive failures and overdue polling; every poll is logged in `RateFetchLog`.
* Scheduling: Celery beat `currencies.refresh_due` (every 60 s; each source has its own `poll_interval_seconds`). New usable rates emit `rates_changed`,
  which queues `pricing.reprice_currency`.

## 3. Pricing engine

Pipeline for one unit (all `Decimal`, each IRR line rounded HALF_UP to a whole rial when created):

1. item price in source currency -> IRR through the `purchase` route
2. cost lines in fixed stage order: `purchase_fee`, `shipping`, `customs`, `service_fee`
   (methods: `fixed`, `percent_of_item`, `percent_of_base` = item + earlier lines, `per_kg`; optional min/max per line)
3. profit: `profit_method` markup (`base x p`) or margin (`base x p/(100-p)`), `+ profit_fixed` rules, lifted to `min_profit` if needed
4. rounding rule (`nearest/up/down` to a step that is a multiple of 10 IRR)
5. presentation: IRR as is; IRT exact `/10`; other currencies `/display-rate`, quantised, flagged **indicative**

**Charge modes** make business models a configuration choice: `included` (inside the price and the profit base), `separate` (own customer line, no markup),
`absorbed` (Maral pays; lowers realised profit). `pending` (the default) blocks publication.
**Value status**: `confirmed` (needs a source note), `estimated`, `unknown`. Unknown, pending or missing required costs become **blockers**; the
calculation is still returned as a cost-only preview with `publishable = false`.

**Rules** are scoped by source, source/destination country, category (and descendants), brand, product, fulfilment type, sales model, shipping method,
with validity windows. Rules with a `slot` (and the kinds profit %, minimum profit, rounding) compete: the most specific wins (product > brand >
category > country/source/other); an exact tie is a blocker (`ambiguous_rules`), never an arbitrary pick. Other rules add up.

**Rule sets** are numbered. Draft -> preview -> publish. Published sets are immutable; publishing archives the previous one and queues `pricing.reprice_all`.
The decisions live in the rule set (`profit_method`, `target_profit_percent` + `target_profit_confirmed`, `required_cost_kinds`,
`required_for_fulfillment`, `estimates_policy`, `quote_validity_minutes`, `source_price_basis`, `customer_breakdown`, `default_destination_country`).
`required_for_fulfillment = []` means "applies to every fulfilment type" (the stricter reading).

## 4. Quotes and history

* `PriceQuote` -> `PriceQuoteItem` -> `PriceQuoteComponent`: original price + currency, applied rate + source + timestamp, every component, profit/markup,
  final price, presentation currency (and its rate), rule version, creation and expiry time, calculation hash.
* Invariants checked on creation (`verify_quote`): sum of in-price components = unit price; line = unit x quantity; totals add up; DB `CHECK` constraints too.
* Expiry = min(created + validity, earliest rate expiry): a quote never outlives its exchange rate. `pricing.expire_quotes` sweeps open quotes.
* `lock_quote(id, reference=order)` accepts a quote for an order (idempotent per order, one quote per order enforced by a unique constraint).
  Locked quotes are never expired, recalculated or edited; rows are immutable at model level. The order phase calls this service.
* `PriceHistory` is append-only: every change of a stored price **and** every time pricing was blocked (old price left untouched), with causes
  `initial / rate_change / source_price_change / rule_change / manual`. Concurrent writers: row lock on the variant + `UNIQUE(variant, sequence)` with retry.
* Manual (`origin = manual`) prices are skipped by the engine unless staff `force`.

## 5. Translation

* Entities: product (title, short/long description, SEO fields, free-text specifications), category, attribute name, attribute value (colours...).
  Customer reviews are **not translatable** (guard + test). Brand names are never translated.
* Protection: brand names (+ translations, aliases), model code, SKUs, staff `ProtectedTerm`s, mixed letter/digit codes, measurements and every number
  (Latin, Persian and Arabic digits) are replaced by `⟦n⟧` placeholders before text leaves the server and restored after. The answer is rejected
  (and never cached or saved) if a placeholder is lost, duplicated, invented or altered, or if a digit run changed.
* Provenance: `origin` (source / machine / human), `status` (machine, needs_review, approved, human, stale), provider, model, source-text hash, reviewer.
  Rows with no record (entered before Phase 6) count as human and are protected.
* Machine output only replaces missing text or unreviewed machine text. Human or approved text is never overwritten; if its source changed it is flagged `stale`.
* Pipeline: `enqueue` (idempotent per entity + language + source hash) -> `translations.run_job` -> cache (keyed on the masked text, languages, provider, model,
  prompt version) -> provider -> verify -> write atomically. Failures: transient errors retry with back-off (1, 2, 5, 15, 30, 60 min) up to
  `TRANSLATION_MAX_ATTEMPTS`, permanent errors go to `dead`; `translations.recover` (every 5 min) frees jobs stuck in `running`, re-dispatches lost messages;
  staff can re-queue any job.
* AI adapter: server-side only. Key from `AI_SETTINGS` (env `TRANSLATION_API_KEY` / `AI_API_KEY`, model `AI_MODEL`); sent in a header, never logged, stored or
  returned (a test inspects every translation API response). **Not verified against a live service.**

## 6. API (all under `/api/v1/`)

Public: `GET currencies`, `GET exchange-rates` (display rates only), `POST pricing/calculate`, `GET pricing/products/{id}?currency=`,
`POST pricing/quotes`, `GET pricing/quotes/{id}`, `POST pricing/quotes/{id}/expire`. Customers never see profit, margin, internal cost or rule names;
unavailable prices give a generic `price_unavailable` (422). Quotes belong to the signed-in user or to the `X-Guest-Token` holder (hashed); anyone else gets 404.

Staff (`accounts.pricing_manage`, Finance Manager): `staff/currencies[/{code}]`, `.../sources[/{code}[/refresh]]`, `.../routes[/{id}]`, `.../rates` (history + manual POST),
`.../rates/{id}/review`, `.../health`; `staff/pricing/readiness`, `.../rulesets[/{v}[/publish|/rules[/{code}]]]`, `.../preview`, `.../reprice`, `.../history`, `.../quotes[/{id}[/expire]]`.
Staff (`accounts.catalog_manage`, Product Manager): `staff/translations/summary|jobs[/{id}[/retry]]|products/{id}[/{lang}[/approve]]`.
Django admin has the same models with the same permissions; published rule sets, rates, quotes and history are read-only there.

## 7. Operating it

1. `python manage.py migrate` (seeds the six currencies and the manual source), `python manage.py seed_pricing_defaults` (rule set v1, everything pending).
2. In the panel: create rate sources + routes (B-5), then work through `GET staff/pricing/readiness` until `ready` is true; each line says what it blocks.
3. Environment: `TRANSLATION_PROVIDER=llm`, `AI_MODEL`, `TRANSLATION_API_KEY`; `CURRENCY_HTTP_ALLOWED_HOSTS`, `CURRENCY_CRED_<REF>`; keep both `*_ALLOW_FAKE/FIXTURE_*` false in production.
4. Run Celery workers for queues `pricing` and `translations` and the beat scheduler (new entries: `currencies-refresh-due`, `pricing-expire-quotes`, `translations-recover`).
   `pricing.reprice_changed_sources` is available as a task for source-price changes; add it to the beat schedule once the owner wants automatic repricing of
   priced variants (the import pipeline itself only asks for prices of variants that have none).

## 8. Known limits

* Verified on SQLite only (`TEST_DATABASE=sqlite`). SQLite stores decimals as doubles beyond ~15 significant digits; SQL Server `decimal(24,10)` is exact.
  Run the suite against SQL Server before go-live. The concurrency tests simulate the collision deterministically; a multi-connection test needs SQL Server.
* Shipping/customs are per unit; order-level consolidation is decision B-7. Foreign display currencies are indicative; the catalogue API still shows IRT/IRR.
* Source prices are used as given (tax/VAT on the source side is not modelled). Direct-inventory variants without a source listing keep manual prices.
* Legacy placeholders `CURRENCY_RATE_PROVIDER` / `CURRENCY_RATE_API_KEY` in Phase 1 settings are unused by Phase 6 (sources are configured in the database).
