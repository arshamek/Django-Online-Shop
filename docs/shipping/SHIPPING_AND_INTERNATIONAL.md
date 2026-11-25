# Phase 8 — Shipping, addresses and international orders

> **Status: tariff-table shipping + mock carrier. No real carrier API (Iran Post, Tipax, DHL, Aramex …) is implemented or claimed to work.**
> Real carriers need a contract, credentials and sandbox access.

## 1. Model (extends the Phase 7 `ShippingMethod`; nothing was duplicated)

| Model | Purpose |
|---|---|
| `ShippingMethod` (extended) | delivery option: `kind` domestic/international, `provider_key` (`table` = our tariffs, or a carrier adapter), `carrier_code`, configured delivery range, `requires_tracking`, `tracking_url_template` (https + `{tracking_number}`), `max_weight_grams`, free threshold. Phase 7 flat `fee_irr` + `countries` still work for methods without rates |
| `ShippingZone` | set of countries, optionally narrowed to provinces/states (Persian or Latin spellings), `priority` |
| `ShippingRate` | method × zone price: `base_fee_irr` + `per_kg_irr` above `included_weight_grams`, subtotal tiers, free-over threshold, delivery range override |
| `CountryShippingPolicy` | per destination: shippable or blocked, address-requirement overrides, customs mode + localized notices, restricted category slugs, localized policy notes |
| `Shipment` / `ShipmentEvent` | parcel with carrier, tracking number/url, status, carrier-confirmed estimate; **append-only** history |

## 2. Pricing rules (`services/methods.resolve`)
* A rate is chosen by zone (specific region beats country-wide, then `priority`) and subtotal tier. Fee = base + ⌈extra kg⌉ × per-kg.
* **Unknown stays unknown**: a missing parcel weight makes a per-kg method *unavailable (`weight_required`)*, never free; a method with no rate and no country serves nowhere; an unserved country yields no options with the reason `unsupported_destination`.
* Country policy can block a country (`shipping_country_not_shippable`) or restricted categories (`shipping_restricted_items`).
* Fee status (`confirmed`/`estimated`) travels with the quote and is shown to the customer.

## 3. Delivery estimates
Only an **administrator-entered day range** (`delivery_min_days`/`max_days`, status `estimated` or `confirmed`) is ever shown; otherwise `status: "unknown"`.
Exact dates exist only as `carrier_estimated_delivery_at`, set from a carrier response. The API contains no computed delivery date anywhere.

## 4. Addresses (`shipping/addresses.py`, `geo.py`)
* Defaults = Phase 7: name, phone, country, city, address line 1; Iran additionally the 10-digit postal code.
* Per-country override without code: `CountryShippingPolicy.address_requirements = {"required": [...], "optional": [...], "postal_code_pattern": "^\\d{5}$", "phone_required": false}` (validated by the staff API).
* Persian/Arabic-Indic digits → ASCII; Arabic ي/ك → Persian ی/ک; spacing normalised; delivery instructions are part of every address.
* All 31 Iranian provinces are canonicalised (Persian + Latin spellings) so `تهران`, `استان تهران`, `Tehran` select the same zone.
* `GET /shipping/address-requirements?country=XX` tells the frontend which fields to show.

## 5. International orders — customs and taxes
The checkout and order show a `customs` block whose status is one of:

| status | meaning | amount |
|---|---|---|
| `estimated` | home-country import customs from pricing rules that are themselves estimates | yes, flagged `is_estimate` |
| `confirmed` | same, from confirmed rules | yes |
| `payable_on_delivery` | duties/taxes collected by the carrier/customs at the destination (policy `on_delivery`) | **none** — not in our total |
| `not_applicable` | domestic delivery with no customs component, or policy says none | none |
| `unknown` | nothing configured (default for every foreign country) — a warning `customs_unknown` is added | **none** — never shown as zero |

Amounts are produced **only for the home country (IR)**: the quote's customs components are Iran's import customs and are never presented as a
foreign destination's duties. A destination duty calculation (DDP) needs a duty/tax table and is **not implemented**; `prepaid` mode without one is reported as `unknown`.
Taxes remain `not_modeled` (Phase 7). Delivery, customs and policy wording are frozen into `shipping_snapshot` on the checkout and the order.
Display currency: all customer endpoints accept the usual `currency` context; fees are stored in IRR and converted for display.

## 6. Shipments and tracking
* Status machine (`services/shipments.TRANSITIONS`): `pending → in_transit → out_for_delivery → delivered`, with `failed_attempt`, `exception`, `returned`, `cancelled`; delivered/returned/cancelled are final; illegal moves → 409 `invalid_shipment_transition`; repeats are idempotent.
* **Tracking**: format-validated (`invalid_tracking_number`). A method with `requires_tracking` cannot ship without it: both the order transition and the shipment leaving `pending` fail with `tracking_required`. Default is `false` to keep Phase 7 behaviour — recommended `true` for real carriers.
* **Order sync**: shipment dispatched → order `SHIPPED` (from `READY_TO_SHIP`); all non-cancelled shipments delivered → order `DELIVERED`. The old path (staff moves the order to `SHIPPED` with tracking metadata) now creates/updates the shipment automatically; the order's own `tracking_*` fields mirror the shipment.
* **Carrier adapters** (`BaseShippingProvider`): `quote`, `create_shipment`, `fetch_tracking`. `shipping.sync_tracking` (Celery beat, every `SHIPPING_TRACKING_SYNC_MINUTES`) pulls scans; scans are idempotent by external id, out-of-order scans are kept as history without moving the status. Push webhooks from carriers are not implemented.
* The mock carrier (`SHIPPING_ALLOW_MOCK_CARRIER`, refused in production) exists only to exercise this plumbing.
