# User Interaction Inventory

Legend — **Persist**: where state lives today. **Backend**: what a real backend must do.

## Global / shell
| # | Interaction | Component | Persist today | Backend |
|---|---|---|---|---|
| G1 | Free-shipping banner (text) | Header | static | config value |
| G2 | Hover nav item → mega menu (categories, popular searches, brands, offer tile) | Header | – | categories/brands/promo |
| G3 | Click nav item → `/shop` with `cat`/`sale` | Header | – | – |
| G4 | Open search overlay; type → top-5 live results; chips; submit → `/shop?q=` | SearchOverlay | – | search endpoint (debounced) |
| G5 | Switch language (header globe, mobile menu, account tab) | Header/Account | localStorage | save to profile; i18n content |
| G6 | Open account / wishlist (desktop icons, mobile nav) | Header | – | auth |
| G7 | Open cart drawer | Header/BottomNav | – | – |
| G8 | Open AI chat (floating button, BottomNav, home CTA) | AIChat | – | – |
| G9 | Mobile menu | Header | – | – |
| G10 | Footer links (all → `/shop`), social links (`#`) | Footer | – | static pages needed |

## Home
| # | Interaction | Backend |
|---|---|---|
| H1 | Hero CTAs: all products / newest (`sort=new`) | – |
| H2 | Category tiles → `/shop?cat=` | categories |
| H3 | "Trending" (8 discounted products), "View all" | products?sale |
| H4 | Source tiles Trendyol/Namshi → `/shop?source=` | sources |
| H5 | "New" horizontal carousel (isNew) | products?is_new |
| H6 | Brand links → `/shop?brand=` | brands |
| H7 | Offer banner → `/shop?sale=true` | promo content |
| H8 | "Start chat" | – |
| H9 | Newsletter email submit (regex validated, success message "demo") | newsletter subscribe |

## Shop listing
| # | Interaction | Notes |
|---|---|---|
| S1 | Filters: category (multi), brand (multi), price max slider 2M–50M, size, color, source, only-sale, only-available | server-side filtering + facets with counts |
| S2 | Sort: popular (no-op), new, price low, price high, discount | needs `popularity` ranking |
| S3 | Free-text `q` (name+brand substring, case-insensitive; Persian) | Persian-aware search |
| S4 | "Show more" (+8; initial 8) | pagination or cursor |
| S5 | Reset filters | – |
| S6 | Mobile filter sheet | – |
| S7 | Empty result message | empty state |
| S8 | Breadcrumb, category title/description, result count | category metadata |
| S9 | Filters are **not** reflected in URL after first load | consider URL-sync (frontend gap) |

## Product card / quick view
| # | Interaction | Persist/Backend |
|---|---|---|
| P1 | Click card → product page | – |
| P2 | Hover → second image; badges: discount %, "new", source, "unavailable" overlay | images, flags |
| P3 | Toggle wishlist heart | wishlist add/remove |
| P4 | Quick view (desktop): choose size, add to cart (first color) | cart add |

## Product page
| # | Interaction | Backend |
|---|---|---|
| D1 | Gallery thumbnails + hover zoom (1.8×) + wishlist | images |
| D2 | Select color (default first) | variant |
| D3 | Select size (required; error "please choose size") | variant |
| D4 | Size guide modal | size chart |
| D5 | Quantity +/− (min 1, no max) | stock/limits unknown |
| D6 | Add to cart (opens drawer) / Buy now (add + go to checkout) | cart |
| D7 | Buttons disabled when unavailable | availability |
| D8 | Delivery estimate by fulfillment | config/estimate |
| D9 | Tabs: description, specs, reviews (2 samples, flagged as demo) | specs/reviews |
| D10 | Related (same category, 4); recently viewed (client ids) | related; hydrate ids |
| D11 | Breadcrumb; SEO head from product | – |
| D12 | Not-found state | 404 |

## Cart drawer
| # | Interaction | Backend |
|---|---|---|
| C1 | List lines (image, brand, name, size, color, unit price) | cart |
| C2 | Qty +/− (min 1), remove | cart line update |
| C3 | Totals: subtotal, shipping (estimate), total | server totals |
| C4 | Proceed to checkout; empty state with link | – |

## Checkout (5 steps, guest-capable, no login required)
| # | Step | Fields / validation | Backend |
|---|---|---|---|
| K1 | Customer | name ≥3 chars; mobile `09…` (Latin or Persian digits); email optional | customer/contact |
| K2 | Address | city, full address ≥10 chars, postal code 10 digits | address (no province, recipient, unit, geo) |
| K3 | Shipping | standard (450k or free ≥20M) / express 950k | shipping options |
| K4 | Summary | lines × price, "ship to" text | quote |
| K5 | Fake payment | notice text; button creates `MRL-######` locally, clears cart | order + payment |
| K6 | Success screen | tracking code, "continue shopping" | order confirmation |
| K7 | Empty-cart guard | link to shop | – |
| K8 | Back/forward between steps; stepper UI | – |

## Account
| # | Interaction | Backend |
|---|---|---|
| A1 | Profile view (name, mobile, email, birthday) — read-only | `/me` (+ edit missing) |
| A2 | Orders list (code, Jalali date, status text, total) — no detail view | `/orders` |
| A3 | Addresses (one, read-only; no add/edit/delete) | `/me/addresses` |
| A4 | Wishlist tab (add to cart with first size/color, remove) | wishlist |
| A5 | Language buttons | `/me` |
| A6 | Logout (fake screen + "log in again") | auth |

## AI chat
| # | Interaction | Backend |
|---|---|---|
| AI1 | Open / minimize / close (close resets conversation to greeting) | conversation lifecycle |
| AI2 | 4 suggestion chips (budget sneaker, new women's bags, compare two products, winter style) | prompt starters |
| AI3 | Send message → 1.1 s typing → text + horizontal product cards linking to product page | assistant response w/ product refs |
| AI4 | "Compare two products" currently hard-coded to p3/p4 (no product selection UI) | comparison needs context |
