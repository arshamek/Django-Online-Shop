# Phase 9 – Product sources, limitations and production blockers

## Source status (as of this delivery)
| Source | Mode | Status reported | Live external search verified? |
|---|---|---|---|
| Maral catalogue | Live database search | `AVAILABLE` | n/a (internal) |
| Trendyol | **Stored import snapshots only** | `AUTHORIZATION_REQUIRED` (live) · `snapshot_search_status: AVAILABLE` | **No** |
| Namshi | **Stored import snapshots only** | `AUTHORIZATION_REQUIRED` (live) · `snapshot_search_status: AVAILABLE` | **No** |

`status` is computed from the `ProductSource` row (`authorization_problems`): a source without `integration_type` =
`official_api`/`licensed_feed` and a recorded licence (`terms_note`) is `AUTHORIZATION_REQUIRED`; an authorised source is
`UNSUPPORTED` because no verified keyword-search capability exists; a missing/inactive source is `NOT_CONFIGURED`. No provider
method is called from the assistant, nothing is scraped, and rate limits are not touched.

### Why live search stays off
* The Phase 5 Trendyol adapter reads the **seller's own** product list (`products/approved`). It is not a marketplace catalogue
  search and must not be presented as one.
* The Namshi adapter reads a licensed feed file whose schema (`maral-namshi-feed v1`) is an assumption until Namshi supplies
  its real contract; no public Namshi catalogue API was found.
* Both adapters are `LIVE_VERIFIED = False` and have only ever run on synthetic fixtures.

### What "snapshot" results are
Rows already imported by the Phase 5 engine. They can be old; every one shows `snapshot.price_observed_at`, `stale`, and an
availability that is `NOT_VERIFIED`/`UNKNOWN`. Fixture rows (`data_origin = fixture`) are hidden from customers unless staff
enable `allow_fixture_snapshots`, so an empty production database shows *no* Trendyol/Namshi results rather than fake ones.

## Pricing and currency limits
* External estimates need a **published Phase 6 rule set** and **fresh exchange rates**; without them the price is `incomplete`
  (no number). Neither exists in this repository's database; the owner decisions in the Phase 6 documents are still required.
* The estimate is `provisional` when the rule set has no shipping or customs rule, when costs are only estimated, or when the
  stored source price is older than `price_max_age_hours` (default 72 h).
* Phase 6 rules matched on brand/category only apply to external listings that Phase 5 has matched to a canonical product;
  unmatched listings are priced with source/country/fulfilment rules only.
* Delivery times are never given.

## Other limitations
* Only one language model provider (Anthropic) is implemented, and it is **not live-verified** (`LIVE_VERIFIED = False`).
* Vocabulary (categories, colours, brands, uses) covers footwear, clothing, bags, watches and perfume; other categories work
  through free keywords and the catalogue's own brand names. It is not an NLP model and will miss unusual phrasing.
* Dedup is intentionally conservative: duplicates are only folded when Phase 5 matched the listing to the shown product.
* Original marketplace URLs are withheld by default (staff switch); marketplace image URLs are shown only for sources whose terms allow image copying (see decisions).
* No streaming responses; a turn is one request/response.
* SQL Server behaviour of the new tables was not exercised here (tests ran on SQLite; see the summary).
* Arabic/Turkish quality is basic (see the multilingual document).

## Unfinished integrations and production blockers
1. **Trendyol:** a written agreement/official API access that covers *catalogue reading for third-party products* (not just the
   seller API), credentials, and a verified `search` capability on the provider. Until then Trendyol results are stored snapshots.
2. **Namshi:** a licensed feed or API agreement, the real feed/API contract and credentials; set `integration_type` and
   `terms_note` on the source.
3. **A real AI provider:** `AI_API_KEY`, `AI_MODEL` (and `AI_BASE_URL` if proxied); set the staff config provider to `anthropic`;
   run `POST /staff/ai/provider-test`; then run a **Persian evaluation** (tone, grammar, number/price handling) before launch.
4. **Pricing prerequisites:** published rule set, owner-approved shipping/customs/profit decisions, live FX rates (Phase 6).
5. **Import data:** a real, regularly refreshed import of Trendyol/Namshi listings (Phase 5 jobs) – the assistant can only show
   what is imported.
6. **Frontend:** the chat UI, product cards, inquiry form and notices described in the integration guide.
7. **Legal/privacy review:** retention period, conversation text is customer data (deletable by the customer; purged after
   `retention_days`), consent wording for AI use, the decision to expose marketplace URLs, and recording image-copy permission (`allows_image_copy`) per source only once the written terms allow it.
8. **Staff process** for purchase inquiries (who reviews them, SLAs) and a notification channel if wanted (none exists).
9. **Load/abuse testing** of the per-user limits against the real database (SQL Server) and cache/Redis settings.
