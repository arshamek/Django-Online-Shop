# Frontend Inventory — MaralOnlineShop

Audit of `arshamek/Django-Online-Shop` @ `main` (HEAD `9101f48`, 3 commits). Read-only audit; nothing in the frontend was changed.

## 1. What the repository actually is
- Despite the repo name, it contains **only a frontend**. There is no Django code, no `requirements.txt`, no `manage.py`.
- Generated with **Lovable** (see `AGENTS.md`: avoid rewriting published history, keep `main` working).
- Everything runs in the browser. README: "no database, no login, no API key, no `.env`". All data is hard-coded demo data.
- **Hygiene issue:** `README.md` contains committed merge-conflict markers (`<<<<<<< HEAD`, `=======`, `>>>>>>>`) from the merge commit `9101f48`.

## 2. Stack
| Area | Finding |
|---|---|
| Framework | TanStack Start v1 (`@tanstack/react-start` 1.168.60) + TanStack Router 1.170.41 (file-based routes), SSR via Nitro (`nitro` 3.0.260603-beta) |
| UI | React 19, TypeScript 5.8, Tailwind CSS v4 (CSS-first config in `src/styles.css`, no `tailwind.config.*`), shadcn/ui (`components.json`), lucide-react |
| Data libs installed | `@tanstack/react-query` (QueryClient created in `router.tsx`, **no `useQuery` anywhere**), `react-hook-form` + `zod` + `@hookform/resolvers` (**unused**; checkout validates by hand) |
| Build/tooling | Vite 8.1.5, Bun (`bun.lock`) **and** npm (`package-lock.json`) lockfiles both committed, ESLint 9, Prettier, Vitest 4 + Testing Library + jsdom |
| Tests | 2 files: pricing/shipping/cart-total rules (`shop-rules.test.ts`), one routing smoke test |
| Node | README says 20.19+ / 22.12+; `npm install --legacy-peer-deps` required |
| Env | No `.env`, no `import.meta.env` usage, no API base URL concept yet |

## 3. Project structure
```
src/
  routes/        __root, index, shop, product.$id, checkout, account, wishlist (+ README.md)
  components/shop/   Header(+Logo, BottomNav, SearchOverlay), Footer, ProductCard(+PriceDisplay, SourceBadge,
                     FulfillmentBadge, WishlistButton, QuickView), CartDrawer(+Row), AIChat
  components/ui/     ~45 shadcn primitives (none are imported by the shop routes or shop components)
  lib/           shop-data.ts (ALL demo data + pricing helpers), shop-store.tsx (cart/wishlist/lang/UI state),
                 utils.ts, error-capture.ts, error-page.ts, lovable-error-reporting.ts
  assets/        14 AI-generated placeholder JPGs
  server.ts / start.ts   SSR entry, 500-page normalizer, CSRF middleware for server functions
  styles.css     Tailwind v4 theme tokens (oklch), glass/cream look, Vazirmatn + Cormorant Garamond
```

## 4. State management (all client-side)
`ShopProvider` (React context, `src/lib/shop-store.tsx`) holds:
| State | Persistence | Shape |
|---|---|---|
| `cart` | localStorage `maral-cart` | `{ id, size, color, qty }[]` — line identity = id+size+color; index-based `updateQty/removeItem` |
| `wishlist` | localStorage `maral-wish` | `string[]` of product ids |
| `recent` | localStorage `maral-recent` | last 8 viewed product ids |
| `lang` | localStorage `maral-lang` | `"fa" \| "en" \| "ar" \| "tr"` — **recorded only, does nothing** |
| `cartOpen`, `chatOpen`, `quickView` | memory | UI only |
| `totals` | derived | subtotal/shipping/total/count computed from local `products` |

Cart stores **no price**: price is looked up live from the local catalog each render.

## 5. Routing & shell
- Root: `<html lang="fa" dir="rtl">`, fonts from Google Fonts, per-route `head()` SEO metadata (Persian).
- Persistent shell: Header, Footer, CartDrawer, QuickView modal, AIChat panel, mobile BottomNav.
- 404 and error components are **English-only** and generic (inconsistent with the Persian UI).

## 6. RTL / language
- RTL is hard-wired at the root and uses logical utilities (`start-*`, `end-*`, `ms-*`, `-scale-x-100` on send icon). Latin brand names and phone/email inputs force `dir="ltr"`.
- Language selector (header globe, mobile menu, account tab) stores a value only. No i18n library, no translation files, `dir`/`lang` never change (Arabic would need RTL kept, English/Turkish LTR).
- Persian digits/formatting done by helpers `faDigits()` and `toman()`; money is shown only in Toman.

## 7. Dead or placeholder items
- Nav "بچگانه" (kids) and "برندها" (brands) have no destination (`cat: undefined`, go to `/shop`).
- All footer links point to `/shop`; social links are `#`; phone/email are placeholders; trust badges are plain text.
- `IRT` exists in the `originalCurrency` type but no product uses it.
- `QuickView`, `WishlistGrid` silently choose the first size/color.

## 8. Business rules embedded in code (demo-level; need your confirmation)
| Rule | Where |
|---|---|
| Final price = `priceToman × (100 − discount)/100`, rounded to nearest 1,000 Toman | `finalPrice()` + unit test |
| Standard shipping 450,000 Toman, free from subtotal ≥ 20,000,000 | `shippingFor()` + test; also repeated in header banner text |
| Express shipping 950,000 Toman, **never free** | hard-coded in `checkout.tsx` |
| Delivery estimate: stock 2–4 working days, order 14–21 working days | hard-coded in `product.$id.tsx` |
| Variant required before add-to-cart on product page (size only; color defaults to first) | `product.$id.tsx` |
| Cart merges lines with same id+size+color | `addToCart` |
| Order code format `MRL-<6 digits>` | `checkout.tsx` (random, client-side) |
| Iran mobile `09xxxxxxxxx`, postal code exactly 10 digits, address ≥ 10 chars, name ≥ 3 chars | `checkout.tsx` |
