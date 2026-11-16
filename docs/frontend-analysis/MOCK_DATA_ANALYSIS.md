# Mock Data Analysis & Migration Inventory

## 1. Inventory
| # | Mock dataset | Location | Size | Used by |
|---|---|---|---|---|
| 1 | Products | `lib/shop-data.ts` `products` | 16 items (`p1`–`p16`) | everything |
| 2 | Categories | `categories` | 6 (women, men, shoes, bags, accessories, beauty) with Persian label, image, description | home, shop, header mega menu, product breadcrumb |
| 3 | Nav categories | `navCategories` | 8 (incl. dead "kids", "brands", "sale") | header |
| 4 | Brands | derived: `Array.from(new Set(products.map(brand)))` | 10 (Atelier Nour, Lumière, Maison Sable, Noir Atelier, Strada, Oxford & Co, Hollis, Soie Persane, Ora, Ambre Studio) — fictitious | home, shop filter, mega menu |
| 5 | Sources (stores) | type `Source` + hard-coded list in shop filter + home tiles | Trendyol (TR), Namshi (AE), Maral (own stock) | filter, badge, home |
| 6 | Currencies | `originalCurrency` | TRY, AED (IRT declared, unused); Toman display only | product spec tab, price |
| 7 | Colors | const `C` | 7 named swatches in `oklch()` | product, filter |
| 8 | Sizes | per-product string arrays | S–XXL, 38–44 shoe sizes, "One Size", "50ml/100ml", "90×90" | product, filter |
| 9 | Reviews | `product.$id.tsx` `reviews` | 2 generic reviews shown on **every** product, labelled "sample" | review tab |
| 10 | Size guide | product page modal | one S–XL chest/waist table for every category | modal |
| 11 | Orders | `account.tsx` `orders` | 2 (`MRL-482910` delivered, `MRL-519034` shipping from Turkey) | account orders |
| 12 | Customer | `account.tsx` | "سارا محمدی", phone, email, birthday, 1 address | account |
| 13 | AI replies | `AIChat.tsx` `reply()` | keyword matcher (کتونی/کفش, کیف, مقایسه, زمست, default) + 4 suggestion chips + greeting | chat |
| 14 | Home AI teaser conversation | `index.tsx` | static 2 bubbles + products p1, p8 | home |
| 15 | Shipping rules | `SHIPPING_FLAT`, `FREE_SHIPPING_FROM`, express 950,000 | 3 constants (+ banner text) | cart, checkout, header |
| 16 | Delivery estimates | `product.$id.tsx` | 2 strings | product page |
| 17 | Shop price slider bounds | `shop.tsx` `MAX=50_000_000`, min 2,000,000 | constants | filter |
| 18 | Marketing copy | hero "Winter Collection 2026", "up to 30% off", trust icons, footer contacts | static | home/footer |
| 19 | Images | `src/assets/*.jpg` (14 AI placeholders) | reused across products (each product uses 2 of ~10 images) | cards, gallery |
| 20 | Newsletter | `index.tsx` | local state only | home |

## 2. Data-quality observations (important for the backend model)
1. **`priceToman` is not derived from `originalPrice`.** Implied Toman-per-unit varies widely:
   - TRY: p5 = 1,500, p7 = 1,500, p12 = 1,643, p6 = 1,885, p16 = 1,885, p14 = 2,048, p1 = 2,067, p13 = 2,079, p2 = 2,167, p9 = 2,182
   - AED: p8 = 16,931, p15 = 18,444, p10 = 21,765, p11 = 21,905, p3 = 26,552, p4 = 27,449
   So there is no visible formula (FX × margin × shipping × duty). A real pricing engine needs rules you have not given yet.
2. `discount` is applied to the Toman price; it is unclear whether it comes from the source store or is Maral's own promotion.
3. `source: "Maral"` items (p13, p16) still carry a TRY `originalPrice`, so "own stock" items have a foreign origin price.
4. `fulfillment: "stock"` appears on Trendyol/Namshi-sourced items (p2, p4, p5, p9, p10, p12) — i.e. "stock" means "Maral already holds it", independent of `source`. Two orthogonal fields: **origin store** and **fulfillment mode**.
5. `available` is product-level; sizes/colors have no individual stock. No quantity on hand. No SKU/variant id.
6. Colors are keyed by display name (Persian) in cart and filters; sizes are free strings.
7. Product ids are `"p1"`… strings; there is no slug.
8. `isNew` is a flag, not a date. "popular" sort has no popularity data (it is simply array order).
9. Categories are a flat list of 6 (no hierarchy; "کیف و اکسسوری" nav entry maps only to `bags`; "پالتو و کت"-style mega-menu links are free-text searches).
10. Specification tab only shows brand, category, original price, fulfillment, sizes — there are **no real specifications** (material, care, origin, SKU).
11. Persian digits are produced by display helpers — **data must stay numeric/ASCII**.

## 3. Replacement plan (mock → API)
| Mock | Replaced by | Notes |
|---|---|---|
| `products` array | `GET /products`, `GET /products/{id}` | keep field names close to current `Product` to minimise frontend change; add `slug`, `variants`, `price` object |
| `categories`, `navCategories` | `GET /categories` | descriptions/images become CMS data; kids/brands decided separately |
| `brands` | `GET /brands` | includes product counts |
| source list | part of `GET /products` facets or `GET /sources` | |
| `finalPrice()` | server-computed `price.final_toman` | remove client rounding; keep the helper for demo mode only |
| `shippingFor`, express cost, delivery estimates, free-shipping banner | `GET /config/storefront` and `POST /checkout/quote` | single source of truth |
| local cart totals | `GET /cart` / quote response | server must re-price |
| `reviews` | `GET /products/{id}/reviews` (or hidden until policy decided) | |
| size guide | per-category size chart in product detail response | |
| `orders` | `GET /orders` | |
| customer/address | `GET /me`, `/me/addresses` | |
| `reply()` | `POST /assistant/messages` | |
| `maral-cart/wish/recent` | wishlist & cart server-side for logged-in users; `recent` can stay client-only | guest behaviour to decide |
| images | CDN/media URLs in API (`images[].url`, `alt`) | |

## 4. Keeping demo mode
Recommended (for a later, approved phase — not done now):
- Introduce a thin data-access layer (e.g. `src/lib/api/` with functions `getProducts`, `getProduct`, …) with **two implementations**: `mock` (re-exporting today's `shop-data.ts` logic) and `http`. Selected by an env flag such as `VITE_DATA_MODE=mock|api`.
- Leave `shop-data.ts` untouched as the mock source; add adapters that map API DTOs → the existing `Product` type so components do not change.
- Use TanStack Query (already installed) and route loaders for fetching; add skeleton/empty/error states per page (see API_REQUIREMENTS §3).
- Seed the Django database from the same 16 products so demo and API show identical data.
