# Phase 9 – Frontend integration guide

**Status: not implemented.** No frontend file was touched in Phase 9. This is the contract a frontend must follow; it has
not been exercised against a real UI.

## 1. Chat widget lifecycle
1. First message: `POST /ai/chat` without `conversation_id`; keep the returned `conversation_id` for the whole chat.
2. Send `language` only when the customer picks a language explicitly; otherwise omit it (or `"auto"`) and render the reply
   in `response.language` (set `dir="rtl"` for `fa` and `ar`, `ltr` for `en` and `tr`).
3. Render `assistant_message` as plain text (it contains line breaks, no markdown, no HTML), then render `products` as cards.
4. "New chat" = start a new conversation (no id). Nothing carries over between conversations. Offer "Delete this chat" →
   `DELETE /ai/conversations/{id}`.
5. Calls need the session cookie and the project's CSRF header like every other authenticated POST. Logged-out customers get
   401/403: show the login prompt.

## 2. Banners and notices
* `demo_mode: true` → show a persistent "Demo assistant" badge. Never describe it as AI-powered.
* `ai_fallback: true` → no UI change needed (results are still correct).
* `clarification` ≠ null → show `clarification.question`; for `options: ["toman","rial"]` show two quick-reply buttons that send
  «تومان» / «ریال» style text such as «۵ میلیون تومان» (the amount the customer meant) in the same conversation.
* `relaxed_filters` non-empty → "closest matches" hint; cards with `match.unmet` should show what was not matched.
* `provider_status`: for every entry with `status != "AVAILABLE"` show a small note such as "Trendyol live search needs official
  authorisation (showing saved data)". Do not show it as an error. `ERROR`/`TEMPORARILY_UNAVAILABLE` may be shown as a warning.

## 3. Product card
| Element | Source field | Rule |
|---|---|---|
| Image | `images[0].url` | Maral products have images. External products have `images: []` unless the source's licence allows image copying (`images_note: image_urls_withheld_source_terms` explains); show a neutral placeholder. Never hot-link anything not in `images`. |
| Title, brand | `title`, `brand` | Plain text; escape it. It can be `(title withheld)` for suspicious marketplace text. |
| **Source label** | `source.code` | Always visible: Maral / Trendyol / Namshi badge. |
| Stored-data badge | `kind == "imported_snapshot"` | "Saved data, not live" + `snapshot.price_observed_at` as a relative time. Add "outdated" when `snapshot.stale`. |
| Price | `prices.estimated_maral` | Format `amount` as Toman. Label by `status`: `listed` → price; `estimated` → "Estimated price"; `provisional` → "Provisional – excludes some costs" (tooltip from `missing_costs` / `warnings`); `incomplete`/`unavailable` → "Price not available yet" (no number). |
| Original price | `prices.original` | Secondary text: "Marketplace price 1,000 TRY". Never present it as Maral's price. |
| Confirmed price | `prices.confirmed` | Always `null` here. The word "final/confirmed" may only appear in checkout. |
| Availability | `availability.status` | `IN_STOCK` → "In stock"; `OUT_OF_STOCK`; `NOT_VERIFIED` → "Availability not verified" (+ "last seen: in stock" from `last_known`); `UNKNOWN` → "Unknown". Never show a green "in stock" for anything but `IN_STOCK`. |
| Sizes / colours | `sizes`, `colors` | Internal: objects with `available`; external: plain strings from stored variants (marketplace text: escape it like every other string; render as text, never HTML). Show only what is listed. |
| Why it matches | `match.matched`, `match.unmet` | Chips: brand, category, color, size, budget, use:*. |
| Link | `product_url`, `maral.detail_api_path` | For Maral products link to the existing product page by `maral.slug`. `product_url` is normally `null` for external products (staff-controlled); show a link only when present. |
| Also on | `also_listed_at` | Optional "Also listed on Trendyol" line (only for matched duplicates). |

## 4. Choosing a product
* The customer taps a card (or says «اولی رو می‌خوام») → send `product_references` (`product.ref`) with a message, or tap-handler
  calls `POST /ai/chat` with text "I'll take this one" + `product_references:[ref]`. Read `selection`.
* `selection.type == "internal_product"` → let the customer choose a variant from `selection.variants`, then use the **existing**
  cart API and checkout. The assistant has created nothing.
* `selection.type == "purchase_inquiry"` → show a form with `selection.options` (size, colour as dropdowns), quantity and a note →
  `POST /ai/purchase-inquiries`. After `201` show "Request received – our team will check price, availability and purchasing
  options. This is not an order and nothing has been charged." Track it via `GET /ai/purchase-inquiries`.
* Never show "order placed" for either path. `is_order` is always `false`.

## 5. Comparison view
`comparison.rows[].values[i]` aligns with `comparison.products[i]`. A `null` value is rendered as "Not available" (never
blank or zero). Highlight `cheapest_estimated` only as "Lowest estimated price".

## 6. Errors
| Status / code | UI |
|---|---|
| 429 `ai_usage_limit` | "Daily assistant limit reached" |
| 429 (throttle) | "Slow down" + retry later |
| 503 `ai_disabled` | Hide or disable the widget |
| 404 on a conversation | Start a new conversation |
| 422 (inquiry codes above) | Show the field message next to size / colour |

## 7. Persian formatting
`assistant_message` already uses Persian digits and «٬» separators for Persian replies. For cards, format `amount` with
`Intl.NumberFormat('fa-IR')` + «تومان»; for `en`/`tr` use Latin digits + "Toman". Keep brand names in Latin script.
