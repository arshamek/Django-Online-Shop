# Phase 6: calculation examples and financial test fixtures

All numbers below are **produced by the real engine** from `tests/fixtures/pricing_scenarios.json` and are asserted by
`tests/test_pricing_engine.py`. The exchange rates are **synthetic test numbers, not market data**:

| Currency | IRR per 1 unit (synthetic) | Toman per 1 unit |
|---|---:|---:|
| TRY | 15,000 | 1,500 |
| AED | 150,000 | 15,000 |
| USD | 550,000 | 55,000 |
| EUR | 600,000 | 60,000 |

Rule: every component is rounded HALF_UP to a whole rial when created; later components use the rounded earlier ones, so you can re-add the printed lines and get the final price exactly. 1 Toman = 10 rial.

## 1. Currency to Toman (1,000 units, no costs, 0% profit)

| Source amount | IRR | Toman |
|---|---:|---:|
| 1,000 TRY | 15,000,000 | 1,500,000 |
| 1,000 AED | 150,000,000 | 15,000,000 |
| 1,000 USD | 550,000,000 | 55,000,000 |
| 1,000 EUR | 600,000,000 | 60,000,000 |

## 2. Markup versus margin (the owner decision B-2)

Cost 100 and a 25% target: markup sells at 125.00 (profit 25% of cost), margin sells at 133.33 (profit 25% of the price).

## 3. S1-markup-all-inclusive

1,000 TRY item; shipping 3,000,000 IRR, customs 10% of (item+shipping), service fee 5% of item, all included in the price; 25% markup; nearest 100,000 IRR.

Input: 1,000 TRY; profit method **markup**, target 25%.

| Line | Basis (IRR) | % | Amount (IRR) | In price? | Mode | Status |
|---|---:|---:|---:|:-:|---|---|
| Item price (converted) |  |  | 15,000,000 | yes |  | confirmed |
| ship |  |  | 3,000,000 | yes | included | confirmed |
| duty | 18,000,000 | 10.000000 | 1,800,000 | yes | included | confirmed |
| svc | 15,000,000 | 5.000000 | 750,000 | yes | included | confirmed |
| Profit (markup) | 20,550,000 | 25 | 5,137,500 | yes |  |  |
| round |  |  | 12,500 | yes |  |  |

* Product price: **25,700,000 IRR = 2,570,000 Toman**
* Billed separately: 0 IRR
* Maral's own cost: 20,550,000 IRR; realised profit 5,150,000 IRR (markup 25.0608%, margin 20.0389%)

## 4. S2-margin-all-inclusive

Same costs as S1 but profit_method = margin 25% (profit is 25% of the SELLING price).

Input: 1,000 TRY; profit method **margin**, target 25%.

| Line | Basis (IRR) | % | Amount (IRR) | In price? | Mode | Status |
|---|---:|---:|---:|:-:|---|---|
| Item price (converted) |  |  | 15,000,000 | yes |  | confirmed |
| ship |  |  | 3,000,000 | yes | included | confirmed |
| duty | 18,000,000 | 10.000000 | 1,800,000 | yes | included | confirmed |
| svc | 15,000,000 | 5.000000 | 750,000 | yes | included | confirmed |
| Profit (margin) | 20,550,000 | 25 | 6,850,000 | yes |  |  |
| round |  |  | 0 | yes |  |  |

* Product price: **27,400,000 IRR = 2,740,000 Toman**
* Billed separately: 0 IRR
* Maral's own cost: 20,550,000 IRR; realised profit 6,850,000 IRR (markup 33.3333%, margin 25.0000%)

## 5. S3-shipping-billed-separately

As S1 but shipping is a separate customer line (not in the product price, no markup on it). Customs is still charged on item+shipping.

Input: 1,000 TRY; profit method **markup**, target 25%.

| Line | Basis (IRR) | % | Amount (IRR) | In price? | Mode | Status |
|---|---:|---:|---:|:-:|---|---|
| Item price (converted) |  |  | 15,000,000 | yes |  | confirmed |
| ship |  |  | 3,000,000 | no | separate | confirmed |
| duty | 18,000,000 | 10.000000 | 1,800,000 | yes | included | confirmed |
| svc | 15,000,000 | 5.000000 | 750,000 | yes | included | confirmed |
| Profit (markup) | 17,550,000 | 25 | 4,387,500 | yes |  |  |
| round |  |  | -37,500 | yes |  |  |

* Product price: **21,900,000 IRR = 2,190,000 Toman**
* Billed separately: 3,000,000 IRR
* Maral's own cost: 17,550,000 IRR; realised profit 4,350,000 IRR (markup 24.7863%, margin 19.8630%)

## 6. S4-absorbed-shipping-minimum-profit

Shipping absorbed by Maral (3,000,000). 10% markup would lose money, so the 2,000,000 IRR minimum profit lifts the price.

Input: 1,000 TRY; profit method **markup**, target 10%.

| Line | Basis (IRR) | % | Amount (IRR) | In price? | Mode | Status |
|---|---:|---:|---:|:-:|---|---|
| Item price (converted) |  |  | 15,000,000 | yes |  | confirmed |
| ship |  |  | 3,000,000 | no | absorbed | confirmed |
| Profit (markup) | 15,000,000 | 10 | 5,000,000 | yes |  |  |

* Product price: **20,000,000 IRR = 2,000,000 Toman**
* Billed separately: 0 IRR
* Maral's own cost: 18,000,000 IRR; realised profit 2,000,000 IRR (markup 11.1111%, margin 10.0000%)

## 7. S5-percent-plus-fixed-markup

20% markup plus a fixed 500,000 IRR profit on a 1,000 TRY item.

Input: 1,000 TRY; profit method **markup**, target 20%.

| Line | Basis (IRR) | % | Amount (IRR) | In price? | Mode | Status |
|---|---:|---:|---:|:-:|---|---|
| Item price (converted) |  |  | 15,000,000 | yes |  | confirmed |
| Profit (markup) | 15,000,000 | 20 | 3,500,000 | yes |  |  |

* Product price: **18,500,000 IRR = 1,850,000 Toman**
* Billed separately: 0 IRR
* Maral's own cost: 15,000,000 IRR; realised profit 3,500,000 IRR (markup 23.3333%, margin 18.9189%)

## 8. S6-per-kg-shipping-eur-fee

Shipping 2,000,000 IRR per kg for a 750 g item, plus a 2 EUR purchase fee (converted at the EUR rate); 25% markup; 1,000 AED item.

Input: 1,000 AED; profit method **markup**, target 25%.

| Line | Basis (IRR) | % | Amount (IRR) | In price? | Mode | Status |
|---|---:|---:|---:|:-:|---|---|
| Item price (converted) |  |  | 150,000,000 | yes |  | confirmed |
| fee |  |  | 1,200,000 | yes | included | confirmed |
| ship |  |  | 1,500,000 | yes | included | confirmed |
| Profit (markup) | 152,700,000 | 25 | 38,175,000 | yes |  |  |

* Product price: **190,875,000 IRR = 19,087,500 Toman**
* Billed separately: 0 IRR
* Maral's own cost: 152,700,000 IRR; realised profit 38,175,000 IRR (markup 25.0000%, margin 20.0000%)

## 9. What happens while decisions are pending

| Situation | Result |
|---|---|
| Profit method not chosen, shipping and customs not defined | cost-only preview 15,000,000 IRR, **not publishable**; blockers: `missing_required_cost`, `profit_method_pending` |
| Shipping rule exists but its value is unknown | not publishable: `unknown_cost` (never treated as 0) |
| No exchange-rate route configured | no price: `no_rate_route` |
| Rate older than the route's maximum age | no price: `rate_stale` (unless the route explicitly allows a flagged stale fallback) |
