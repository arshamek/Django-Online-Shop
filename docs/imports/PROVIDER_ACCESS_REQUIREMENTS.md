# Provider access requirements (Trendyol, Namshi)

Status: researched 2026-11-21 from the sources named below. **Nothing here has been exercised against a live
service.** Re-check each source before relying on it; terms and limits change. This is engineering
documentation, not legal advice: have the licence/terms position confirmed in writing before any live import.

## Summary

| | Trendyol | Namshi |
|---|---|---|
| Official programme | Marketplace "Partner" integration API (developers.trendyol.com) | Seller-side partner portal / catalogue upload tooling; affiliate programmes via affiliate networks |
| Who it is for | **Sellers**, managing their *own* store | **Sellers** (uploading) and **publishers** (tracked links) |
| Can it read an arbitrary third-party catalogue? | **No.** Product V2 lists the authenticated seller's own approved products | **No public catalogue-read API was found** |
| What Maral needs before a live import | Partner credentials (Maral's own, or a seller who authorises Maral), **or** a separate written data agreement with Trendyol | A written agreement with Namshi (licensed feed or partner API) |
| Provider status in this repo | Mapper + live transport written to the documented V2 shape; fixtures only; `LIVE_VERIFIED = False` | Licensed-feed reader with an **assumed** schema; fixtures only; `LIVE_VERIFIED = False` |

If neither source grants access, the engine still works with fixtures, with the staff/CSV path
(TECHNICAL_DECISIONS, "Source access not granted"), or with a different licensed source. It must not fall back
to scraping.

## Trendyol

What the documentation says (developers.trendyol.com, read 2026-11-21):

* The integration is the Marketplace Partner API for sellers: product transfer, stock/price updates, orders.
* Authentication is HTTP Basic with the seller's API key/secret, plus the seller id in the path. Requests must
  carry a `User-Agent` of the form `<sellerId> - <IntegratorName>` (or `SelfIntegration`); requests without it
  are rejected with 403. The stage environment is IP-allow-listed.
* **Product V1 endpoints were retired on 15 Oct 2026.** Only Product V2 is used here:
  `GET /integration/product/sellers/{sellerId}/products/approved` (filters: `page`, `size` max 100,
  `contentId`, `barcode`, date filters; `page x size` is capped at 10,000, after which `nextPageToken` is used).
* The service-limits page lists a read limit of 1,000 requests/min for the lowest tier (updated 14 Sep 2026).
  Maral's default ceiling is 60/min per source (`ProductSource.rate_limit_per_min`); keep it at or below the
  published limit for your tier.
* Trendyol's influencer/affiliate programme exists. No product-feed offering for third parties was found in it.

What this means for Maral's proxy-purchasing model: Product V2 returns *only the credentials-holder's own
products*. It cannot list or look up other sellers' products. It is useful if Maral (or a seller who authorises
Maral as their integrator) has Partner credentials; it is not a catalogue source for proxy buying.

Ways to get legitimate catalogue access, in order of effort:

1. Ask Trendyol (partner/business development) for a **licensed catalogue/data feed** for resale or proxy
   purchasing, in writing, with permitted uses, caching/retention, image rights and rate limits stated.
2. Become a Trendyol seller (or integrator for a consenting seller) if the use case is selling their own stock.
3. Use the staff/CSV path for hand-picked products.

Enabling a live Trendyol source (only after one of the above):

1. Record the permission: set the `trendyol` ProductSource `integration_type` to `official_api` or
   `licensed_feed` and put the agreement reference/summary in `terms_note`. (The gate refuses live calls if
   either is empty. This records a decision; it does not prove one.)
2. Set `TRENDYOL_SELLER_ID`, `TRENDYOL_API_KEY`, `TRENDYOL_API_SECRET`, `TRENDYOL_INTEGRATOR_NAME`,
   `TRENDYOL_STAGE=true` in the environment. Secrets are never stored in the database.
3. Set `IMPORTS_PROVIDER_MODE=live`.
4. Run the **contract verification checklist** against the stage environment before any production use:
   response field names/types match `providers/trendyol.py`; `priceSeenByCustomer` vs `salePrice` semantics (which
   one Maral may use as the purchase price); stock fields; image URL form (the documented sample shows relative
   paths, so a CDN base must be agreed; absolute `https://` URLs are required for publication); pagination past
   10,000 items; behaviour on 401/403/429 and the `Retry-After` header. Then flip `LIVE_VERIFIED` in code.

Known limits of the Trendyol provider: no category-filtered discovery (the documented filter has none);
`barcode` is the seller's own barcode, so a GTIN from Trendyol is *never* trusted on its own for auto-matching;
`productMainId` is the seller's grouping code and is deliberately not treated as a manufacturer model code.

## Namshi

What was found (read 2026-11-21; these are mostly third-party descriptions, not Namshi documentation):

* Selling on Namshi uses a partner account on Noon's partner portal. Integrator platforms (e.g. ChannelEngine's
  Namshi guide) describe catalogue **uploads into** Namshi using service-account credentials issued to the
  seller. This is a write/seller path.
* Namshi affiliate programmes run on affiliate networks (tracked links, commissions). No product-feed terms for
  Namshi were found.
* Third-party "scraper" products for Namshi exist. They are not an authorised route and are not used.
* No public API for reading Namshi's catalogue as a third party was found.

So a live Namshi import requires a **written agreement with Namshi** (a licensed catalogue feed or a partner
API). Until then `NamshiProvider` reads a feed *file* delivered under such a licence
(`IMPORTS_CREDENTIALS['namshi']['feed_path']` / `NAMSHI_FEED_PATH`).

The feed schema in `providers/namshi.py` ("maral-namshi-feed v1": `feed_version`, `currency`, products keyed by
`parent_sku` with `variants[].partner_sku`, `gtin`, `price.msrp/sale`, `stock.qty`, `images[]`, `category_path`)
is **assumed** so the pipeline could be built and tested. It must be reconciled with the real specification when
one exists, including: GTIN presence, currencies (Namshi markets use AED/SAR/USD; **SAR is not in
`IMPORTS_SUPPORTED_CURRENCIES`** and would be rejected until the pricing phase supports it), Arabic/English text
fields, update frequency, and image/usage rights.

## What the engine enforces regardless of source

* Live calls are refused unless the source row records `official_api`/`licensed_feed` and a non-empty
  `terms_note`, credentials are present, and `IMPORTS_PROVIDER_MODE=live`.
* HTTPS only, fixed host allow-list, no redirects, 8 MB response cap, 15 s timeout.
* No CAPTCHA, anti-bot, authentication or rate-limit circumvention: a 401/403 fails the job without retrying; a
  429 pauses every worker for that source until `Retry-After`; there is no IP/User-Agent rotation.
* Image metadata is stored; images are not downloaded. Copying needs `ProductSource.allows_image_copy`, and a
  published product needs an image Maral may show (`IMPORTS_REQUIRE_IMAGE`).
* Fixture data is tagged `data_origin=fixture` and can never be published unless
  `IMPORTS_ALLOW_FIXTURE_PUBLISH=true` (development/testing only).
