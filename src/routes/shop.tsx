import { createFileRoute, Link } from "@tanstack/react-router";
import { SlidersHorizontal, X } from "lucide-react";
import { useMemo, useState } from "react";
import { brands, categories, faDigits, finalPrice, products, toman, type CategoryId } from "@/lib/shop-data";
import { ProductCard } from "@/components/shop/ProductCard";
import { cn } from "@/lib/utils";

interface Search { cat?: CategoryId | undefined; q?: string | undefined; brand?: string | undefined; source?: string | undefined; sale?: boolean | undefined; sort?: string | undefined }

export const Route = createFileRoute("/shop")({
  validateSearch: (s: Record<string, unknown>): Search => ({
    cat: typeof s["cat"] === "string" ? (s["cat"] as CategoryId) : undefined,
    q: typeof s["q"] === "string" ? s["q"] : undefined,
    brand: typeof s["brand"] === "string" ? s["brand"] : undefined,
    source: typeof s["source"] === "string" ? s["source"] : undefined,
    sale: s["sale"] === true || s["sale"] === "true" ? true : undefined,
    sort: typeof s["sort"] === "string" ? s["sort"] : undefined,
  }),
  head: () => ({
    meta: [
      { title: "فروشگاه — MaralOnlineShop" },
      { name: "description", content: "کشف پوشاک، کفش، کیف و اکسسوری از برندهای بین‌المللی." },
      { property: "og:title", content: "فروشگاه مارال" },
      { property: "og:description", content: "محصولات منتخب از Trendyol و Namshi با قیمت تومانی." },
    ],
  }),
  component: Shop,
});

const MAX = 50_000_000;

function Shop() {
  const s = Route.useSearch();
  const [cats, setCats] = useState<string[]>(s.cat ? [s.cat] : []);
  const [selBrands, setBrands] = useState<string[]>(s.brand ? [s.brand] : []);
  const [sources, setSources] = useState<string[]>(s.source ? [s.source] : []);
  const [sizes, setSizes] = useState<string[]>([]);
  const [colors, setColors] = useState<string[]>([]);
  const [maxPrice, setMax] = useState(MAX);
  const [onlySale, setSale] = useState(!!s.sale);
  const [onlyAvail, setAvail] = useState(false);
  const [sort, setSort] = useState(s.sort ?? "popular");
  const [drawer, setDrawer] = useState(false);
  const [shown, setShown] = useState(8);

  // re-sync when the URL changes (e.g. via the header menu)
  const key = JSON.stringify(s);
  const [lastKey, setLastKey] = useState(key);
  if (key !== lastKey) {
    setLastKey(key); setCats(s.cat ? [s.cat] : []); setBrands(s.brand ? [s.brand] : []); setSources(s.source ? [s.source] : []); setSale(!!s.sale); if (s.sort) setSort(s.sort);
  }

  const list = useMemo(() => {
    let r = products.filter((p) =>
      (!cats.length || cats.includes(p.category)) &&
      (!selBrands.length || selBrands.includes(p.brand)) &&
      (!sources.length || sources.includes(p.source)) &&
      (!sizes.length || p.sizes.some((z) => sizes.includes(z))) &&
      (!colors.length || p.colors.some((c) => colors.includes(c.name))) &&
      finalPrice(p) <= maxPrice && (!onlySale || p.discount > 0) && (!onlyAvail || p.available) &&
      (!s.q || (p.name + p.brand).toLowerCase().includes(s.q.toLowerCase())));
    if (sort === "low") r = [...r].sort((a, b) => finalPrice(a) - finalPrice(b));
    if (sort === "high") r = [...r].sort((a, b) => finalPrice(b) - finalPrice(a));
    if (sort === "new") r = [...r].sort((a, b) => Number(!!b.isNew) - Number(!!a.isNew));
    if (sort === "discount") r = [...r].sort((a, b) => b.discount - a.discount);
    return r;
  }, [cats, selBrands, sources, sizes, colors, maxPrice, onlySale, onlyAvail, sort, s.q]);

  const cat = categories.find((c) => c.id === s.cat);
  const toggle = (arr: string[], set: (v: string[]) => void, v: string) => set(arr.includes(v) ? arr.filter((x) => x !== v) : [...arr, v]);
  const allSizes = Array.from(new Set(products.flatMap((p) => p.sizes)));
  const allColors = Array.from(new Map(products.flatMap((p) => p.colors).map((c) => [c.name, c])).values());
  const reset = () => { setCats([]); setBrands([]); setSources([]); setSizes([]); setColors([]); setMax(MAX); setSale(false); setAvail(false); };

  const filters = (
    <div className="space-y-7 text-sm">
      <Group t="دسته‌بندی">{categories.map((c) => <Check key={c.id} on={cats.includes(c.id)} onClick={() => toggle(cats, setCats, c.id)}>{c.label}</Check>)}</Group>
      <Group t="برند">{brands.map((b) => <Check key={b} on={selBrands.includes(b)} onClick={() => toggle(selBrands, setBrands, b)}><span className="font-display text-base">{b}</span></Check>)}</Group>
      <Group t="محدوده قیمت">
        <input type="range" min={2_000_000} max={MAX} step={1_000_000} value={maxPrice} onChange={(e) => setMax(+e.target.value)} className="w-full accent-[var(--accent)]" aria-label="حداکثر قیمت" />
        <p className="text-xs text-muted-foreground">تا {toman(maxPrice)}</p>
      </Group>
      <Group t="سایز"><div className="flex flex-wrap gap-1.5">{allSizes.map((z) => <button key={z} onClick={() => toggle(sizes, setSizes, z)} className={cn("rounded-full border px-3 py-1.5 text-xs", sizes.includes(z) && "border-foreground bg-foreground text-background")}>{z}</button>)}</div></Group>
      <Group t="رنگ"><div className="flex flex-wrap gap-2">{allColors.map((c) => <button key={c.name} title={c.name} aria-label={c.name} onClick={() => toggle(colors, setColors, c.name)} className={cn("size-8 rounded-full border-2 ring-offset-2 ring-offset-background transition", colors.includes(c.name) && "ring-2 ring-foreground")} style={{ background: c.value }} />)}</div></Group>
      <Group t="منبع محصول">{["Trendyol", "Namshi", "Maral"].map((x) => <Check key={x} on={sources.includes(x)} onClick={() => toggle(sources, setSources, x)}>{x === "Maral" ? "انبار مارال" : x}</Check>)}</Group>
      <Group t="وضعیت">
        <Check on={onlySale} onClick={() => setSale(!onlySale)}>فقط تخفیف‌دار</Check>
        <Check on={onlyAvail} onClick={() => setAvail(!onlyAvail)}>فقط کالاهای موجود</Check>
      </Group>
      <button onClick={reset} className="text-xs text-muted-foreground underline underline-offset-4">پاک کردن فیلترها</button>
    </div>
  );

  return (
    <div className="mx-auto max-w-7xl px-4 pt-10 md:px-6">
      <nav className="text-xs text-muted-foreground"><Link to="/" className="hover:text-foreground">خانه</Link> / <span>فروشگاه</span>{cat && <> / <span className="text-foreground">{cat.label}</span></>}</nav>
      <div className="mt-6 flex flex-wrap items-end justify-between gap-4 border-b pb-6">
        <div>
          <h1 className="text-3xl font-semibold md:text-5xl">{s.q ? `نتایج «${s.q}»` : cat?.label ?? (onlySale ? "تخفیف‌ها" : "همه محصولات")}</h1>
          <p className="mt-3 max-w-lg text-muted-foreground">{cat?.desc ?? "انتخاب‌های منتخب از فروشگاه‌های بین‌المللی، با قیمت نهایی به تومان."}</p>
        </div>
        <div className="flex items-center gap-3 text-sm">
          <span className="text-muted-foreground">{faDigits(list.length)} محصول</span>
          <select value={sort} onChange={(e) => setSort(e.target.value)} aria-label="مرتب‌سازی" className="rounded-full border bg-card px-4 py-2.5 outline-none">
            <option value="popular">محبوب‌ترین</option><option value="new">جدیدترین</option><option value="low">ارزان‌ترین</option><option value="high">گران‌ترین</option><option value="discount">بیشترین تخفیف</option>
          </select>
          <button onClick={() => setDrawer(true)} className="flex items-center gap-2 rounded-full border bg-card px-4 py-2.5 lg:hidden"><SlidersHorizontal className="size-4" /> فیلتر</button>
        </div>
      </div>
      <div className="mt-8 grid gap-10 lg:grid-cols-[240px_1fr]">
        <aside className="hidden lg:block">{filters}</aside>
        <div>
          {list.length === 0 ? <p className="py-20 text-center text-muted-foreground">محصولی با این فیلترها پیدا نشد.</p> : (
            <div className="grid grid-cols-2 gap-x-3 gap-y-8 md:grid-cols-3 md:gap-x-5">{list.slice(0, shown).map((p) => <ProductCard key={p.id} p={p} />)}</div>
          )}
          {shown < list.length && <div className="mt-12 text-center"><button onClick={() => setShown(shown + 8)} className="rounded-full border px-8 py-3 text-sm hover:border-foreground">نمایش بیشتر</button></div>}
        </div>
      </div>
      {drawer && (
        <div className="fixed inset-0 z-[70] lg:hidden" onClick={() => setDrawer(false)}>
          <div className="absolute inset-0 bg-ink/30 backdrop-blur-sm" />
          <div className="glass absolute inset-x-2 bottom-2 max-h-[85vh] overflow-y-auto rounded-3xl bg-background/90 p-6 animate-in slide-in-from-bottom" onClick={(e) => e.stopPropagation()}>
            <div className="mb-6 flex justify-between"><p className="text-lg font-semibold">فیلترها</p><button onClick={() => setDrawer(false)} aria-label="بستن"><X className="size-5" /></button></div>
            {filters}
            <button onClick={() => setDrawer(false)} className="mt-6 w-full rounded-full bg-primary py-3.5 text-primary-foreground">نمایش {faDigits(list.length)} محصول</button>
          </div>
        </div>
      )}
    </div>
  );
}

function Group({ t, children }: { t: string; children: React.ReactNode }) {
  return <div><p className="mb-3 font-semibold">{t}</p><div className="space-y-2">{children}</div></div>;
}
function Check({ on, onClick, children }: { on: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button onClick={onClick} className="flex w-full items-center gap-2.5 text-start">
      <span className={cn("grid size-4 place-items-center rounded border transition", on && "border-foreground bg-foreground")}>{on && <span className="size-1.5 rounded-full bg-background" />}</span>
      <span className={on ? "text-foreground" : "text-muted-foreground"}>{children}</span>
    </button>
  );
}
