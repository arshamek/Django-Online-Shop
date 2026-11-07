import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { finalPrice, products, shippingFor, type Product } from "./shop-data";

export interface CartItem { id: string; size: string; color: string; qty: number }
export type Lang = "fa" | "en" | "ar" | "tr";

interface Store {
  cart: CartItem[];
  wishlist: string[];
  recent: string[];
  lang: Lang;
  cartOpen: boolean;
  chatOpen: boolean;
  quickView: Product | null;
  setCartOpen: (v: boolean) => void;
  setChatOpen: (v: boolean) => void;
  setQuickView: (p: Product | null) => void;
  setLang: (l: Lang) => void;
  addToCart: (item: CartItem) => void;
  updateQty: (i: number, qty: number) => void;
  removeItem: (i: number) => void;
  clearCart: () => void;
  toggleWish: (id: string) => void;
  pushRecent: (id: string) => void;
  totals: { subtotal: number; shipping: number; total: number; count: number };
}

const Ctx = createContext<Store | null>(null);

export function cartTotals(cart: CartItem[]) {
  const subtotal = cart.reduce((s, c) => {
    const p = products.find((x) => x.id === c.id);
    return s + (p ? finalPrice(p) * c.qty : 0);
  }, 0);
  const shipping = shippingFor(subtotal);
  return { subtotal, shipping, total: subtotal + shipping, count: cart.reduce((s, c) => s + c.qty, 0) };
}

function usePersisted<T>(key: string, init: T) {
  const [v, setV] = useState<T>(init);
  const [ready, setReady] = useState(false);
  useEffect(() => {
    try { const s = localStorage.getItem(key); if (s) setV(JSON.parse(s)); } catch { /* ignore */ }
    setReady(true);
  }, [key]);
  useEffect(() => { if (ready) localStorage.setItem(key, JSON.stringify(v)); }, [key, v, ready]);
  return [v, setV] as const;
}

export function ShopProvider({ children }: { children: ReactNode }) {
  const [cart, setCart] = usePersisted<CartItem[]>("maral-cart", []);
  const [wishlist, setWish] = usePersisted<string[]>("maral-wish", []);
  const [recent, setRecent] = usePersisted<string[]>("maral-recent", []);
  const [lang, setLang] = usePersisted<Lang>("maral-lang", "fa");
  const [cartOpen, setCartOpen] = useState(false);
  const [chatOpen, setChatOpen] = useState(false);
  const [quickView, setQuickView] = useState<Product | null>(null);

  const store: Store = {
    cart, wishlist, recent, lang, cartOpen, chatOpen, quickView,
    setCartOpen, setChatOpen, setQuickView, setLang,
    addToCart: (item) => {
      setCart((c) => {
        const i = c.findIndex((x) => x.id === item.id && x.size === item.size && x.color === item.color);
        if (i >= 0) return c.map((x, j) => (j === i ? { ...x, qty: x.qty + item.qty } : x));
        return [...c, item];
      });
      setCartOpen(true);
    },
    updateQty: (i, qty) => setCart((c) => c.map((x, j) => (j === i ? { ...x, qty: Math.max(1, qty) } : x))),
    removeItem: (i) => setCart((c) => c.filter((_, j) => j !== i)),
    clearCart: () => setCart([]),
    toggleWish: (id) => setWish((w) => (w.includes(id) ? w.filter((x) => x !== id) : [...w, id])),
    pushRecent: (id) => setRecent((r) => [id, ...r.filter((x) => x !== id)].slice(0, 8)),
    totals: cartTotals(cart),
  };
  return <Ctx.Provider value={store}>{children}</Ctx.Provider>;
}

export function useShop() {
  const c = useContext(Ctx);
  if (!c) throw new Error("useShop outside provider");
  return c;
}
