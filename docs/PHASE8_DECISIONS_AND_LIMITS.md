# Phase 8 — decisions, open business questions, honest limits

## A. Technical decisions (made)
| # | Decision | Reason |
|---|---|---|
| A1 | Extend the Phase 7 `payments` / `shipping` apps; Phase 7 behaviour and the 823 existing tests are unchanged | No duplicate models, services or endpoints |
| A2 | Adapters never touch the database; the service layer decides everything | A new provider cannot bypass the safety rules |
| A3 | Provider eligibility, enabling and sandbox verification are **data** (`PaymentProviderConfig`) enforced by the API | Nobody can enable a provider on an assumption |
| A4 | The charge (currency, amount, FX rate) is computed and frozen server-side at initiation; callbacks must match exactly | A provider/customer cannot change what is owed |
| A5 | Untrusted callbacks are only a hint; the provider's server-side verification is applied | "Never trust frontend payment success" |
| A6 | Provider network calls happen outside DB locks (payment row committed first) | A slow gateway cannot hold a checkout lock |
| A7 | A callback for an already-disabled provider is still processed | Money that really arrived must be recognised |
| A8 | Reconciliation never reverses a capture automatically; it flags | A wrong automatic reversal is worse than a review item |
| A9 | Refund = one `PaymentRefund` per order refund, provider idempotency key, cap under lock | Duplicate refunds are impossible |
| A10 | Shipments are the source of truth for delivery; the order mirrors them; the old "mark order shipped" path still works | Backwards compatible |
| A11 | Unknown stays unknown: no weight/tariff/estimate/duty is ever defaulted to 0 or a made-up value | Honest customer information |
| A12 | New permissions `payments_manage`, `shipping_manage`, `shipments_manage`; Finance Manager gets the first, Order Manager the other two | Separation of duties |
| A13 | Foreign destination customs amounts are never taken from Iranian import-customs components | Different legal charge |

## B. Business decisions that need the owner (temporary default in brackets)
| # | Question | Temporary default |
|---|---|---|
| B1 | Which Iranian PSP(s) will be used (contract, merchant id, fees, refund support)? | None enabled; only mocks in dev |
| B2 | Can any international provider onboard your legal entity, and in which country must the merchant be registered? Settlement currency/bank route and sanctions/legal review | **Unknown — no international provider can be enabled**; the framework is ready |
| B3 | FX policy for foreign-currency charges (who bears rate movement, margin, which rate purpose) | Charge at the current `display` rate; refuse stale rates |
| B4 | Which currencies to offer per provider | Adapter ∩ config; first/`default_charge_currency` when the customer's display currency is unsupported |
| B5 | Real tariffs: zones, per-method prices, per-kg, free thresholds | Nothing seeded (demo loader unchanged); an unpriced method is unavailable |
| B6 | Delivery time ranges per method/zone | Unknown (no range shown) |
| B7 | Which countries to serve internationally and which categories are restricted | None |
| B8 | Customs/duty handling per country (`on_delivery` vs prepaid) and the duty table if prepaid | `unknown`; prepaid calculation not implemented |
| B9 | Require tracking numbers for shipped orders? | `requires_tracking=false` (Phase 7 behaviour); recommended `true` per method |
| B10 | Carrier(s) and whether to integrate their APIs | Manual tracking entry |
| B11 | Reconciliation cadence / look-back | every 15 min / 7 days (env) |
| B12 | Several parcels per order allowed? | Yes (model supports it); order delivered when all are |

## C. Honest limits (what is NOT done / NOT verified)
1. **No real payment gateway or carrier adapter exists.** Everything runs against mocks; nothing here proves a real integration works.
2. The mock providers simulate generic behaviour, not any specific gateway's API.
3. No destination-country duty/tax calculation; taxes `not_modeled`.
4. Provider refund status polling is not implemented (an unknown refund outcome is retried with the same key); provider-side refunds made outside the system are flagged, not imported.
5. Carrier push webhooks and label printing are not implemented (pull sync + manual tracking only).
6. Tests ran on SQLite smoke mode (the project's own fallback). Row-lock semantics and the new constraints were **not** exercised on SQL Server here.
7. `config/settings/production` guards were verified by import; the four `test_settings.py` production tests (`django.setup()` with the SQL Server driver) could not run in this environment (no ODBC runtime) — they fail identically before and after Phase 8.
8. The React storefront was **not** changed; its mock payment/shipping step must be wired to the endpoints in `api/PHASE8_PAYMENTS_SHIPPING_API.md`.
9. Pre-existing test failures (unrelated to Phase 8, present in the uploaded project): 4 in `tests/test_user_model.py` (old `create_user` usage) and 4 in `tests/test_settings.py` (need the SQL Server ODBC driver).
