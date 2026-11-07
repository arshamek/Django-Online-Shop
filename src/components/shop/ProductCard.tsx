import { Link } from "@tanstack/react-router";
import { Eye, Heart, ShoppingBag } from "lucide-react";
import { useState } from "react";
import { finalPrice, toman, faDigits, type Product } from "@/lib/shop-data";
import { useShop } from "@/lib/shop-store";
import { cn } from "@/lib/utils";

export function PriceDisplay({ p, large }: { p: Product; large?: boolean }) {
  return (
    <div className="flex flex-wrap items-baseline gap-x-2">
      <span className={cn("font-semibold text-foreground", large ? "text-2xl" : "text-sm")}>{toman(finalPrice(p))}</span>
      {p.discount > 0 && <span className={cn("text-muted-foreground line-through", large ? "text-base" : "text-xs")}>{toman(p.priceToman)}</span>}
    </div>
  );
}

export function SourceBadge({ p }: { p: Product }) {
  return (
    <span className="glass rounded-full px-2.5 py-1 text-[10px] font-medium text-foreground">
      {p.source === "Maral" ? "مارال" : p.source}
    </span>
  );
}

export function FulfillmentBadge({ p }: { p: Product }) {
  return p.fulfillment === "stock" ? (
    <span className="rounded-full bg-success/10 px-3 py-1 text-xs font-medium text-success">موجود در انبار مارال</span>
  ) : (
    <span className="rounded-full bg-accent/15 px-3 py-1 text-xs font-medium text-accent">قابل سفارش از فروشگاه خارجی</span>
  );
}

export function WishlistButton({ id, className }: { id: string; className?: string }) {
  const { wishlist, toggleWish } = useShop();
  const on = wishlist.includes(id);
  const [pop, setPop] = useState(0);
  return (
    <button
      aria-label={on ? "حذف از علاقه‌مندی‌ها" : "افزودن به علاقه‌مندی‌ها"}
      onClick={(e) => { e.preventDefault(); e.stopPropagation(); toggleWish(id); setPop((x) => x + 1); }}
      className={cn("glass grid size-9 place-items-center rounded-full transition hover:scale-105", className)}
    >
      <Heart key={pop} className={cn("size-4", pop && "animate-heart", on ? "fill-destructive text-destructive" : "text-foreground")} />
    </button>
  );
}

export function ProductCard({ p }: { p: Product }) {
  const { setQuickView } = useShop();
  return (
    <div className="group relative">
      <Link to="/product/$id" params={{ id: p.id }} className="block">
        <div className="relative aspect-[3/4] overflow-hidden rounded-2xl bg-muted">
          <img src={p.images[0]} alt={p.name} loading="lazy" width={768} height={1024}
            className="absolute inset-0 size-full object-cover transition duration-700 ease-out group-hover:scale-105 group-hover:opacity-0" />
          <img src={p.images[1] ?? p.images[0]} alt="" loading="lazy" width={768} height={1024}
            className="absolute inset-0 size-full scale-105 object-cover opacity-0 transition duration-700 ease-out group-hover:scale-100 group-hover:opacity-100" />
          <div className="absolute inset-x-3 top-3 flex items-start justify-between">
            <div className="flex flex-col items-start gap-1.5">
              {p.discount > 0 && <span className="rounded-full bg-ink px-2.5 py-1 text-[10px] font-semibold text-ink-foreground">{faDigits(p.discount)}٪ تخفیف</span>}
              {p.isNew && <span className="rounded-full bg-card px-2.5 py-1 text-[10px] font-semibold">جدید</span>}
            </div>
            <WishlistButton id={p.id} />
          </div>
          <div className="absolute bottom-3 start-3"><SourceBadge p={p} /></div>
          {!p.available && <div className="absolute inset-0 grid place-items-center bg-background/50 text-sm font-medium">ناموجود</div>}
          <button
            onClick={(e) => { e.preventDefault(); setQuickView(p); }}
            className="glass absolute bottom-3 end-3 flex translate-y-2 items-center gap-1.5 rounded-full px-3 py-2 text-xs font-medium opacity-0 transition group-hover:translate-y-0 group-hover:opacity-100 max-md:hidden"
          >
            <Eye className="size-3.5" /> نمایش سریع
          </button>
        </div>
        <div className="mt-3 space-y-1 px-0.5">
          <p className="font-display text-sm tracking-wide text-muted-foreground" dir="ltr" style={{ textAlign: "right" }}>{p.brand}</p>
          <h3 className="line-clamp-1 text-sm text-foreground">{p.name}</h3>
          <PriceDisplay p={p} />
        </div>
      </Link>
    </div>
  );
}

export function QuickView() {
  const { quickView: p, setQuickView, addToCart } = useShop();
  const [size, setSize] = useState<string | null>(null);
  if (!p) return null;
  return (
    <div className="fixed inset-0 z-[70] grid place-items-center p-4 animate-in fade-in" onClick={() => setQuickView(null)}>
      <div className="absolute inset-0 bg-ink/40 backdrop-blur-sm" />
      <div className="glass relative grid w-full max-w-3xl overflow-hidden rounded-3xl bg-card md:grid-cols-2 animate-in zoom-in-95" onClick={(e) => e.stopPropagation()}>
        <img src={p.images[0]} alt={p.name} className="aspect-[3/4] size-full object-cover max-md:max-h-72" />
        <div className="flex flex-col gap-4 p-6">
          <button onClick={() => setQuickView(null)} className="self-end text-sm text-muted-foreground hover:text-foreground">بستن ✕</button>
          <p className="font-display text-lg text-muted-foreground">{p.brand}</p>
          <h3 className="text-xl font-semibold">{p.name}</h3>
          <PriceDisplay p={p} large />
          <FulfillmentBadge p={p} />
          <div className="flex flex-wrap gap-2">
            {p.sizes.map((s) => (
              <button key={s} onClick={() => setSize(s)} className={cn("min-w-11 rounded-full border px-3 py-2 text-xs transition", size === s ? "border-foreground bg-foreground text-background" : "hover:border-foreground")}>{s}</button>
            ))}
          </div>
          <div className="mt-auto flex gap-2">
            <button disabled={!p.available} onClick={() => { addToCart({ id: p.id, size: size ?? p.sizes[0] ?? "", color: p.colors[0]?.name ?? "", qty: 1 }); setQuickView(null); }}
              className="flex flex-1 items-center justify-center gap-2 rounded-full bg-primary py-3 text-sm font-medium text-primary-foreground transition hover:opacity-90 disabled:opacity-40">
              <ShoppingBag className="size-4" /> {p.available ? "افزودن به سبد" : "ناموجود"}
            </button>
            <Link to="/product/$id" params={{ id: p.id }} onClick={() => setQuickView(null)} className="rounded-full border px-4 py-3 text-sm hover:border-foreground">جزئیات</Link>
          </div>
        </div>
      </div>
    </div>
  );
}
