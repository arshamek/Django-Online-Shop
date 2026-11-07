import { Link, useNavigate } from "@tanstack/react-router";
import { Globe, Heart, Home, LayoutGrid, Menu, Search, ShoppingBag, User, X } from "lucide-react";
import { useEffect, useState } from "react";
import { brands, categories, faDigits, navCategories, products, images } from "@/lib/shop-data";
import { useShop, type Lang } from "@/lib/shop-store";
import { cn } from "@/lib/utils";

const langs: { id: Lang; label: string }[] = [
  { id: "fa", label: "فارسی" }, { id: "en", label: "English" }, { id: "ar", label: "العربية" }, { id: "tr", label: "Türkçe" },
];

export function Logo({ className }: { className?: string }) {
  return (
    <Link to="/" className={cn("font-display text-2xl font-semibold tracking-[0.12em] text-foreground", className)} dir="ltr">
      MARAL<span className="text-accent">·</span>
    </Link>
  );
}

function SearchOverlay({ onClose }: { onClose: () => void }) {
  const [q, setQ] = useState("");
  const nav = useNavigate();
  const res = q.trim() ? products.filter((p) => (p.name + p.brand).toLowerCase().includes(q.toLowerCase())).slice(0, 5) : [];
  return (
    <div className="fixed inset-0 z-[70] animate-in fade-in" onClick={onClose}>
      <div className="absolute inset-0 bg-ink/30 backdrop-blur-md" />
      <div className="glass relative mx-auto mt-24 w-[92%] max-w-2xl rounded-3xl bg-background/80 p-6" onClick={(e) => e.stopPropagation()}>
        <form onSubmit={(e) => { e.preventDefault(); nav({ to: "/shop", search: { q } }); onClose(); }} className="flex items-center gap-3 border-b pb-4">
          <Search className="size-5 text-muted-foreground" />
          <input autoFocus value={q} onChange={(e) => setQ(e.target.value)} placeholder="جستجوی محصول، برند یا دسته‌بندی…" className="flex-1 bg-transparent text-lg outline-none placeholder:text-muted-foreground" />
          <button type="button" onClick={onClose} aria-label="بستن"><X className="size-5" /></button>
        </form>
        <div className="mt-4 space-y-2">
          {res.map((p) => (
            <Link key={p.id} to="/product/$id" params={{ id: p.id }} onClick={onClose} className="flex items-center gap-3 rounded-xl p-2 hover:bg-muted">
              <img src={p.images[0]} alt="" className="size-12 rounded-lg object-cover" />
              <div className="text-sm"><p>{p.name}</p><p className="font-display text-muted-foreground">{p.brand}</p></div>
            </Link>
          ))}
          {!q && <div className="flex flex-wrap gap-2 text-sm">{["پالتو", "کیف", "کتانی", "عطر"].map((t) => <button key={t} onClick={() => setQ(t)} className="rounded-full border px-3 py-1.5 hover:border-foreground">{t}</button>)}</div>}
        </div>
      </div>
    </div>
  );
}

export function Header() {
  const { totals, wishlist, setCartOpen, lang, setLang } = useShop();
  const [mega, setMega] = useState<number | null>(null);
  const [search, setSearch] = useState(false);
  const [mobile, setMobile] = useState(false);
  const [langOpen, setLangOpen] = useState(false);
  const [scrolled, setScrolled] = useState(false);
  useEffect(() => {
    const f = () => setScrolled(window.scrollY > 20);
    f(); window.addEventListener("scroll", f); return () => window.removeEventListener("scroll", f);
  }, []);

  return (
    <>
      <div className="bg-ink py-2 text-center text-xs tracking-wide text-ink-foreground">
        تجربه خرید از برندهای محبوب دنیا، در <span dir="ltr">MaralOnlineShop</span> — ارسال رایگان برای خرید بالای ۲۰ میلیون تومان
      </div>
      <header className="sticky top-0 z-50 px-3 pt-3 md:px-6" onMouseLeave={() => setMega(null)}>
        <div className={cn("glass mx-auto flex max-w-7xl items-center gap-4 rounded-full px-4 py-2.5 transition-all md:px-6", scrolled && "bg-background/80")}>
          <button className="lg:hidden" aria-label="منو" onClick={() => setMobile(true)}><Menu className="size-5" /></button>
          <Logo />
          <nav className="mx-auto hidden items-center gap-1 lg:flex">
            {navCategories.map((n, i) => (
              <Link key={n.label} to="/shop" search={n.cat ? { cat: n.cat } : n.sale ? { sale: true } : {}}
                onMouseEnter={() => setMega(i)}
                className={cn("relative rounded-full px-3 py-2 text-[13px] transition hover:bg-foreground/5", n.sale && "text-destructive", mega === i && "bg-foreground/5")}>
                {n.label}
              </Link>
            ))}
          </nav>
          <div className="ms-auto flex items-center gap-1 lg:ms-0">
            <IconBtn label="جستجو" onClick={() => setSearch(true)}><Search className="size-[18px]" /></IconBtn>
            <div className="relative max-md:hidden">
              <IconBtn label="زبان" onClick={() => setLangOpen((v) => !v)}><Globe className="size-[18px]" /></IconBtn>
              {langOpen && (
                <div className="glass absolute end-0 top-12 w-36 rounded-2xl bg-background/90 p-1.5 animate-in fade-in slide-in-from-top-2">
                  {langs.map((l) => (
                    <button key={l.id} onClick={() => { setLang(l.id); setLangOpen(false); }} className={cn("block w-full rounded-xl px-3 py-2 text-start text-sm hover:bg-muted", lang === l.id && "bg-muted font-semibold")}>{l.label}</button>
                  ))}
                </div>
              )}
            </div>
            <Link to="/account" aria-label="حساب کاربری" className="grid size-10 place-items-center rounded-full hover:bg-foreground/5 max-md:hidden"><User className="size-[18px]" /></Link>
            <Link to="/wishlist" aria-label="علاقه‌مندی‌ها" className="relative grid size-10 place-items-center rounded-full hover:bg-foreground/5 max-md:hidden">
              <Heart className="size-[18px]" />{wishlist.length > 0 && <Badge n={wishlist.length} />}
            </Link>
            <IconBtn label="سبد خرید" onClick={() => setCartOpen(true)}>
              <ShoppingBag className="size-[18px]" />{totals.count > 0 && <Badge n={totals.count} />}
            </IconBtn>
          </div>
        </div>

        {mega !== null && (
          <div className="glass mx-auto mt-2 hidden max-w-7xl rounded-3xl bg-background/90 p-8 animate-in fade-in slide-in-from-top-2 lg:block">
            <div className="grid grid-cols-4 gap-8">
              <div>
                <p className="eyebrow mb-4">دسته‌بندی‌ها</p>
                <ul className="space-y-2.5 text-sm">{categories.map((c) => <li key={c.id}><Link to="/shop" search={{ cat: c.id }} onClick={() => setMega(null)} className="hover:text-accent">{c.label}</Link></li>)}</ul>
              </div>
              <div>
                <p className="eyebrow mb-4">پرطرفدار</p>
                <ul className="space-y-2.5 text-sm text-muted-foreground">{["پالتو و کت", "بافت", "کتانی", "کیف دوشی", "ساعت", "عطر"].map((s) => <li key={s}><Link to="/shop" search={{ q: s.split(" ")[0] }} onClick={() => setMega(null)} className="hover:text-foreground">{s}</Link></li>)}</ul>
              </div>
              <div>
                <p className="eyebrow mb-4">برندهای ویژه</p>
                <ul className="space-y-2.5 font-display text-base">{brands.slice(0, 6).map((b) => <li key={b}><Link to="/shop" search={{ brand: b }} onClick={() => setMega(null)} className="hover:text-accent">{b}</Link></li>)}</ul>
              </div>
              <Link to="/shop" search={{ sale: true }} onClick={() => setMega(null)} className="group relative overflow-hidden rounded-2xl">
                <img src={images.offer} alt="" className="aspect-[4/5] size-full object-cover transition duration-700 group-hover:scale-105" />
                <div className="bg-dark-veil absolute inset-0" />
                <div className="absolute bottom-4 start-4 text-ink-foreground"><p className="font-display text-2xl">تا ۳۰٪ تخفیف</p><p className="text-xs opacity-80">کالکشن زمستانی</p></div>
              </Link>
            </div>
          </div>
        )}
      </header>

      {mobile && (
        <div className="fixed inset-0 z-[70] lg:hidden" onClick={() => setMobile(false)}>
          <div className="absolute inset-0 bg-ink/30 backdrop-blur-sm" />
          <div className="glass absolute inset-y-2 start-2 w-[85%] max-w-sm overflow-y-auto rounded-3xl bg-background/90 p-6 animate-in slide-in-from-right" onClick={(e) => e.stopPropagation()}>
            <div className="mb-6 flex items-center justify-between"><Logo /><button onClick={() => setMobile(false)} aria-label="بستن"><X className="size-5" /></button></div>
            <nav className="space-y-1">
              {navCategories.map((n) => (
                <Link key={n.label} to="/shop" search={n.cat ? { cat: n.cat } : n.sale ? { sale: true } : {}} onClick={() => setMobile(false)} className="flex justify-between rounded-2xl px-4 py-3.5 text-base hover:bg-muted">
                  {n.label}<span className="text-muted-foreground">‹</span>
                </Link>
              ))}
            </nav>
            <div className="mt-6 flex flex-wrap gap-2">
              {langs.map((l) => <button key={l.id} onClick={() => setLang(l.id)} className={cn("rounded-full border px-3 py-1.5 text-xs", lang === l.id && "border-foreground bg-foreground text-background")}>{l.label}</button>)}
            </div>
          </div>
        </div>
      )}
      {search && <SearchOverlay onClose={() => setSearch(false)} />}
    </>
  );
}

function IconBtn({ children, label, onClick }: { children: React.ReactNode; label: string; onClick: () => void }) {
  return <button aria-label={label} onClick={onClick} className="relative grid size-10 place-items-center rounded-full transition hover:bg-foreground/5">{children}</button>;
}
function Badge({ n }: { n: number }) {
  return <span className="absolute -top-0.5 -end-0.5 grid size-[18px] place-items-center rounded-full bg-accent text-[10px] font-bold text-accent-foreground">{faDigits(n)}</span>;
}

export function BottomNav() {
  const { setCartOpen, totals, setChatOpen } = useShop();
  const item = "flex flex-1 flex-col items-center gap-1 py-2 text-[10px] text-muted-foreground";
  return (
    <nav className="glass fixed inset-x-3 bottom-3 z-40 flex rounded-full bg-background/80 px-2 md:hidden">
      <Link to="/" className={item} activeProps={{ className: "text-foreground" }} activeOptions={{ exact: true }}><Home className="size-5" />خانه</Link>
      <Link to="/shop" className={item} activeProps={{ className: "text-foreground" }}><LayoutGrid className="size-5" />فروشگاه</Link>
      <button onClick={() => setChatOpen(true)} className={item}><span className="grid size-5 place-items-center rounded-full bg-accent text-[9px] font-bold text-accent-foreground">AI</span>مارال</button>
      <button onClick={() => setCartOpen(true)} className={cn(item, "relative")}><ShoppingBag className="size-5" />سبد {totals.count > 0 && <span className="absolute top-1 end-1/4 size-2 rounded-full bg-accent" />}</button>
      <Link to="/account" className={item} activeProps={{ className: "text-foreground" }}><User className="size-5" />حساب</Link>
    </nav>
  );
}
