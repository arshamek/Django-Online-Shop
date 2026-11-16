# Frontend Gaps, Missing Requirements & Recommended Order

## 1. Frontend gaps and defects found (no changes made)
**Data / logic**
1. `priceToman` is hard-coded, not derived (see MOCK_DATA_ANALYSIS §2). `IRT` currency unused.
2. Cart holds no price snapshot and uses **index-based** line updates; no line ids; unavailable/removed products are silently dropped from the list (`return null`) but still counted in `totals.count`.
3. No server notion of stock quantity or per-variant availability; `available` is product-level.
4. Express shipping (950,000) never qualifies for free shipping; values duplicated in `checkout.tsx`, `shop-data.ts` and banner text.
5. Wishlist "add to cart" and quick view silently pick the **first color/size** (product page requires size, quick view does not).
6. "Popular" sort has no metric; filters are not URL-synced after load (only seeded from URL).
7. Order id generated client-side (`MRL-` + random 6 digits); cart cleared on fake payment; nothing persisted.
8. Size guide is one generic table for all categories (incl. shoes, bags, perfume).
9. Reviews: same 2 samples on every product.
10. AI "compare" is hard-coded to p3/p4; chat state not persisted; close button wipes conversation.
11. `lang` is stored but nothing is translated; `dir`/`lang` on `<html>` never change.

**Missing UI**
12. No login/register/OTP/password flows; account page is permanently "logged in" with fake user.
13. No order detail/tracking page; no address add/edit/delete; no profile editing.
14. No brands page, no kids category, no static/legal pages (footer links all → `/shop`).
15. No loading skeletons, toasts, or network error states (components exist in `ui/` but are unused); 404/error pages are English.
16. No payment-result page; no product-import/proxy-purchase information shown at checkout (customs, ID, duties).
17. No price breakdown UI (spec asks for one in the brief).

**Repo hygiene**
18. `README.md` has unresolved merge-conflict markers.
19. Both `bun.lock` and `package-lock.json`; `--legacy-peer-deps` needed.
20. Repo name suggests Django, but no Django code exists; monorepo vs separate repo undecided.
21. Pre-release/beta pins (`nitro 3.0.260603-beta`, Vite 8.1.5) — fine for demo, risky for production.

## 2. Requirements evidenced by the frontend
These are **in the design/copy/code**, so treated as intended, pending your sign-off. They are **not** confirmed business rules.
| Topic | Evidence |
|---|---|
| Persian-first RTL storefront; Toman-only display | root `dir="rtl"`, `toman()` |
| Two foreign source stores (Trendyol TR, Namshi AE) plus own stock "Maral" | `Source` type, filters, home tiles, copy "خرید مستقیم از فروشگاه‌های ترکیه و امارات" |
| Two fulfillment modes: in Maral stock (2–4 working days) / ordered from foreign store (14–21 working days) | `fulfillment`, product page |
| Maral performs purchase, shipping and delivery with a final Toman price | home copy "مارال خرید، ارسال و تحویل را با قیمت نهایی تومانی انجام می‌دهد" |
| Percentage discounts; strikethrough list price | `discount`, `PriceDisplay` |
| Variants: size + color; size mandatory | product page |
| Shipping: standard 450k (free ≥20M) / express 950k; shipping cost "estimated" in cart | constants, drawer label |
| Guest checkout (no login step) with mobile-first identity | checkout |
| Iranian mobile and 10-digit postal code validation | checkout |
| Customer account: profile, orders, addresses, wishlist, language | account tabs |
| Languages: fa, en, ar, tr | `Lang` |
| Order tracking, returns, authenticity guarantee, 24/7 support, payment methods (as promised topics only) | footer, trust icons |
| AI assistant: product recommendation, budget filtering, comparison, styling | chips |

## 3. Missing business requirements (do NOT assume — need your decisions)
### Direct sales (Maral stock)
- How is stock tracked (per variant? quantities? multiple warehouses)? Reservation on cart/checkout? 
- Who sets prices for own stock; is the TRY `originalPrice` on Maral items meaningful?
- Can a cart mix stock + order items, and are they one order or split into shipments?

### Proxy purchasing / product import
- How are products imported from Trendyol and Namshi: scraping, official APIs/affiliate feeds, or manual entry? Legal/ToS position?
- How often must price/availability sync; what happens when the source price changes after the customer ordered?
- Is the product list curated (hand-picked) or a mirror of the source catalog?
- Do customers paste a product URL to request an item not listed (the footer mentions "نحوه سفارش از خارج")?
- Who buys the item (staff, agent), what are the purchase statuses, and what if the source cancels/out-of-stock?
- Are other source stores planned beyond the two (type is currently fixed)?

### Currency conversion & pricing
- Which FX rate (open market, exchange, own), update frequency, rounding rule (frontend: nearest 1,000 Toman)?
- Price formula: FX × (item + source shipping + customs/duty + commission/margin + insurance)? Per category/brand margins?
- Is the price shown final and **locked** at order time? Is a breakdown shown to customers?
- Are `discount` percentages from sources or Maral promotions? Coupons/gift cards?
- Is Toman the only payment currency? Is an AED/TRY display needed?

### Delivery
- Carrier(s), coverage (all Iran?), shipping price rules by weight/volume/city; the 450k/950k values are demo?
- Delivery estimates: fixed ranges or computed? Split shipments? Tracking numbers per leg (abroad → Iran → customer)?
- Customs: who pays, any customer ID (national code) required for imported goods? 

### Checkout & payments
- Payment gateway(s) (Zarinpal, etc.), card-to-card, wallet, COD, installments? Prepayment 100% or deposit for imported items?
- Payment timeout and stock/price hold duration; failure and retry behaviour.
- Invoice/VAT requirements; legal entity details (links with your company-registration plan).
- Is guest checkout allowed in production, and is phone verification required?

### Customer accounts
- Login method (OTP via SMS, password, both)? SMS provider? Profile fields (birthday is shown)? Loyalty tier ("عضو طلایی" appears)?
- Guest cart/wishlist merge on login; account deletion/privacy policy.

### Returns & refunds
- Policy per fulfillment mode (stock vs ordered abroad), time windows, who pays return shipping, refund method/timing, non-returnable categories (beauty/perfume).
- Order cancellation rules before/after purchase from source.

### AI recommendations
- Provider/model, language(s), whether it may use order/profile data, grounding strictly in catalog, guardrails, cost limits, conversation retention, escalation to human support.

### Other
- Review policy (verified buyers only? moderation?) — brief says "where authorized".
- Order statuses list and notifications (SMS/email/Telegram?).
- i18n scope: UI only or product data too? Is `ar`/`tr`/`en` required at launch?
- Admin users and roles; reporting needs.
- Hosting/region and **sanctions/payment/legal constraints** for importing from Turkish/UAE stores and domestic payment (your business to confirm).
- Deployment: separate repos or monorepo; browser→Django direct or through the TanStack server.

## 4. Assumptions I would make if you approve (reversible)
1. API is JSON over REST under `/api/v1`, documented with OpenAPI; Django + DRF.
2. Money stored as integer Toman, original price kept with currency; FX/margin in a configurable pricing service.
3. Origin store and fulfillment mode modelled as separate fields (as the frontend does).
4. Phone-number OTP auth as default, guest checkout allowed.
5. Server-side cart for logged-in users; guest cart via token; `recent` stays client-side.
6. Mock data seeded into the DB so demo and API match; frontend demo mode retained via a data-access layer.

## 5. Recommended implementation order (after your approval)
| Phase | Scope | Why this order |
|---|---|---|
| 1 | Django project foundation: settings, Docker/env, DRF, OpenAPI, auth skeleton, CI, tests | unblocks everything |
| 2 | Catalog domain: Category, Brand, Source, Product, Variant, Image, import of the 16 mock products, `/categories`, `/brands`, `/products`, `/products/{id}`, `/config/storefront`; Persian search | read-only API, lowest risk, covers 80% of screens |
| 3 | Pricing engine (FX rates, margins, rounding, discount) + admin for rates | needs your business rules; isolates risk |
| 4 | Frontend integration layer behind `VITE_DATA_MODE` (mock/api) — **requires your approval to touch the frontend** | proves contract |
| 5 | Auth (OTP), profile, addresses | prerequisite for orders & wishlist |
| 6 | Wishlist and cart (server-priced) | |
| 7 | Checkout quote, orders, statuses | |
| 8 | Payment gateway integration | needs gateway decision |
| 9 | Admin/back-office: product import from sources, stock, order & proxy-purchase workflow | operational core |
| 10 | AI assistant (grounded in catalog) | after catalog + search are stable |
| 11 | Reviews, newsletter, i18n content, returns/refunds | |
| 12 | Hardening: rate limiting, monitoring, backups, security review, performance | before launch |

**Waiting for your approval and answers to §3 (priority: pricing formula, import method, payments, auth) before starting Phase 1.**
