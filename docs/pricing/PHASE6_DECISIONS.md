# Phase 6: decision register (translation, currency, pricing)

Written **before** implementation, as requested. The system is built so that none of the open business decisions
needs a code change: each one is a setting or a rule that an authorised administrator fills in later.
Until an owner decision is made, the system **refuses to publish a price that depends on it** (it never guesses).

Legend: ✅ built in Phase 6 · ⏳ waiting for the owner · 📥 waiting for outside information

---

## 1. Technical decisions (implemented now)

| ID | Decision | Why |
|----|----------|-----|
| T-1 | IRR (rial) is the accounting currency; Toman (IRT) = IRR / 10 is a *display unit* (`Currency.base_factor`). | Matches D-11 and Phase 4 (`VariantPrice` is IRR). |
| T-2 | Every money calculation uses `decimal.Decimal`. No `float` anywhere in `apps/pricing`, `apps/currencies`. A test scans the source for it. | Brief, Part C. |
| T-3 | Exchange rates are append-only rows (`ExchangeRate`); the "current" rate is *resolved* through an admin-configured route, never assumed. | History + "market rate ≠ purchase rate". |
| T-4 | Pricing rules live in a **versioned rule set**. Published sets are immutable; edits happen in a draft that is previewed, then published. | Approved design (`PricingRuleSet`). |
| T-5 | A price quote is an immutable snapshot (inputs, rate, timestamp, every component, rule version). A locked quote can never change. | Part D / H-15. |
| T-6 | Repricing is non-destructive: if a price cannot be computed (missing/stale rate, pending decision) the existing price is left untouched and the reason is written to `PriceHistory` (outcome `blocked`). | Customers never see a half-calculated price. |
| T-7 | Manual prices (`VariantPrice.origin = manual`) are never overwritten by the engine unless staff force it. | Respect human decisions. |
| T-8 | Translations are stored in the *owner's* translation tables (additive columns: origin, status, provider, model, hash). Source text is never modified. Machine output never overwrites human or approved text. | Approved design; requirements 6–10. |
| T-9 | Brand names, model numbers, product codes, measurements and numbers are masked before text reaches the provider and verified after; a translation that fails verification is rejected, not saved. | Requirements 1–5. |
| T-10 | Provider ports: `TranslationProvider`, `ExchangeRateProvider`. Concrete adapters are swappable by settings; credentials come only from environment variables on the server. | Never expose credentials. |
| T-11 | Translation jobs are idempotent (unique on entity + language + source hash), retried with back-off, stuck jobs are recovered, exhausted jobs go to a `dead` state that staff can re-queue. | Part A. |
| T-12 | Customer reviews are **not** translatable (hard guard in code, tested). | Permission/republication rule. |
| T-13 | Permissions reuse existing roles: `accounts.pricing_manage` (Finance Manager) for currency + pricing, `accounts.catalog_manage` (Product Manager) for translations. | No new permission model needed. |
| T-14 | When a business decision is pending the engine returns the calculation as a **preview with blockers** (`publishable = false`) so staff can still see numbers; quotes and stored prices require `publishable = true`. | Req. 10, 13, 14. |

## 2. Business decisions that need the owner

Each item says what it means in plain words, the realistic options, what Maral should expect, and what the system
does until it is decided. **No option has been chosen for you.** Where a number is pre-filled it is marked *initial*
and can be changed in the administration panel.

### B-1. How do you sell? (sales model)
*In plain words:* Does the customer see **one all-inclusive price** (product + shipping + customs + fees), or the
**product price plus separate charges** (e.g. shipping and customs paid separately), or a mix depending on category/supplier?
* Option A: all-inclusive. Simple for customers; you carry the risk if shipping/customs cost more than estimated.
* Option B: separate lines. Transparent; checkout is more complex and customers may compare only the product price.
* Option C: mixed per category or supplier (supported by rule scope).
*System meanwhile:* every cost rule has `charge_mode = pending`. A pending mode on a cost that applies blocks publishing.
Modes: `included` (in the price), `separate` (own line for the customer), `absorbed` (Maral pays, not billed).

### B-2. How is profit calculated? (markup or margin)
*In plain words:* with a cost of 100 and a target of 25%: **markup** sells at 125 (profit is 25% *of the cost*);
**margin** sells at 133.33 (profit is 25% *of the selling price*). Margin gives more money for the same number.
Pick one; the administrator can switch later and every quote keeps the method it was made with.
*System meanwhile:* `profit_method = pending` → calculations show a cost-only preview, nothing is published.

### B-3. Target profit
*Initial value 25%* (from the brief), stored as data, flagged "not yet confirmed by the owner".
Open question: one percentage for everything, or different by category/brand/product (supported by rules)?

### B-4. Minimum profit per item
A floor such as "never earn less than X per item", even for cheap products. Optional. If set, it lifts the price when
the percentage would give less (or when absorbed costs eat the profit).

### B-5. Which exchange rate do we use to buy goods?
*In plain words:* the rate on a public website is **not** necessarily the rate at which Maral can really obtain TRY/AED/USD/EUR.
Options: official/bank rate · exchange-bureau rate · open-market rate · the actual rate paid to a payment agent · a manually entered rate.
Also decide: how old a rate may be before it is "stale" (default per route is set by the administrator), and who enters manual rates.
*System meanwhile:* no route configured → **rate missing** → nothing is priced. The route (ordered sources, maximum age,
stale fallback) is configured per currency in the administration panel.

### B-6. Safety buffer for rate swings
Optional extra percentage so a sudden rate jump does not erase profit. Model it as a percentage rule (`service_fee`, base = item)
named "FX buffer"; no code change needed.

### B-7. Shipping
Who is the carrier(s)? Charged per item, per order, or by weight? Included or separate (B-1)? Free above a threshold?
*System meanwhile:* shipping is a **required** cost for proxy-purchase items. No shipping rule (or a rule whose value is
`unknown`) → blocker, never zero. Writing a *confirmed zero* rule is the explicit way to say "free".
Quotes calculate shipping **per unit** until the owner decides on order-level consolidation.

### B-8. Customs and duties
Included in the price, paid by the customer on delivery, or absorbed? Is an estimate acceptable or must it be confirmed?
What happens if the real duty differs? *System meanwhile:* required cost; estimated values are flagged `estimated`.

### B-9. Service and purchase fees
Fixed fee per item, percentage, or both? Do you pass on payment-agent / card fees? (Rules of kind `service_fee`, `purchase_fee`.)

### B-10. Rounding
Round to which step (for example 1,000 Toman = 10,000 IRR) and which direction (nearest, always up, always down)?
*System meanwhile:* no rounding rule → whole rials, and a warning is shown.

### B-11. Source-store discounts
If Trendyol/Namshi discount a product, do customers get it? Default behaviour: the source's current (sale) price is the
cost basis, and the source list price is used to show a discount percentage. Configurable (`source_price_basis`).

### B-12. Quote validity
How long may a customer pay at a quoted price? *Provisional technical default: 30 minutes*; a quote also never outlives its exchange rate.

### B-13. What may customers see of the calculation?
`none` (default), `summary` (rate, original price, costs), `itemized` (every non-profit line). Profit is never shown.

### B-14. Several suppliers for one product
Which one wins? *System meanwhile:* the variant's own `source` is used; no cross-supplier optimisation.

### B-15. Charging foreign customers
Prices in USD/EUR/TRY/AED are **indicative display only**; payment stays in IRR (D-11).

### B-16. May a price be published while some costs are only estimates?
`estimates_policy`: `pending` (default, blocks), `allow` (publish, flagged estimated), `block` (only confirmed costs).

## 3. External information still required 📥

| ID | What | Needed for |
|----|------|-----------|
| E-1 | Shipping tariffs per carrier (zones, weight bands, minimums, fuel/remote surcharges, transit time) | shipping rules |
| E-2 | Customs/duty/VAT rates per product group (HS code mapping), thresholds, who clears goods | customs rules |
| E-3 | An **authorised** exchange-rate source: endpoint, API key, terms of use, update frequency | `ExchangeRateSource` (HTTP-JSON adapter is ready; no live provider was contacted) |
| E-4 | Rates Maral can actually obtain (bank/bureau/agent) and their timing | B-5 |
| E-5 | Payment-agent and purchase fees | B-9 |
| E-6 | Product weights (feed or manual) | per-kg shipping |
| E-7 | Translation provider choice, model, contract, data-processing terms | `TRANSLATION_PROVIDER`, `AI_*` |
| E-8 | Written permission before any review is translated/republished | not built until granted |

## 4. What is real and what is not

* Built and tested with deterministic fixtures: rate providers (fixture, manual, generic HTTP-JSON with injected transport),
  translation providers (fake, LLM adapter with injected transport), the whole pricing engine, quotes, history, APIs.
* **Not verified live:** no external rate or AI service was contacted (no credentials). `LIVE_VERIFIED = False` on both adapters.
* Not built (later phases): orders (quotes expose `lock_quote()` for the order phase), payment, order-level shipping consolidation.
