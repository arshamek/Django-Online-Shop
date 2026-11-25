# Adding a real payment provider — checklist and procedure

**Do not write an adapter before section 1 is answered in writing.** A provider's documentation page is not proof that
*your* merchant account can use it.

## 1. Eligibility and legal checklist (business work, before any code)

Record the answers (with document / ticket / contract references) in `PaymentProviderConfig.eligibility_notes`,
`settlement_notes` and `legal_notes`.

1. **Merchant eligibility** — does the provider accept a merchant registered in *your* country and legal form? Which documents (business registration, tax id, e-commerce trust enamad for Iranian PSPs, bank account)? Who confirmed this and when?
2. **Customer eligibility** — from which countries / card types / currencies may customers pay? (`customer_countries`, `supported_currencies`)
3. **Currencies and settlement** — which currencies can be charged, into which currency and bank account are funds settled, how fast, with which fees and reserves? (`settlement_currency`)
4. **Legal / compliance** — licences, sanctions and export-control exposure, tax treatment, data-protection and card-scheme rules, chargeback liability. Get qualified legal advice; the code cannot decide this.
5. **Refund and dispute rules** — are refunds supported, partial refunds, time limits, who bears fees?
6. **Sandbox access** — credentials for an authorised sandbox account and its documentation.
7. **Webhook security** — signing scheme, replay protection, source IPs, retry behaviour, event ids.

If any answer is "unknown", the provider stays `unverified` and cannot be enabled (enforced by the staff API).

## 2. Writing the adapter

1. Subclass `IranianGatewayProvider` or `InternationalProvider` (or `BasePaymentProvider`), set a unique `key` (slug ≤ 30), declare `capabilities` truthfully (`supported_currencies`, `supports_refund`, `supports_partial_refund`, `supports_server_verification`).
2. Implement `initiate` (create session/payment; send `request.merchant_ref` as the merchant reference and use `request.callback_url`), `parse_callback` (authenticate; raise `CallbackRejected`; return a `ProviderEvent` with `trusted=True` only if authenticated by signature), `verify` (server-side status query → `VerificationResult` incl. amount/currency/merchant ref), and `refund` if supported (raise `RefundRejected` for definitive refusals, `ProviderError` when the outcome is unknown; **pass the idempotency key to the provider**).
3. Use `providers/client.request_json` for HTTP (https only, timeouts, no redirects). Read secrets with `self.credentials()`; never log them; never put them in a model or an API response.
4. Provide a deterministic `event_id` when the provider sends none (e.g. `<ref>:<status>`).
5. Register: `PAYMENT_PROVIDER_ADAPTERS=my_package.providers.MyGateway` and `PAYMENT_PROVIDER_CREDENTIALS={"my_gateway": {...}}` in the environment. Production requires `PAYMENTS_CALLBACK_BASE_URL` to be https when adapters are listed.
6. Tests: copy the scenarios of `tests/test_payment_providers.py` / `test_payment_refunds.py` against recorded sandbox responses (success, failure, expiry, duplicate callback, invalid signature, amount mismatch, duplicate and partial refund, outage).

## 3. Enabling it

1. Create the config: `POST /api/v1/staff/payment-providers` (`key`, `names`, `merchant_country`, `supported_currencies`, `settlement_*`, `legal_notes`) — it starts disabled and unverified.
2. After section 1: `PATCH …/{key}` with `eligibility_status=verified` **and** `eligibility_notes` (mandatory).
3. Run the full flow in the provider's **sandbox** (`mode=sandbox`), including a refund and a lost-webhook reconciliation. Then record `sandbox_verified_at`.
4. Enable (`is_enabled=true`); switch to `mode=live` only with live credentials and after the sandbox record exists.
5. Watch `staff/payments?problem=1` and the reconciliation runs during the first days.

## 4. What is explicitly NOT done in Phase 8
No real Iranian gateway adapter, no real international adapter, no 3-D Secure / hosted-field handling (the provider's hosted page does that — no card data touches this backend), no settlement/accounting export.
