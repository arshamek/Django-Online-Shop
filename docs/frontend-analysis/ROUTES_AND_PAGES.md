# Routes & Pages

All routes are file-based (TanStack Start). Source: `src/routes/`.

| URL | File | Purpose | Search params / path params | Data source today |
|---|---|---|---|---|
| `/` | `index.tsx` | Home: hero, 6 categories, "trending" (first 8 with discount>0), 2 source tiles (Trendyol/Namshi), "new" carousel (`isNew`), brand list, limited-offer banner, AI teaser, trust icons, newsletter | – | `products`, `categories`, `brands`, `images` from `shop-data.ts` |
| `/shop` | `shop.tsx` | Listing + filters + sort + "show more" | `cat`, `q`, `brand`, `source`, `sale`, `sort` (URL seeds initial state only; later filter changes are **not** written back to the URL) | local `products` filtered in `useMemo` |
| `/product/$id` | `product.$id.tsx` | Product detail | `id` (e.g. `p1`); `loader` is synchronous and throws `notFound()` | local `products.find` |
| `/checkout` | `checkout.tsx` | 5-step checkout (customer → address → shipping → summary → fake payment) → success screen | – | `cart` (localStorage), local totals |
| `/account` | `account.tsx` | Tabs: profile, orders, addresses, wishlist, language; fake logout | – | hard-coded user "سارا محمدی", 2 orders, 1 address |
| `/wishlist` | `wishlist.tsx` | Saved products grid (also embedded in account tab) | – | `wishlist` ids + local `products` |
| (root) 404 | `__root.tsx` | English "Page not found" | – | – |
| (root) error | `__root.tsx` | English "This page didn't load" + retry | – | – |

## Overlays that behave like pages
| Overlay | Component | Opened from |
|---|---|---|
| Cart drawer | `CartDrawer` | header bag icon, mobile BottomNav, add-to-cart |
| Search overlay | `SearchOverlay` (in Header.tsx) | header search icon |
| Mega menu | Header | hover on desktop nav |
| Mobile menu | Header | hamburger |
| Quick view modal | `QuickView` | hover button on product card (desktop only) |
| Size guide modal | product page | "راهنمای سایز" |
| Filter drawer (mobile) | shop page | "فیلتر" button |
| AI chat panel | `AIChat` | floating button, home CTA, BottomNav "AI" |

## Missing pages (referenced by UI or implied by features)
- No login / register / OTP / password-reset screens (account page is always "logged in").
- No order detail / tracking page (footer "پیگیری سفارش" and trust icon exist).
- No brands page; no kids category; no static pages (FAQ, how to order from abroad, shipping info, returns policy, payment methods, authenticity guarantee, contact) — all footer links go to `/shop`.
- No standalone cart page (drawer only). No payment-result/callback page.
- No admin/seller UI (out of scope for storefront).
