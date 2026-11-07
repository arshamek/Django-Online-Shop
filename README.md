# Maral Online Shop — demo storefront

A Persian, right-to-left fashion marketplace demo ( homepage, shop with filters,
product page, cart, five-step checkout, wishlist, account, and a scripted "Maral AI"
chat panel ).

Everything runs in the browser: there is **no database, no login, no API key and no
`.env` file** to configure. All products, brands, prices and the sample customer are
hard-coded demo data.

---

## 1. What you need

| Tool     | Version                                            |
| -------- | -------------------------------------------------- |
| Node.js  | **20.19+ or 22.12+** — Node 22 LTS is the safe pick  |
| npm      | 10 or newer (comes with Node)                       |

Check what you have:

```sh
node -v
npm -v
```

If you need Node, the easiest route is [nvm](https://github.com/nvm-sh/nvm#installing-and-updating):

```sh
nvm install 22
nvm use 22
```

## 2. Run it

```sh
unzip maral-online-shop.zip
cd maral-online-shop
npm install --legacy-peer-deps
npm run dev
```

Open **http://localhost:5173** in your browser.

`npm install` downloads the dependencies into a `node_modules` folder the first time
— that takes a minute or two and is normal. You only need it once.

> `--legacy-peer-deps` is needed because npm 10 hits a known bug resolving one of
> this project's test dependencies. Without it you may see
> `Cannot read properties of null (reading 'edgesOut')`.
>
> Already have [Bun](https://bun.sh)? `bun install && bun run dev` works too, needs no
> flag, is faster, and `bun.lock` is included.

## 3. Other commands

```sh
npm run build      # production build into dist/
npm run preview    # serve the built output locally
npm test           # run the pricing / cart rule tests
npm run lint       # eslint
npm run format     # prettier
```

## 4. Where things live

```text
src/
  routes/
    index.tsx          homepage (hero, categories, popular picks, brands, offers, AI, newsletter)
    shop.tsx           product list + filters (category, brand, price, size, color, store, discount, stock)
    product.$id.tsx    product page (gallery, colors, sizes, quantity, size guide, delivery estimate)
    checkout.tsx       five-step checkout ending in a fake "test payment"
    wishlist.tsx       saved items
    account.tsx        sample customer profile and order history
    __root.tsx         shared layout, header/footer, fonts, <head> metadata
  components/shop/     Header, Footer, ProductCard, CartDrawer, AIChat
  lib/
    shop-data.ts       ALL demo data: 16 products, brands, prices, categories, shipping rules
    shop-store.tsx     cart + wishlist state (kept in localStorage, so it survives a refresh)
  assets/              the product photos (AI-generated placeholders)
  styles.css           Tailwind v4 theme: colors, fonts, glass/cream look
public/                favicon, robots.txt
```

### Changing the demo content

- **Products, prices, brands, categories** → `src/lib/shop-data.ts`
- **Free-shipping threshold and shipping fee** → same file (`FREE_SHIPPING_LIMIT`,
  `SHIPPING_COST`)
- **Photos** → drop your own files into `src/assets/` and update the imports in
  `src/lib/shop-data.ts`
- **Colors / fonts** → `src/styles.css`

### Known gaps in the demo

- The language switcher (English / Arabic / Turkish) only records your choice — the
  text stays Persian.
- The "Maral AI" chat answers from pre-written replies; it is not connected to a real
  model.
- Checkout ends in a simulated payment; no money moves and no card is charged.
- The kids category and the brands page are not built — those links open the general
  shop.

## 5. Built with

TanStack Start v1 (React 19 + Vite 8), TypeScript, Tailwind CSS v4, shadcn/ui,
React Hook Form + Zod, lucide-react.

## 6. Troubleshooting

- **`npm install` fails or the dev server won't start** — check `node -v`; anything
  older than 20.19 will break. Then delete `node_modules` and `package-lock.json`
  and install again with `npm install --legacy-peer-deps`.
- **Port 5173 is busy** — `npm run dev -- --port 3000`
- **Blank page after a change** — run `npm run build` to see the real error message,
  or `npm test` to check the data rules.
