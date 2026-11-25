# Phase 8 — Payment architecture

> **Status: framework + mock providers. No real payment gateway is implemented or claimed to work.** A real provider
> counts as working only after an authorised sandbox account has completed the full flow
> (see `PROVIDER_INTEGRATION_GUIDE.md`).

## 1. Building blocks (all under `apps/payments/`)

| Piece | File | Role |
|---|---|---|
| `BasePaymentProvider` | `providers/base.py` | The contract every gateway adapter implements: `initiate`, `parse_callback`, `verify`, optional `refund` / `cancel`, plus a `ProviderCapabilities` declaration (kind, currencies, refund / partial refund / server verification support) |
| `IranianGatewayProvider` | `providers/iranian.py` | Adapter family: IRR only, browser redirect + return, **unsigned return that must be verified server-to-server**, refunds usually manual |
| `InternationalProvider` | `providers/international.py` | Adapter family: multi-currency hosted checkout session, **signed + replay-protected webhooks** (HMAC helper included), partial refunds |
| Mock providers | `providers/mock.py`, `providers/fake.py` | `mock_ir`, `mock_intl`, `fake` — development/tests only, gated by `PAYMENTS_ALLOW_FAKE_PROVIDER` (refused in production) |
| Registry | `providers/registry.py` | Which adapters exist; real ones are added by dotted path in `PAYMENT_PROVIDER_ADAPTERS` |
| Method resolution | `methods.py` | Which providers a customer may use and **exactly what would be charged** (currency, amount, FX rate) |
| Workflow | `services.py` | initiate / callbacks / capture / fail / verify / cancel / expire |
| Refunds | `refunds.py` | Provider refunds, idempotent, capped, partial |
| Reconciliation | `reconciliation.py`, `tasks.py` | Compare our payments with the provider's answers |
| Secure HTTP | `providers/client.py` | https-only, timeouts, no redirects, bounded size, no secret logging — for real adapters |

## 2. Eligibility is data, not an assumption

A real adapter is usable only when **all** of these hold (`methods.unavailable_reason`):

1. the adapter class is installed (`PAYMENT_PROVIDER_ADAPTERS`),
2. an administrator created its `PaymentProviderConfig` and set `is_enabled`,
3. `eligibility_status == verified` **with written evidence** in `eligibility_notes` (who confirmed what, with which document or ticket),
4. in `live` mode, a recorded `sandbox_verified_at`,
5. the customer's country is allowed (`customer_countries`), the amount is within limits, the currency is supported (adapter ∩ config), and a usable (non-stale) exchange rate exists.

The staff API refuses to enable a provider that does not meet 3–4. Mocks need no config (an explicit disable still wins).
The code makes **no claim** about which providers accept which merchants: that must be established per provider and per
legal entity (checklist in the integration guide).

## 3. The ten-step workflow

| # | Step | Where / how |
|---|---|---|
| 1 | Initiation | `POST /checkout/{id}/payment` (or inline with `POST /orders`) with `Idempotency-Key` and optional `{provider}` |
| 2 | Secure request | The **server** builds a `PaymentRequest`: amount = sum of order totals, charge currency + frozen FX rate, our `merchant_ref`, server-built callback URL, no card data, no client-supplied URLs. Credentials come from `PAYMENT_PROVIDER_CREDENTIALS` (environment) only |
| 3 | Transaction creation | A `Payment` row (`initiated`) is committed first, **then** the provider is called outside any lock; an append-only `PaymentTransaction` ledger records every event |
| 4 | Redirect / session | Provider answers with `provider_ref` + `redirect_url`; payment becomes `pending`. A provider outage marks it `failed(provider_unavailable)` and returns 503 `payment_provider_error` |
| 5 | Callback verification | `POST /webhooks/payments/{provider}` (signed webhooks) and `GET/POST /payments/callback/{provider}` (browser return). Signatures + timestamp tolerance are checked; **untrusted returns are only a hint** — the service calls the provider's verify API and applies *that* answer |
| 6 | Transaction verification | `services.verify_payment` (also staff endpoint, reconciliation, expiry, cancel) — the provider's server-side answer is the only thing that can capture |
| 7 | Success | `capture`: amount **and** currency must equal what the server asked for; orders become `PAID` through the order state machine |
| 8 | Failure | `fail`: payment `failed`, orders stay `PENDING_PAYMENT` and payable; a retry creates a new attempt |
| 9 | Expiration | `expire_due` (called first by `lifecycle.expire_unpaid`): each live payment past its window is **verified first** — if the provider says paid it is captured, not expired; otherwise `expired` + ledger row. Money that arrives after cancellation opens a `late_payment` refund (Phase 7 behaviour, unchanged) |
| 10 | Refunds | `orders.refunds.execute_with_provider` → `payments.refunds.execute` (see §5) |

The browser never decides: `PaymentReturnView` redirects to `FRONTEND_BASE_URL + PAYMENTS_RESULT_PATH?payment=<id>` **without any status**;
the storefront must read `GET /payments/{id}`.

## 4. Safety properties and where they are tested

| Requirement | Implementation | Test |
|---|---|---|
| Idempotency keys | `(user, Idempotency-Key)` unique on payments; refunds unique per `orders.Refund` + provider-side key | `test_idempotency_key_replays_...`, `test_duplicate_refund_...` |
| Callback signature verification | `international.verify_signature` (HMAC-SHA256 over `ts.body`, constant-time, ±300 s) | `test_invalid_signature_is_rejected_...` (bad / missing / wrong secret / replayed old / malformed) |
| Transaction reference validation | every callback must echo `merchant_ref` (payment public id); mismatch → rejected + payment flagged | `test_callback_for_another_payments_reference_...` |
| Duplicate callbacks | ledger unique `(payment, kind, event_id)`; already-captured → `duplicate` | `test_duplicate_callback_is_applied_once` |
| Payment attempt history | one `Payment` per attempt + ledger + `ProviderCallback` receipts (also for rejected callbacks); customer view `GET /orders/{ref}/payments` | `test_payment_history_of_an_order_...` |
| Reconciliation | `reconcile()` — recovers lost webhooks, recovers late captures, **flags (never reverses)** captures the provider does not confirm; every run stored | `test_reconciliation_*` |
| Partial refunds | cap check under row lock; proportional charge share, last slice takes the remainder (no rounding dust) | `test_partial_refunds_add_up_exactly` |
| Audit logging | `audit.record` for initiated / captured / failed / verified / refunded / flagged / cancelled / provider config changes | `test_audit_trail_covers_the_payment_lifecycle` |
| No raw card data | no card field exists anywhere; provider payloads are stored **recursively redacted** (keys like `card`, `token`, `api_key`, `signature`...) | `test_provider_payloads_are_stored_without_secrets_...` |
| Credentials server-side | environment only (`PAYMENT_PROVIDER_CREDENTIALS`); config model/API has no credential field | `test_provider_configuration_never_contains_or_accepts_credentials` |

## 5. Refunds

* One `PaymentRefund` per `orders.Refund` (DB-unique). Executing twice is a `409 refund_already_completed`; a crash
  between "provider refunded" and "order refund completed" is recovered **without a second provider request**.
* Cap: `Σ(pending + succeeded + manual) ≤ captured`. Providers that cannot refund partially are refused for partial amounts.
* Provider refuses → `failed` row + `refund_failed` ledger entry, order refund stays `approved` (retryable). Provider unreachable → row stays
  `pending`; retry reuses the same idempotency key.
* Providers without automated refunds (`supports_refund=False`, typical for Iranian PSPs): staff refund manually and call
  `complete`; the amount is still booked on the payment as a `manual` `PaymentRefund`.
* Foreign-currency payments are refunded in the original charge currency, proportionally. Differences caused by the provider's own FX/fees are not modelled.

## 6. Operations

* Celery beat: `payments-reconcile` every `PAYMENTS_RECONCILE_INTERVAL_MINUTES` (default 15). Manual: `python manage.py reconcile_payments`.
* Dev helper: `python manage.py simulate_payment <payment-id> captured|failed` (mock providers only), then hit the return URL or run reconciliation.
* Review queue: `GET /api/v1/staff/payments?problem=1` (failed, expired, cancelled or flagged), detail with ledger + callbacks, `…/verify`, `…/resolve-review` (note required).
