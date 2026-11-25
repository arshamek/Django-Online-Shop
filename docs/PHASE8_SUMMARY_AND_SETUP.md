# Phase 8 — implementation summary and setup

See `payments/PHASE8_PAYMENTS.md`, `payments/PROVIDER_INTEGRATION_GUIDE.md`, `shipping/SHIPPING_AND_INTERNATIONAL.md`,
`api/PHASE8_PAYMENTS_SHIPPING_API.md` and `PHASE8_DECISIONS_AND_LIMITS.md` for details.

## What changed
* `apps/payments`: `BasePaymentProvider` + Iranian/international families, mock providers, provider registry, config model with enforced eligibility, charge-currency resolution, verified callbacks, duplicate/replay protection, payment expiry with verification, cancel, idempotent + partial refunds, reconciliation, callback receipts, staff API, admin.
* `apps/shipping`: zones, rates, country policies, `BaseShippingProvider` (+ table-rate and mock carrier), shipments with append-only history and a status machine, tracking rules, address requirements and Persian normalisation, international customs/delivery information, customer + staff APIs.
* `apps/orders`: payment provider choice, order payment/shipment endpoints, `shipping_snapshot`, order ⇄ shipment synchronisation, refund execution made idempotent, expiry verifies with the provider first.
* `apps/accounts`: permissions `payments_manage`, `shipping_manage`, `shipments_manage` (+ role mapping).
* Migrations: `accounts/0002`, `orders/0002`, `payments/0002` (includes a backfill for Phase 7 payments), `shipping/0002`.

## Setup
```bash
cd maral_backend
python -m venv .venv && . .venv/bin/activate
pip install -r requirements/base.txt          # + dev requirements for tests
cp .env.example .env                          # then edit (SECRET_KEY, DB, ...)
python manage.py migrate                      # roles/permissions sync automatically after migrate
python manage.py check
```
New environment variables (all optional; defaults are safe): `PAYMENT_PROVIDER_ADAPTERS`, `PAYMENT_PROVIDER_CREDENTIALS` (JSON, secret),
`PAYMENTS_CALLBACK_BASE_URL`, `PAYMENTS_RESULT_PATH`, `PAYMENTS_RECONCILE_INTERVAL_MINUTES`, `SHIPPING_PROVIDER_ADAPTERS`,
`SHIPPING_ALLOW_MOCK_CARRIER` (never in production), `SHIPPING_TRACKING_SYNC_MINUTES`. Existing: `PAYMENTS_ALLOW_FAKE_PROVIDER`, `PAYMENTS_FAKE_WEBHOOK_SECRET` (dev only).
Run Celery worker **and beat** so `payments.reconcile` and `shipping.sync_tracking` execute.

### Try it locally with the mocks
```bash
# .env: PAYMENTS_ALLOW_FAKE_PROVIDER=true  SHIPPING_ALLOW_MOCK_CARRIER=true
#       PAYMENT_PROVIDER_CREDENTIALS={"mock_intl": {"webhook_secret": "dev-secret"}}
python manage.py runserver
# customer: GET /api/v1/payments/methods?checkout_id=...  ->  POST /api/v1/checkout/{id}/payment {"provider":"mock_ir"}
python manage.py simulate_payment <payment-id> captured     # pretend the customer paid at the mock gateway
python manage.py reconcile_payments                          # or open the return URL: the payment is applied after server-side verification
```
Shipping: create a zone, a method and a rate with the staff API (or Django admin), then `GET /api/v1/shipping/methods?country=DE`.

### Tests
```bash
cd maral_backend && TEST_DATABASE=sqlite pytest -q            # smoke mode used in CI/sandbox
TEST_DATABASE=sqlite pytest -q tests/test_payment_providers.py tests/test_payment_refunds.py tests/test_payment_admin.py \
  tests/test_shipping_zones.py tests/test_shipments.py tests/test_international_checkout.py tests/test_phase8_settings.py
```
Result at hand-over: **934 passed, 8 failed** — the same 8 pre-existing failures as the uploaded project (see `PHASE8_DECISIONS_AND_LIMITS.md` C9). Phase 7 tests: unchanged and green. Phase 8 added 111 tests.

## Going live checklist
1. Answer B1–B12 in `PHASE8_DECISIONS_AND_LIMITS.md`. 2. Follow `payments/PROVIDER_INTEGRATION_GUIDE.md` for each provider (eligibility → adapter → sandbox → enable).
3. Enter real tariffs, zones, country policies, delivery ranges; set `requires_tracking`. 4. Set `PAYMENTS_CALLBACK_BASE_URL` to the public https URL and configure the provider's webhook to `/api/v1/webhooks/payments/<key>`.
5. Wire the storefront checkout to the new endpoints. 6. Run the suite on SQL Server.
