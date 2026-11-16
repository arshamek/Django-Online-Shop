# Component Inventory

## Shop components (`src/components/shop/`)
| Component | File | Props / state | Data it needs | Interactions |
|---|---|---|---|---|
| `Header` | Header.tsx | `useShop` (totals, wishlist, lang) | `navCategories`, `categories`, `brands`, offer image | scroll style, mega menu, search open, language menu, cart open, links |
| `Logo` | Header.tsx | `className` | – | link home |
| `SearchOverlay` | Header.tsx | local `q` | products (name+brand substring, top 5) | type, quick suggestion chips (پالتو/کیف/کتانی/عطر), submit → `/shop?q=`, click result → product |
| `BottomNav` | Header.tsx | – | cart count | mobile nav, opens chat/cart |
| `Footer` | Footer.tsx | – | static columns | all links → `/shop` |
| `ProductCard` | ProductCard.tsx | `p: Product` | id, brand, name, images[0..1], priceToman, discount, isNew, source, available, wishlist state | link to product, hover image swap, wishlist toggle, quick view |
| `PriceDisplay` | ProductCard.tsx | `p`, `large` | `finalPrice`, `priceToman`, `discount` | – |
| `SourceBadge` | ProductCard.tsx | `p` | `source` | – |
| `FulfillmentBadge` | ProductCard.tsx | `p` | `fulfillment` | – |
| `WishlistButton` | ProductCard.tsx | `id` | wishlist ids | toggle, heart animation |
| `QuickView` | ProductCard.tsx | context `quickView` | brand, name, price, fulfillment, sizes, images[0], available | pick size, add to cart (first color), link to details |
| `CartDrawer` | CartDrawer.tsx | context | cart lines → product (image, brand, name, unit price) | qty +/−, remove, totals, go to checkout; empty state |
| `Row` | CartDrawer.tsx | `k`,`v`,`bold` | – | shared label/value row (also used by checkout) |
| `AIChat` | AIChat.tsx | local `msgs`, `typing`, `input` | products (scripted) | open/minimize/close(reset), 4 suggestion chips, send, product cards link out |

## Route-local components
| Component | Where | Notes |
|---|---|---|
| `Home`, `SectionHead` | index.tsx | newsletter form (local only), AI teaser mock chat |
| `Shop`, `Group`, `Check` | shop.tsx | 8 filter groups, sort select, mobile filter sheet |
| `ProductPage` | product.$id.tsx | gallery + hover zoom, color, size (required), qty, add/buy-now, delivery estimate, tabs (desc/spec/reviews), size-guide modal, related (same category, 4), recently viewed |
| `Checkout` | checkout.tsx | stepper, per-step validation, shipping choice, summary sidebar, success screen |
| `Account` | account.tsx | 5 tabs + fake logout |
| `WishlistGrid`, `Wishlist` | wishlist.tsx | exported and reused by account |

## Library layer
| Module | Role |
|---|---|
| `lib/shop-data.ts` | types (`Product`, `CategoryId`, `Source`, `Fulfillment`), demo data, `finalPrice`, `shippingFor`, `toman`, `faDigits`, nav config, image map |
| `lib/shop-store.tsx` | `ShopProvider`, `useShop`, `cartTotals`, `usePersisted` |
| `lib/error-*.ts`, `lovable-error-reporting.ts` | Lovable error capture/reporting (platform tooling) |

## shadcn/ui
~45 primitives exist in `components/ui/`; the shop screens use **none** of them (all hand-styled Tailwind). `skeleton.tsx`, `form.tsx`, `sonner.tsx` exist but are unused — no loading skeletons, no toasts.

## TypeScript interfaces found
```ts
Product { id, brand, name, originalPrice, originalCurrency: "TRY"|"AED"|"IRT", priceToman, discount,
          category, source: "Trendyol"|"Namshi"|"Maral", images: string[], sizes: string[],
          colors: {name, value}[], available, fulfillment: "stock"|"order", isNew?, description }
CategoryId = "women"|"men"|"shoes"|"bags"|"accessories"|"beauty"
CartItem { id, size, color, qty }      Lang = "fa"|"en"|"ar"|"tr"
Msg { from: "bot"|"me", text, items?: Product[] }   (AIChat, local)
Search { cat?, q?, brand?, source?, sale?, sort? }   (shop route)
```
