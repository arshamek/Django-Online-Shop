# Order state machine

Source of truth in code: `maral_backend/apps/orders/state.py`. Only `services.transitions.transition()` writes
`Order.status` (the model refuses any other status write); every call validates the edge, applies side effects and
appends an `OrderStatusHistory` row (from, to, actor type, actor, reason, metadata) in one transaction.

```
DIRECT_INVENTORY
  PENDING_PAYMENT → PAID → PROCESSING → READY_TO_SHIP → SHIPPED → DELIVERED

PROXY_PURCHASE
  PENDING_PAYMENT → PAID → PROCESSING → PURCHASE_PENDING → PURCHASED → IN_TRANSIT
                  → [CUSTOMS_PROCESSING] → READY_TO_SHIP → SHIPPED → DELIVERED

Cancellation   PENDING_PAYMENT, PAID, PROCESSING → CANCELLED      (customer or staff)
               PURCHASE_PENDING → CANCELLED                       (staff only: source out of stock / price changed)
Money back     CANCELLED (money captured) | DELIVERED | PARTIALLY_REFUNDED → REFUND_PENDING
               REFUND_PENDING → PARTIALLY_REFUNDED | REFUNDED | back to where it was (refund rejected)
               PARTIALLY_REFUNDED → REFUND_PENDING | REFUNDED          REFUNDED is terminal
```

## Allowed transitions

| From | To |
|---|---|
| PENDING_PAYMENT | PAID, CANCELLED |
| PAID | PROCESSING, CANCELLED |
| PROCESSING | PURCHASE_PENDING *(proxy)*, READY_TO_SHIP *(direct)*, CANCELLED |
| PURCHASE_PENDING *(proxy)* | PURCHASED, CANCELLED |
| PURCHASED *(proxy)* | IN_TRANSIT |
| IN_TRANSIT *(proxy)* | CUSTOMS_PROCESSING, READY_TO_SHIP |
| CUSTOMS_PROCESSING *(proxy)* | READY_TO_SHIP |
| READY_TO_SHIP | SHIPPED |
| SHIPPED | DELIVERED |
| DELIVERED | REFUND_PENDING |
| CANCELLED | REFUND_PENDING *(only if money was captured)* |
| REFUND_PENDING | PARTIALLY_REFUNDED, REFUNDED, DELIVERED / CANCELLED *(refund rejected: back to the status before)* |
| PARTIALLY_REFUNDED | REFUND_PENDING, REFUNDED |
| REFUNDED | — |

Everything else is refused with `409 invalid_transition` and leaves no trace. Tests walk the whole table,
prove every status is reachable per order type, and try the forbidden jumps.

## Who may do what

* **Payment provider (verified webhook)**: `PENDING_PAYMENT → PAID`. Nobody else — not staff, not the browser.
* **System (tasks)**: `PENDING_PAYMENT → CANCELLED` when the payment window ends (`payment_timeout`).
* **Customer**: cancel while `PENDING_PAYMENT`, `PAID`, `PROCESSING`; request a return refund after delivery.
* **Order Manager (staff)**: the operational path (`PROCESSING … DELIVERED`, cancel). `PURCHASED` requires the source
  store's order reference; `SHIPPED` records carrier + tracking number. Refund statuses and `PAID` are refused here.
* **Finance Manager (staff)**: refunds only (approve / reject / complete / execute). Separation of duties: whoever
  moves parcels cannot release money.

## Side effects

| Transition | Effect |
|---|---|
| → PAID | `paid_at`; direct: reservation committed (`on_hand`↓, `reserved`↓); proxy: procurement `queued` |
| → PURCHASED | procurement `purchased` (+ source order ref, time, buyer) |
| → SHIPPED / DELIVERED | `shipped_at` + tracking / `delivered_at` (starts the return window) |
| → CANCELLED from PENDING_PAYMENT | reservation released; nothing to refund |
| → CANCELLED after payment | direct: stock returned to the shelf; proxy: procurement cancelled / source_unavailable; a **full refund** is opened and the order moves on to REFUND_PENDING (two history rows) |
| payment captured for an already cancelled order | never resurrects the order; a `late_payment` refund is opened |

## Cancellation and return rules (all values configurable, see `PHASE7_DECISIONS.md`)

* Customer cancel: statuses `PENDING_PAYMENT`, `PAID`, `PROCESSING` → else `422 cancellation_not_allowed`.
* Return refund: order `DELIVERED`/`PARTIALLY_REFUNDED`, within `ORDER_RETURN_WINDOW_DAYS` of `delivered_at`, items
  not in `ORDER_NON_RETURNABLE_CATEGORY_SLUGS`, quantity ≤ not-yet-refunded quantity, one open refund at a time.
* The customer says **which items and why**; the server computes the amount from the order snapshot:
  `(unit price + unit separate charges) × qty` plus the matching share of the delivery fee (prorated by line price,
  exact to the rial) — so returning everything refunds exactly the order total and `REFUNDED` is reached.
* A rejected refund returns the order to its previous status; cancellation/late-payment refunds cannot be rejected.
* Completing a refund never exceeds the order total (also a DB CHECK). Physically restocking returned goods is a
  manual staff decision (not automated).


## Phase 8 additions
* **Shipments drive delivery.** A shipment moving to `in_transit` moves a `READY_TO_SHIP` order to `SHIPPED`; when every non-cancelled shipment is `delivered` the order moves to `DELIVERED`. Marking the order `SHIPPED` / `DELIVERED` directly (the Phase 7 way) creates / updates the shipment, so there is never a second shipment.
* **Tracking rule.** If the order's shipping method has `requires_tracking`, `READY_TO_SHIP → SHIPPED` fails with `tracking_required` unless a tracking number is supplied.
* **Payment expiry** now verifies each live payment with its provider first: a payment the provider reports as paid is captured and its orders move to `PAID` instead of being cancelled.
* Details: `docs/shipping/SHIPPING_AND_INTERNATIONAL.md`, `docs/payments/PHASE8_PAYMENTS.md`.
