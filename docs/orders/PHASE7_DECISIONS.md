# Phase 7 — decisions, open questions and honest limits

Three separate lists, as in Phase 6. Nothing in B was silently decided as a business fact: each item has a
**temporary default** so the system works, and that default is a setting or a data row you can change.

## A. Technical decisions (already made, no owner input needed)

| # | Decision | Plain-language reason |
|---|---|---|
| A1 | One checkout creates one order per fulfilment type | Stock items and order-on-demand items follow different steps and take different times. Mixing them in one order would make the status meaningless |
| A2 | The customer pays once per checkout; the delivery fee is split between the orders to the rial | One payment screen, no double delivery charge, no lost rial |
| A3 | The price a customer accepts is a server-built *quote*; cart lines store no price | A browser (or an old cart) can never set or keep a stale price |
| A4 | Preview = the real checkout calculation, rolled back | Preview and real numbers can never disagree |
| A5 | Order creation is one database transaction with an `Idempotency-Key` stored inside it | Pressing the button twice, or a network retry, gives the same order, never two |
| A6 | Stock is reserved with one conditional database update | Two customers cannot both get the last unit, even at the same millisecond |
| A7 | Guest cart ids are random 256-bit tokens issued by the server, stored hashed | Not guessable, not chosen by the client, useless if the database leaks |
| A8 | Order status has exactly one writer (the state machine); history is append-only | Every change has who/when/why and cannot be forged |
| A9 | Payment becomes "captured" only from a signature-verified provider event; wrong amounts are recorded but never applied | The browser's "I paid" is never believed |
| A10 | Refund amounts are computed from the order snapshot, never accepted from the client | A customer cannot ask for more than they paid |
| A11 | Customers see the price build-up only as far as `customer_breakdown` allows (default: none) | Respects the Phase 6 decision about hiding costs |
| A12 | Staff roles: Order Manager moves orders, Finance Manager releases money, Product Manager edits stock | Separation of duties using the roles that already existed |

## B. Business decisions that need the owner (temporary defaults in brackets)

| # | Question | Options | Temporary default |
|---|---|---|---|
| B1 **[Q-guest]** | May people buy **without an account**? | (a) account required; (b) guest checkout with phone/OTP verification | (a) account required. Carts and the merge-after-login already support guests browsing |
| B2 | How long does a customer have to pay, and how long is stock held? | any number of minutes | 30 minutes (`ORDER_PAYMENT_WINDOW_MINUTES`) |
| B3 | Delivery fees and free-delivery threshold | per method: fee, "free over", cities | None in production. `load_demo_shipping` creates the frontend demo values (450,000 / free over 20,000,000 / express 950,000 Toman) **flagged as estimates**. With no method configured the cart says "shipping not configured" — never 0 |
| B4 | Is the delivery fee refunded on a return? | never / always / proportional | proportional to the returned goods (a full return refunds the full fee) |
| B5 | Return window and exclusions | days; categories (e.g. underwear, cosmetics) | 14 days, no category excluded (`ORDER_RETURN_WINDOW_DAYS`, `ORDER_NON_RETURNABLE_CATEGORY_SLUGS`) |
| B6 | Can a customer cancel an order that is already paid? | until packing/purchase / never / staff only | until packing (direct) or purchasing starts (proxy): `PENDING_PAYMENT`, `PAID`, `PROCESSING` |
| B7 | When the source store's price rises before we buy | contact customer / absorb up to X% / auto-cancel+refund | recorded in each order line as `hold_and_contact_customer`; staff can cancel at `PURCHASE_PENDING` and the customer is refunded in full |
| B8 | A proxy product whose source availability is *unknown* | block / allow | block (only `in_stock` can be ordered) |
| B9 | Taxes (VAT / duties beyond the customs rules) | rates, who pays | **not modelled**: checkout returns `taxes: not_modeled`; customs estimates come only from Phase 6 rules |
| B10 | Coupons / order-level discounts | — | not implemented. "Discount" today = list price minus accepted price, informational |
| B11 | Customer-visible status wording (fa/en) | — | provisional labels in `apps/orders/labels.py` |
| B12 | Maximum quantity per line / lines per cart | numbers | 99 / 50 (`CART_MAX_QUANTITY_PER_LINE`, `CART_MAX_LINES`) |
| B13 | Guest wishlist (the frontend keeps one in the browser) | merge on login / sign-in only | sign-in only; nothing is merged |
| B14 | Online payment only, or also cash-on-delivery / bank transfer? | — | online provider interface only |

## C. External information needed later

| # | Needed | From |
|---|---|---|
| C1 | A real payment gateway: contract, merchant credentials, callback format, refund API, signature scheme | payment provider (then implement `PaymentProvider`; nothing else changes) |
| C2 | Domestic courier tariffs, tracking API, city coverage | courier / Iranian post |
| C3 | Official customs/VAT rules for imported goods | customs broker / accountant |
| C4 | SMS/email provider for order notifications | provider contract |

## D. Honest limits — what is *not* proven

1. **SQL Server was not run.** Tests ran on SQLite. Constraints and locking were written within the documented
   mssql-django limits (no `select_for_update(of=…)`, no joins under row locks, filtered unique indexes). The Phase 1
   spike **T8** (100 concurrent workers, zero oversell, deadlock behaviour) is still required before production.
2. **No real concurrency test.** "Last unit goes to one buyer", idempotent replay and double submission are proven
   sequentially; the guarantees come from database constraints/conditional updates, which only a multi-process
   test on SQL Server can confirm.
3. **The payment provider is a fake** (development/tests only; the production settings module refuses to start with
   it enabled). Refund *execution* with a provider works only for providers that implement `refund`; otherwise finance
   completes the refund manually after returning the money.
4. **No shipments/courier integration**: tracking is carrier + number typed by staff.
5. **No notifications** (order confirmation, status e-mail/SMS).
6. Returned goods are **not automatically restocked**.
7. The frontend still uses its own browser cart and client-made order code until it is wired to these APIs
   (a separate, approved frontend step).
8. Existing demo catalogue data has no stock rows: run `python manage.py seed_demo_inventory` (DEBUG only) to try
   direct orders, and `load_demo_shipping` for delivery methods.
