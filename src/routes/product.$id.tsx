import { createFileRoute, Link, notFound, useNavigate } from "@tanstack/react-router";
import { Minus, Plus, Ruler, Star, Truck, X } from "lucide-react";
import { useEffect, useState } from "react";
import { categories, faDigits, products } from "@/lib/shop-data";
import { FulfillmentBadge, PriceDisplay, ProductCard, WishlistButton } from "@/components/shop/ProductCard";
import { useShop } from "@/lib/shop-store";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/product/$id")({
  loader: ({ params }) => {
    const p = products.find((x) => x.id === params.id);
    if (!p) throw notFound();
    return { p };
  },
  head: ({ loaderData }) => ({
    meta: loaderData
      ? [
          { title: `${loaderData.p.name} | ${loaderData.p.brand} — مارال` },
          { name: "description", content: loaderData.p.description },
          { property: "og:title", content: `${loaderData.p.name} — ${loaderData.p.brand}` },
          { property: "og:description", content: loaderData.p.description },
        ]
      : [{ title: "محصول یافت نشد — مارال" }, { name: "robots", content: "noindex" }],
  }),
  notFoundComponent: () => <div className="py-32 text-center">محصول پیدا نشد. <Link to="/shop" className="underline">بازگشت به فروشگاه</Link></div>,
  component: ProductPage,
});

const reviews = [
  { n: "نمونه کاربر ۱", r: 5, t: "کیفیت دوخت عالی بود و سایزبندی دقیق." },
  { n: "نمونه کاربر ۲", r: 4, t: "رنگ کمی روشن‌تر از عکس بود ولی خیلی راضی‌ام." },
];

function ProductPage() {
  const { p } = Route.useLoaderData();
  const { addToCart, pushRecent, recent } = useShop();
  const nav = useNavigate();
  const [img, setImg] = useState(0);
  const [size, setSize] = useState<string | null>(null);
  const [color, setColor] = useState(p.colors[0]?.name ?? "");
  const [qty, setQty] = useState(1);
  const [guide, setGuide] = useState(false);
  const [err, setErr] = useState(false);
  const [zoom, setZoom] = useState<{ x: number; y: number } | null>(null);
  const [tab, setTab] = useState<"desc" | "spec" | "rev">("desc");

  useEffect(() => { pushRecent(p.id); setImg(0); setSize(null); setColor(p.colors[0]?.name ?? ""); setQty(1); }, [p.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const add = (buy?: boolean) => {
    if (!size) { setErr(true); return; }
    addToCart({ id: p.id, size, color, qty });
    if (buy) nav({ to: "/checkout" });
  };
  const cat = categories.find((c) => c.id === p.category)!;
  const related = products.filter((x) => x.category === p.category && x.id !== p.id).slice(0, 4);
  const recentList = recent.filter((id) => id !== p.id).map((id) => products.find((x) => x.id === id)!).filter(Boolean).slice(0, 4);

  return (
    <div className="mx-auto max-w-7xl px-4 pt-10 md:px-6">
      <nav className="text-xs text-muted-foreground"><Link to="/">خانه</Link> / <Link to="/shop" search={{ cat: p.category }}>{cat.label}</Link> / <span className="text-foreground">{p.name}</span></nav>
      <div className="mt-6 grid gap-10 lg:grid-cols-[1.1fr_1fr]">
        <div className="flex gap-3 max-md:flex-col-reverse">
          <div className="flex gap-2 md:flex-col">
            {p.images.map((src, i) => <button key={i} onClick={() => setImg(i)} className={cn("w-20 overflow-hidden rounded-xl border-2 transition", img === i ? "border-foreground" : "border-transparent opacity-60")}><img src={src} alt="" className="aspect-[3/4] object-cover" /></button>)}
          </div>
          <div className="relative flex-1 cursor-zoom-in overflow-hidden rounded-3xl bg-muted"
            onMouseMove={(e) => { const r = e.currentTarget.getBoundingClientRect(); setZoom({ x: ((e.clientX - r.left) / r.width) * 100, y: ((e.clientY - r.top) / r.height) * 100 }); }}
            onMouseLeave={() => setZoom(null)}>
            <img src={p.images[img]} alt={p.name} className="aspect-[3/4] size-full object-cover transition-transform duration-300" style={zoom ? { transform: "scale(1.8)", transformOrigin: `${zoom.x}% ${zoom.y}%` } : undefined} />
            <WishlistButton id={p.id} className="absolute top-4 end-4 size-11" />
          </div>
        </div>

        <div className="space-y-6 lg:sticky lg:top-28 lg:self-start">
          <div>
            <p className="font-display text-xl text-muted-foreground">{p.brand}</p>
            <h1 className="mt-1 text-3xl font-semibold">{p.name}</h1>
          </div>
          <div className="flex items-center gap-3"><PriceDisplay p={p} large />{p.discount > 0 && <span className="rounded-full bg-ink px-3 py-1 text-xs text-ink-foreground">{faDigits(p.discount)}٪ تخفیف</span>}</div>
          <div className="flex flex-wrap gap-2"><FulfillmentBadge p={p} /><span className="rounded-full border px-3 py-1 text-xs">منبع: {p.source === "Maral" ? "مارال" : p.source}</span>
            <span className={cn("rounded-full px-3 py-1 text-xs", p.available ? "bg-success/10 text-success" : "bg-destructive/10 text-destructive")}>{p.available ? "موجود" : "ناموجود"}</span></div>

          <div><p className="mb-3 text-sm">رنگ: <span className="text-muted-foreground">{color}</span></p>
            <div className="flex gap-2">{p.colors.map((c) => <button key={c.name} aria-label={c.name} onClick={() => setColor(c.name)} className={cn("size-9 rounded-full border-2 ring-offset-2 ring-offset-background", color === c.name && "ring-2 ring-foreground")} style={{ background: c.value }} />)}</div></div>

          <div>
            <div className="mb-3 flex justify-between text-sm"><p>سایز</p><button onClick={() => setGuide(true)} className="flex items-center gap-1 text-muted-foreground underline underline-offset-4"><Ruler className="size-3.5" /> راهنمای سایز</button></div>
            <div className="flex flex-wrap gap-2">{p.sizes.map((z) => <button key={z} onClick={() => { setSize(z); setErr(false); }} className={cn("min-w-12 rounded-full border px-4 py-2.5 text-sm transition", size === z ? "border-foreground bg-foreground text-background" : "hover:border-foreground")}>{z}</button>)}</div>
            {err && <p className="mt-2 text-xs text-destructive">لطفاً سایز را انتخاب کنید.</p>}
          </div>

          <div className="flex items-center gap-3">
            <div className="flex items-center gap-3 rounded-full border px-2 py-1.5"><button aria-label="افزایش" onClick={() => setQty(qty + 1)} className="p-1.5"><Plus className="size-4" /></button><span className="w-5 text-center">{faDigits(qty)}</span><button aria-label="کاهش" onClick={() => setQty(Math.max(1, qty - 1))} className="p-1.5"><Minus className="size-4" /></button></div>
            <button disabled={!p.available} onClick={() => add()} className="flex-1 rounded-full bg-primary py-4 text-sm font-medium text-primary-foreground transition hover:opacity-90 disabled:opacity-40">افزودن به سبد خرید</button>
          </div>
          <button disabled={!p.available} onClick={() => add(true)} className="w-full rounded-full border border-foreground py-4 text-sm font-medium transition hover:bg-foreground hover:text-background disabled:opacity-40">خرید فوری</button>

          <div className="flex gap-3 rounded-2xl bg-card p-4 text-sm"><Truck className="size-5 shrink-0 text-accent" />
            <div><p className="font-medium">زمان تخمینی تحویل</p><p className="text-muted-foreground">{p.fulfillment === "stock" ? "۲ تا ۴ روز کاری از انبار مارال" : "۱۴ تا ۲۱ روز کاری (سفارش از فروشگاه خارجی)"}</p></div></div>
        </div>
      </div>

      <section className="mt-20">
        <div className="flex gap-6 border-b text-sm">{([["desc", "توضیحات"], ["spec", "مشخصات"], ["rev", "نظرات"]] as const).map(([k, l]) => <button key={k} onClick={() => setTab(k)} className={cn("-mb-px border-b-2 pb-3", tab === k ? "border-foreground" : "border-transparent text-muted-foreground")}>{l}</button>)}</div>
        <div className="max-w-2xl py-6 leading-8 text-muted-foreground">
          {tab === "desc" && <p>{p.description}</p>}
          {tab === "spec" && <dl className="grid grid-cols-2 gap-y-2 text-sm">{[["برند", p.brand], ["دسته", cat.label], ["قیمت مبدأ", `${faDigits(p.originalPrice)} ${p.originalCurrency}`], ["نوع تأمین", p.fulfillment === "stock" ? "انبار" : "سفارش خارجی"], ["سایزها", p.sizes.join("، ")]].map(([a, b]) => <div key={a} className="contents"><dt>{a}</dt><dd className="text-foreground">{b}</dd></div>)}</dl>}
          {tab === "rev" && <div className="space-y-4"><p className="text-xs">نظرات زیر نمونه نمایشی هستند و از مشتریان واقعی نیستند.</p>{reviews.map((r) => <div key={r.n} className="rounded-2xl bg-card p-4"><div className="flex items-center gap-2 text-foreground"><span className="text-sm font-medium">{r.n}</span><span className="flex">{Array.from({ length: r.r }).map((_, i) => <Star key={i} className="size-3 fill-accent text-accent" />)}</span></div><p className="mt-1 text-sm">{r.t}</p></div>)}</div>}
        </div>
      </section>

      {related.length > 0 && <section className="mt-12"><h2 className="mb-6 text-2xl font-semibold">محصولات مرتبط</h2><div className="grid grid-cols-2 gap-x-3 gap-y-8 md:grid-cols-4 md:gap-x-5">{related.map((x) => <ProductCard key={x.id} p={x} />)}</div></section>}
      {recentList.length > 0 && <section className="mt-16"><h2 className="mb-6 text-2xl font-semibold">بازدیدهای اخیر</h2><div className="grid grid-cols-2 gap-x-3 gap-y-8 md:grid-cols-4 md:gap-x-5">{recentList.map((x) => <ProductCard key={x.id} p={x} />)}</div></section>}

      {guide && (
        <div className="fixed inset-0 z-[70] grid place-items-center p-4" onClick={() => setGuide(false)}>
          <div className="absolute inset-0 bg-ink/40 backdrop-blur-sm" />
          <div className="glass relative w-full max-w-md rounded-3xl bg-background/90 p-6 animate-in zoom-in-95" onClick={(e) => e.stopPropagation()}>
            <div className="mb-4 flex justify-between"><h3 className="text-lg font-semibold">راهنمای سایز</h3><button onClick={() => setGuide(false)} aria-label="بستن"><X className="size-5" /></button></div>
            <table className="w-full text-center text-sm"><thead className="text-muted-foreground"><tr><th className="py-2">سایز</th><th>سینه (cm)</th><th>کمر (cm)</th></tr></thead>
              <tbody>{[["S", "۸۸", "۷۰"], ["M", "۹۴", "۷۶"], ["L", "۱۰۰", "۸۲"], ["XL", "۱۰۶", "۸۸"]].map((r) => <tr key={r[0]} className="border-t">{r.map((c) => <td key={c} className="py-2">{c}</td>)}</tr>)}</tbody></table>
          </div>
        </div>
      )}
    </div>
  );
}
