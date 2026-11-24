# Phase 7 — Cart, Wishlist, Checkout, Orders, Stock, Payments (overview)

Status: implemented and tested on SQLite. **Not yet run on SQL Server** and **no real payment gateway** (see
`PHASE7_DECISIONS.md` §D). The frontend was not touched.

## 1. What was built

| App | Responsibility |
|---|---|
| `cart` | Guest + customer carts, variant/size/colour selection, availability, server-side totals, merge after login |
| `wishlist` | Customer-only product wishlist, status checks, move to cart |
| `inventory` | Warehouses, stock per variant, reservations, append-only movements, catalogue availability sync |
| `shipping` | Domestic delivery methods (`ShippingMethod`): fee, free-over threshold, countries. Data, not code |
| `orders` | Checkout, orders, immutable price snapshots, state machine, history, cancellation, refunds/returns, idempotency, staff API |
| `payments` | Payment initiation, provider interface, signed webhook, verified capture. Fake provider for dev/tests only |

## 2. The flow, step by step

```
cart ──► POST /checkout/preview   (nothing saved; same numbers as the real thing)
     ──► POST /checkout           1 validate cart  2 availability  3 variants  4 address (ownership)
                                  5 delivery fee  6 customs estimate  7 authoritative price QUOTE (expires)
                                  8 summary  -> Checkout row (frozen quote + address + method + totals)
     ──► POST /orders             Idempotency-Key required. ONE transaction: lock checkout+cart, re-validate
                                  everything, lock the quote, reserve stock, write orders + snapshots + history,
                                  close checkout, spend the cart.            (step 9)
     ──► payment initiated        amount = server sum of the unpaid orders   (step 10)
     ──► provider webhook         signature verified -> PAID, stock committed
```

* **One checkout → one order per fulfilment type.** A cart with stock items and order-on-demand items becomes two
  orders (`DIRECT_INVENTORY`, `PROXY_PURCHASE`) because they have different state machines, suppliers and delivery
  times. The customer pays once for the checkout. The delivery fee is split between the orders with a
  largest-remainder allocation, so the parts always add up to the checkout total to the rial.
* **Nothing the client sends is a price.** Inputs are: which saved address, which delivery method, which display
  currency (and quantities/variants in the cart). Extra fields (`total`, `unit_price`, `status`…) are dropped by the
  serializers. The client may only *confirm* the total it saw (`accepted_total_irr`); a mismatch is `409 total_mismatch`.
* **Cart lines store no price.** The cart shows catalogue prices recomputed on each request (`price_basis:
  storefront_estimate`). The binding price is the `PriceQuote` built at checkout by the Phase 6 engine.
* **Stock**: reservation is a conditional `UPDATE … WHERE on_hand >= reserved + safety + qty` (no read-then-write).
  A direct order holds stock for `ORDER_PAYMENT_WINDOW_MINUTES`; payment commits it; timeout/cancel releases it;
  cancel-after-payment returns it to the shelf. Proxy orders never touch internal stock.
* **Availability rule** — direct variant: internal stock; proxy variant: last observed source availability must be
  `in_stock` (`unknown` blocks ordering, see decisions).

## 3. API (all under `/api/v1/`)

Customer (session cookie + CSRF, like the rest of the API):

| Method & path | Purpose |
|---|---|
| `POST /cart` | create/fetch cart; guests get a one-time `guest_token` |
| `GET /cart` · `DELETE /cart` | retrieve (recalculated) · clear |
| `POST /cart/items` | add `{variant_id}` or `{product_id, size, color}` + `quantity` |
| `PATCH /cart/items/{line_id}` | change `quantity` and/or select another `size`/`color`/`variant_id` |
| `DELETE /cart/items/{line_id}` | remove line |
| `POST /cart/validate` | re-check availability of every line |
| `POST /cart/merge` | after login: merge guest cart (`X-Guest-Token` header) into the customer cart |
| `GET /wishlist` · `GET /wishlist/status?ids=` | list · bulk status |
| `GET/PUT/DELETE /wishlist/items/{product_id}` | status · add (idempotent) · remove (idempotent) |
| `POST /wishlist/items/{product_id}/move-to-cart` | `{variant_id}` or `{size,color}`, `quantity`, `keep_in_wishlist` |
| `POST /checkout/preview` · `POST /checkout` · `GET /checkout/{id}` | `{address_id, shipping_method, currency}` |
| `POST /checkout/{id}/payment` | start/resume payment (`Idempotency-Key`) |
| `POST /orders` | `{checkout_id, accepted_total_irr?, initiate_payment?}` + `Idempotency-Key` |
| `GET /orders` (`?status=&order_type=`) · `GET /orders/{id or MRL-code}` | list · detail with price snapshot |
| `GET /orders/{ref}/tracking` · `/history` | progress steps + estimate · status history |
| `POST /orders/{ref}/cancel` | request cancellation (rules in `ORDER_STATE_MACHINE.md`) |
| `GET/POST /orders/{ref}/refunds` | list · request a return refund `{items:[{order_item_id, quantity}], reason}` |
| `GET /payments/{id}` | payment status (owner only) |

Guest cart identity: header `X-Guest-Token`. The token is 256-bit random, issued by the server, shown once, stored
only as SHA-256. Guest carts expire after `CART_GUEST_TTL_DAYS` of inactivity (daily purge task).

Provider/staff:

| Path | Permission |
|---|---|
| `POST /webhooks/payments/{provider}` | provider signature (no session) |
| `GET /staff/orders[/{id}]`, `POST /staff/orders/{id}/transition` | `orders_view` / `orders_manage` (Order Manager) |
| `GET /staff/refunds`, `POST /staff/orders/{id}/refunds`, `POST /staff/refunds/{id}/approve｜reject｜complete｜execute` | `refunds_manage` (Finance Manager) |
| `POST /staff/inventory/receive｜adjust` | `catalog_manage` (Product Manager) |

Ownership: every customer query filters by `request.user`; somebody else's id answers `404`, exactly like a missing one.

## 4. Price snapshots

`OrderItem` stores, immutably: product title, brand, SKU, size/colour (code + name), quantity, fulfilment type,
source + external product/variant ids + URL (proxy), purchase requirements, expected delivery window, original
price + currency, exchange rate + source + time + manual flag, list price, accepted selling price, discount, fees,
international freight and customs components, separate charges, line totals, pricing rule version, and a link to the
frozen `PriceQuoteItem`. `Order` stores customer + address snapshots, delivery method, totals. Models refuse
updates/deletes (only lifecycle fields of `Order` may change; `OrderItem`, `OrderStatusHistory`,
`InventoryMovement`, `PaymentTransaction` are append-only). Database CHECK constraints guarantee
`line = unit × qty`, `order total = items + separate + shipping`, `checkout total = same`.

What customers see of this is governed by the Phase 6 setting `customer_breakdown` (none by default): source price,
rate and fees/freight/customs are hidden unless the owner enables them. Profit and internal cost are never exposed.

## 5. Background tasks (Celery beat)

`orders.expire_unpaid` (1 min: cancel unpaid orders past the window, release stock, expire payments),
`orders.expire_checkouts` (5 min), `inventory.expire_reservations` (5 min safety net), `cart.purge_stale` (daily).
All are idempotent.

## 6. Deviations from the approved Phase 1 design (please review)

| Design said | Built | Why |
|---|---|---|
| `Order.price_quote_id` unique | not unique | a checkout can create two orders from one quote |
| `Order.payment_status`, `Order.idempotency_key` | omitted; `IdempotencyRecord` table; `Payment` has its own status | single status field as the brief asks; idempotency written in the same transaction as the order |
| `Order.user_id` nullable for guest checkout [Q-guest] | required | guest checkout not approved (see decisions) |
| `OrderItem.status` | order-level status only | the brief defines one order state machine |
| Payment belongs to an order | payment belongs to the **checkout** | one payment for two orders |

## 7. Mapping to the existing frontend (no frontend change was made)

| Frontend today | Backend |
|---|---|
| `shop-store` cart in `localStorage` keyed by `id+size+color`, colour/size as display names | `POST/PATCH /cart/items` with option **codes** (`size`, `color` from the product API). Frontend must send codes and use `line_id` |
| `CartDrawer` totals, shipping 450,000 / free over 20M Toman (demo constants) | `GET /cart` totals; delivery fee comes from `ShippingMethod` rows (`load_demo_shipping` creates the demo values flagged *estimated*) |
| Product page add-to-cart, size/colour pickers | `POST /cart/items` `{product_id,size,color}` |
| Wishlist (`toggleWish`, ids in `localStorage`) | `PUT/DELETE /wishlist/items/{product_id}`, `GET /wishlist/status`; sign-in required; guest wishlists are not merged |
| Checkout steps: address → delivery (standard / express 950,000) → payment → confirm | `POST /checkout/preview` per step, `POST /checkout`, then `POST /orders` |
| Order code `MRL-` + `Math.random()` generated in the browser, `clearCart()` on step 4 | The server issues `MRL-######`; the browser must show the code from the `POST /orders` response |
| Account → orders | `GET /orders`, `/orders/{ref}`, `/tracking`, `/cancel`, `/refunds` |

Frontend note: any number the browser computes (totals, shipping, status) is display-only. The server recalculates
and decides every financial and status outcome.
