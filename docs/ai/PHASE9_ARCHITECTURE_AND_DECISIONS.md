# Phase 9 – Maral AI Shopping Assistant: architecture and decisions

Backend only. No React/frontend file was created or changed. Frontend integration is described in
`PHASE9_FRONTEND_INTEGRATION.md`; it has **not** been implemented or tested against a UI.

## 1. What was built

A Django app `apps/ai_assistant/` that lets an authenticated customer describe what they want in Persian (default),
English, Arabic or Turkish, and returns real products from three sources with Maral prices, plus a short reply that
explains the match and asks which product the customer prefers.

```
customer message
   │  POST /api/v1/ai/chat
   ▼
 views/public.py ── DRF auth, scoped throttle (ai_chat), serializer validation
   ▼
 services/chat.py  (one turn)
   ├─ limits.py            daily budget, conversation cap, usage record
   ├─ language.py + nlu.py language detection + filter extraction (rule-based, deterministic)
   │    └─ numbers.py       Persian/English number words, Toman/Rial budgets, sizes
   ├─ discovery.py         one entry point, per-source adapters, conservative dedup, ranking
   │    ├─ sources/internal.py   Maral catalogue   (reuses catalog.selectors / presenters / search backend)
   │    ├─ sources/snapshots.py  stored Trendyol/Namshi imports (NOT a live search)
   │    └─ pricing_estimate.py   Phase 6 engine (calculate) for external prices
   ├─ recommend.py         ranking, "why it matches", comparison (verified attributes only)
   ├─ replies.py           deterministic fa/en/ar/tr templates + the facts given to the model
   ├─ providers/           AIProviderInterface: mock (demo) and Anthropic adapters
   └─ safety.py            untrusted-text sanitising, prompt-injection detection, reply validation
   ▼
 Conversation / Message / UsageRecord rows (this conversation only)
```

The model never searches, prices or decides anything. The backend computes structured results; the model (or the
template in demo mode) only phrases them, and its text is validated before it reaches the customer.

## 2. Reused services (nothing duplicated)

| Need | Reused from |
|---|---|
| Catalogue search, visibility, card data, brand aliases | Phase 4: `catalog.selectors`, `catalog.presenters`, `catalog.search.DatabaseSearchBackend`, `ProductParams` |
| Text normalisation (ی/ي, ک/ك, digits, ZWNJ) | `common.text.normalize_search_text` (extended only for query-side Turkish folding) |
| Imported Trendyol/Namshi listings, prices, availability | Phase 5: `ExternalProduct`, `ExternalProductVariant`, `SourcePrice`, `SourceAvailability`, `authorization_problems` |
| Price estimate, FX, rule sets, blockers, rule version | Phase 6: `pricing.services.engine.calculate`, `rulesets.current_ruleset_or_none`, `inputs.effective_amounts`, currency resolver |
| Cart, orders, payments, shipping | Phase 7/8, untouched. The assistant only points at them. |
| Auth, roles, permissions, audit, throttling, error envelope | Phase 1–3 infrastructure |

New: the `ai_assistant` app (5 tables), the `accounts.ai_manage` permission (Product Manager role + superusers), three
throttle scopes and one setting (`AI_SETTINGS["base_url"]`). No existing table, model or behaviour was altered.

## 3. Decisions (those you approved, and the ones I made)

### Approved by you
1. **Imported snapshots are searched and always labelled** as stored, with `snapshot.price_observed_at`,
   `availability.confidence` and `live_marketplace_result: false`.
2. **Anthropic is the first real provider** behind `AIProviderInterface`; `mock` is the deterministic demo provider.
   The API never says "live/verified" unless credentials exist *and* a staff provider test succeeded
   (`provider.verified`).
3. **`PurchaseInquiry`** for external products: never an order, quote or payment; staff must stamp price,
   availability and feasibility checks before `feasible`.
4. **`ai_manage`** permission for Super Admin (superuser) and Product Manager.

### Mandatory conditions → how they are met
| Condition | Implementation |
|---|---|
| Live Trendyol/Namshi keyword search disabled | No provider is called. `live_search = "disabled"` and `live_search_status` = `AUTHORIZATION_REQUIRED` / `UNSUPPORTED` / `NOT_CONFIGURED`, computed from the source row. No scraping code exists. |
| No second pricing engine; missing costs never zero | `pricing_estimate.estimate` calls `calculate`. If the active rule set has no shipping or customs rule, `missing_costs` lists them and status is `provisional`. Stale price, estimated costs and display blockers also force `provisional`. No rule set / no rate / no price → `incomplete`/`unavailable` with `blockers` and **no amount**. |
| Three prices kept apart | `prices.original` (marketplace, own currency), `prices.estimated_maral` (Toman + status + rule version + time), `prices.confirmed` (always `null`; only checkout creates one). |
| AI cannot invent facts | Facts come from the DB; model text is validated (see §5); on failure the template replaces it. |
| External products enter cart/order only when canonical | The assistant creates no cart item. Internal products → cart; external → `PurchaseInquiry`. `maral.purchasable_in_cart` is true only for a published canonical product. |
| No long-term memory | State is `Conversation.state` (filters, last result refs, last prices, language) of that conversation only. Nothing is read across conversations; no profile table exists (a test asserts the model list). |
| Retention / deletion configurable | `AssistantConfig.retention_days`; daily task + `manage.py purge_ai_conversations`; customer `DELETE /ai/conversations/{id}`. |
| AI tools read-only | The model has no tools. Only conversation and `PurchaseInquiry` rows are written, by explicit customer API calls. A test snapshots prices/stock/orders/payments before and after. |
| Previous phases preserved | 934 previously passing tests still pass (see summary). |

### Decisions I made (please review)
* **Marketplace URL is withheld from customers by default; images follow the source's licence.** The Phase 5 model
  documents `external_url` as staff-only, so staff must opt in with `expose_marketplace_urls` (the URL is always stored on
  a `PurchaseInquiry` for staff). Marketplace image URLs are exposed **only** when the source's own
  `ProductSource.allows_image_copy` is true; no staff/config switch can override a source's licence (the former
  `show_external_images` switch was removed in review, migration `0002`). This is where the Phase 9 brief ("display product
  links/images") and earlier documented rules disagree; the earlier rules win.
* **Fixture rows never reach customers** unless staff enable `allow_fixture_snapshots` (staging/demo) - on every path, including by-reference loading and inquiries.
* **Availability for snapshots is never `IN_STOCK`.** A stored observation is `NOT_VERIFIED` (with `last_known`) or
  `UNKNOWN`. Internal products are `IN_STOCK` only when Maral's own catalogue says so, with `confidence` `high`
  (direct inventory) or `medium` (synced from a source, re-verified at checkout).
* **"Colloquial Toman"**: «پنج تومن» is read as 5,000,000 Toman and «پونصد تومن» as 500,000 Toman; the assumption is
  returned in `assumptions` and the reply states the amount, so the customer can correct it. A bare amount without a
  unit (e.g. «زیر ۵۰۰۰۰۰۰») or a foreign currency always triggers a clarification question.
* **"Cheaper"** means strictly below the average estimated price of the results just shown (or the referenced
  product). It is a one-turn refinement and is not remembered.
* **Budget filter on external products** uses the estimated Maral price. A product with no computable price is
  excluded when a budget is set (and counted in `excluded_without_price`) because the budget cannot be verified.
* **Trailing-slash URLs** from the brief (`/api/v1/ai/chat/`) and the project's slash-less style both work.
* **No model call for greetings, clarifications, availability and selection**: their text is deterministic.

## 4. Data model

| Table | Purpose |
|---|---|
| `ai_assistant_config` | Singleton staff config (no secrets). |
| `ai_assistant_conversation`, `ai_assistant_message` | The current conversation; deleted by the customer or by retention. |
| `ai_assistant_usage` | One row per turn: provider, model, status, error code, tokens, latency, injection flag. **No message text.** |
| `ai_assistant_purchase_inquiry` | Request for staff to look at an external product; price/availability *snapshot* the customer saw; staff re-check stamps. |

Migrations: `apps/ai_assistant/migrations/0001_initial.py`, `0002_remove_show_external_images.py` (drops the image-licence
override switch), `apps/accounts/migrations/0003_alter_staffpermissions_options.py` (adds the `ai_manage` permission; metadata only).

## 5. Safety model

* **Untrusted inputs**: customer text, marketplace titles/descriptions/brands, model output.
* `safety.sanitize_untrusted` strips markup, control/bidi characters, code fences and role markers, truncates, and
  detects injection phrases (English, Persian, Turkish, Arabic). A flagged marketplace title/description is replaced
  (`(title withheld)`) and never sent to the model. Descriptions are never sent to the model at all.
* The customer message is passed to the model inside `<customer_message>` and described as untrusted.
* **Reply validation** (`safety.validate_reply`) rejects: empty/oversized text, echoed injection phrases or role
  markers, any URL (the model is given none), any number of 4+ digits that is not a price/budget the backend
  supplied, delivery-time claims, and availability claims. Free-text availability cannot be attributed to one product, so
  an "in stock"/"available" sentence is accepted only when **every** product shown is `IN_STOCK`, and any size it names
  must be in stock for every product (`in_stock_sizes` per product in the facts; stored snapshots never have any).
  Otherwise the per-product `availability` on the cards and the backend's own sentences are the only availability
  statements (availability/selection replies never go through the model at all). A rejected reply is replaced by the
  template and recorded as `fallback` in `UsageRecord`.
* **Conversation history** given to the provider is the *current conversation's earlier customer messages only*, treated
  as untrusted data: messages flagged as suspected injection (flag stored on the message, and re-checked when read) are
  dropped, the rest are sanitised and truncated (300 chars), assistant turns and marketplace text are never replayed,
  and the Anthropic adapter presents them as a quoted JSON list inside `<earlier_customer_messages>` in the single user
  turn (no forged roles; `<`/`>` neutralised; the system prompt states they are quotations, never instructions). A
  flagged *current* message is replaced by a placeholder for the model (the backend still parses it deterministically).
* **Product-originated text in the model's context** (titles, brands, sizes, colours of internal catalogue products *and*
  imported snapshots, plus filter words taken from the customer or the catalogue) is untrusted even after `clean_text`.
  Three layers: (1) `replies.product_facts / filter_facts / comparison_facts` put every such string under an
  `untrusted_text` (or `untrusted_values`) key, separate from the authoritative fields (prices, `price_status`,
  availability, `last_known_availability`, `in_stock_sizes`, source status, codes), after `safety.safe_fact_text`
  (sanitised, single line, role labels neutralised, truncated, **withheld** if it looks like an injection; filter words are
  dropped entirely when the customer message was flagged); `in_stock_sizes` are authoritative only if the raw value already
  matches a short allow-list (`safe_size_token`: rejected, never repaired); (2) the Anthropic adapter serialises everything
  with `safety.safe_json`, which escapes `<`, `>`, `&` and U+2028/2029 (valid JSON, same text for the model, but a string can never
  contain a literal `</facts>`-style delimiter or start a new "line"); (3) at the provider boundary `_prepare_facts`
  re-sanitises whatever it is given (idempotent), moves legacy flat free-text fields under `untrusted_text`, and never
  sends `template_text`. The system prompt states that anything under `untrusted_text`/`untrusted_values` is data, never an
  instruction, field name, price, status or availability. The public API responses are unchanged (frontends must still escape
  every string they render, including marketplace sizes and colours).
* **One visibility rule for stored listings** (`snapshots.visible_listings`): active listing, active source, source
  enabled by staff, pipeline not failed/archived, fixture rows only if staff allow. Discovery, by-reference loading
  (compare / availability / select, including references remembered in conversation state) and purchase inquiries all use
  it, so a client-supplied id can never reach a listing a normal search would not show.
* **Provider failures** (timeout, rate limit, 5xx, network, bad credentials, unreadable body) never produce a 5xx for
  the customer: results are returned with the template text and `ai_fallback: true`. Error text and credentials are
  never echoed.
* **Ownership**: conversations and inquiries are queried with `user=request.user`; someone else's id is a 404.
* **Limits**: DRF scoped throttles (`ai_chat`, `ai_read`, `ai_inquiry` - the inquiry list view resolves its scope per HTTP method, so POST really uses `ai_inquiry`), a configurable daily message budget per user
  (429 `ai_usage_limit`), 50 stored conversations per user, 20 open inquiries per user, input length caps.
* **Secrets**: `AI_API_KEY` is read from the environment only; it is not stored, serialised, logged or sent to the
  frontend (tests assert this).
* **Audit**: config changes, provider tests, inquiry create/cancel/update, conversation deletion and suspected
  injections are written with `audit.record`.

## 6. Known limitations

See `PHASE9_SOURCES_AND_LIMITATIONS.md`.
