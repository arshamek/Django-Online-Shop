# Phase 8 API — payments, shipping, shipments (all under `/api/v1`)

Conventions as in `API_CONVENTIONS.md` (error envelope `{"error": {"code", "message", "fields"}}`, session auth, CSRF on unsafe methods).
The generated OpenAPI schema (`/api/schema/`, when docs are enabled) lists every field; this page is the behavioural summary.

## Customer — payments
| Method & path | Auth | Notes |
|---|---|---|
| `GET /payments/methods?checkout_id=` | customer | Usable methods for the checkout's unpaid orders: `key`, `kind`, localized `name`, `charge {amount, currency}`, `is_foreign_currency`, `exchange_rate`, `is_test_method`. Methods that are disabled, unverified, country-restricted, out of limits or without a fresh exchange rate are absent |
| `POST /checkout/{id}/payment` | customer | Header `Idempotency-Key` (required). Body `{ "provider": "<key>" }` optional. 201 → payment (below). Errors: `payment_method_*` (422), `payment_in_progress` / `idempotency_conflict` / `nothing_to_pay` (409), `payment_provider_error` (503) |
| `POST /orders` | customer | Existing; new optional `payment_provider` |
| `GET /payments/{id}` | owner | Status. **Server record only.** `{id, provider, status, amount_irr, charge{amount,currency}, exchange_rate, redirect_url (only while payable), expires_at, captured_at, refunded_irr, failure_code}` |
| `POST /payments/{id}/cancel` | owner | Abandon a payment in progress (verifies with the provider first; `409 payment_already_captured` if the customer actually paid) |
| `GET /orders/{ref}/payments` | owner | Attempt history newest first with each attempt's events and completed refunds. No provider references |
| `GET\|POST /payments/callback/{provider}` | none | Browser return. Never trusted; verified server-side; 302 to `FRONTEND_BASE_URL + PAYMENTS_RESULT_PATH?payment=<id>` (**no status in the URL**) or `?error=` |
| `POST /webhooks/payments/{provider}` | provider signature | `200 {"status": "applied"\|"duplicate"\|"ignored"}` or `400 {"status":"rejected"}` |

Payment `status`: `initiated`, `pending`, `captured`, `failed`, `expired`, `cancelled`. Only the server moves it, and only from a verified provider answer.

## Customer — shipping
| Method & path | Notes |
|---|---|
| `GET /shipping/methods?country=&region=&city=&postal_code=` and `POST /shipping/estimate` (same fields in JSON) | Methods and fees for a destination, using the caller's cart when there is one (`basket_known`). `methods[]`: `code, label, kind, carrier, description, fee{amount,currency}, is_free, fee_status, delivery{status,min_days,max_days,unit}`; `unavailable[]` (`method`, `reason`: `weight_required`, `overweight`, `restricted_items`, `country_not_shippable`, `provider_error`); `supported`; `customs` (status + notices, **never an amount**); `policy_notes`; `address_requirements`; `disclaimers[]` |
| `GET /shipping/countries` | Countries with at least one active priced method, minus blocked countries |
| `GET /shipping/address-requirements?country=XX` | Required/optional fields, postal pattern; Iranian provinces for `IR` |
| `GET /orders/{ref}/shipments` | `delivery` (what was promised at checkout) + shipments with carrier, `tracking_number`, `tracking_url`, status, customer-safe history |
| `GET /orders/{ref}/tracking` | Existing; now also lists `shipments` |
| `GET /checkout/{id}`, `POST /checkout/preview` | Existing; new `delivery` and `customs` blocks (statuses in `SHIPPING_AND_INTERNATIONAL.md` §5) |

## Staff — payments (permissions: read `payments_view`, write `payments_manage`)
`GET /staff/payment-providers`, `POST` (create), `GET|PATCH /staff/payment-providers/{key}` — enabling rules in `PHASE8_PAYMENTS.md` §2; no credential fields; no DELETE.
`GET /staff/payments?status=&provider=&problem=1&needs_review=1`, `GET /staff/payments/{id}` (ledger, refunds, callback receipts), `POST …/verify`, `POST …/resolve-review {note}`,
`GET|POST /staff/payments/reconciliation`. Refunds: existing `POST /staff/refunds/{id}/execute` (`refunds_manage`) now goes through the idempotent provider refund.

## Staff — shipping (read `orders_view`; write `shipping_manage` / `shipments_manage`)
Configuration (`shipping_manage`): `/staff/shipping/zones`, `/staff/shipping/methods` (by `code`), `/staff/shipping/rates`, `/staff/shipping/country-policies` (by ISO code) — list/create/get/patch; rates can also be deleted; zones/methods/policies are deactivated, not deleted.
Shipments (`shipments_manage`): `GET /staff/shipments?status=&open=1&order=CODE`, `GET /staff/shipments/{id}`, `GET|POST /staff/orders/{order_id}/shipments` (`carrier`, `tracking_number`, `use_provider`), `POST /staff/shipments/{id}/status {status, description, location, occurred_at}`, `POST …/tracking {carrier, tracking_number}`, `POST …/sync`.

## New error codes
`payment_method_disabled | eligibility_not_verified | sandbox_not_verified | country_not_supported | amount_below_minimum | amount_above_maximum | no_currency`, `payment_rate_unavailable`, `payment_rate_stale`, `payment_amount_too_small`, `payment_in_progress`, `payment_provider_error`, `payment_not_cancellable`, `payment_already_captured`, `verification_not_supported`, `provider_unreachable`, `refund_already_completed`, `refund_exceeds_payment`, `partial_refund_not_supported`, `manual_refund_required`, `provider_refund_failed`, `shipping_weight_required`, `shipping_overweight`, `shipping_restricted_items`, `shipping_country_not_shippable`, `shipping_method_unavailable` (with `fields.destination=["unsupported_destination"]` when no method serves the destination), `address_incomplete`, `tracking_required`, `invalid_tracking_number`, `invalid_shipment_transition`, `order_not_shippable`, `carrier_unreachable`, `tracking_sync_not_supported`.
