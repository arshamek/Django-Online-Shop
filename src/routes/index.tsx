import { createFileRoute, Link } from "@tanstack/react-router";
import { ArrowLeft, Globe2, Headphones, PackageSearch, Receipt, ShoppingCart, Sparkles } from "lucide-react";
import { useState } from "react";
import { brands, categories, images, products } from "@/lib/shop-data";
import { ProductCard } from "@/components/shop/ProductCard";
import { useShop } from "@/lib/shop-store";

export const Route = createFileRoute("/")({
  head: () => ({
    meta: [
      { title: "MaralOnlineShop — استایل تو، فراتر از مرزها" },
      { name: "description", content: "جدیدترین انتخاب‌های دنیای مد و فشن را از برندهای محبوب جهان کشف کن." },
      { property: "og:title", content: "MaralOnlineShop — استایل تو، فراتر از مرزها" },
      { property: "og:description", content: "فروشگاه بین‌المللی مد و لایف‌استایل با قیمت تومانی." },
    ],
  }),
  component: Home,
});

function SectionHead({ eyebrow, title, to }: { eyebrow: string; title: string; to?: boolean }) {
  return (
    <div className="mb-8 flex items-end justify-between gap-4">
      <div><p className="eyebrow mb-2">{eyebrow}</p><h2 className="text-2xl font-semibold md:text-4xl">{title}</h2></div>
      {to && <Link to="/shop" className="group flex items-center gap-2 text-sm">مشاهده همه <ArrowLeft className="size-4 transition group-hover:-translate-x-1" /></Link>}
    </div>
  );
}

function Home() {
  const { setChatOpen } = useShop();
  const [email, setEmail] = useState("");
  const [subscribed, setSubscribed] = useState(false);
  return (
    <div className="-mt-[72px]">
      {/* Hero */}
      <section className="relative h-[92vh] min-h-[560px] overflow-hidden">
        <img src={images.hero} alt="کالکشن زمستانی مارال" width={1920} height={1088} className="absolute inset-0 size-full scale-105 object-cover object-[70%_center] animate-in fade-in zoom-in-105 duration-[1600ms] max-md:object-[78%_center]" />
        <div className="bg-veil absolute inset-0 max-md:bg-dark-veil" />
        <div className="relative mx-auto flex h-full max-w-7xl items-end px-6 pb-20 md:items-center md:pb-0">
          <div className="max-w-xl md:me-auto md:ms-0 md:[margin-inline-start:auto]">
            <p className="eyebrow animate-rise" dir="ltr" style={{ textAlign: "right" }}>Winter Collection 2026</p>
            <h1 className="mt-4 text-5xl font-bold leading-[1.15] animate-rise [animation-delay:150ms] max-md:text-ink-foreground md:text-7xl">استایل تو،<br />فراتر از مرزها</h1>
            <p className="mt-6 max-w-md text-lg leading-8 text-muted-foreground animate-rise [animation-delay:300ms] max-md:text-ink-foreground/80">جدیدترین انتخاب‌های دنیای مد و فشن را از برندهای محبوب جهان کشف کن.</p>
            <div className="mt-8 flex flex-wrap gap-3 animate-rise [animation-delay:450ms]">
              <Link to="/shop" className="rounded-full bg-primary px-8 py-4 text-sm font-medium text-primary-foreground transition hover:-translate-y-0.5 hover:shadow-soft">مشاهده کالکشن</Link>
              <Link to="/shop" search={{ sort: "new" }} className="glass rounded-full px-8 py-4 text-sm font-medium transition hover:-translate-y-0.5">جدیدترین محصولات</Link>
            </div>
          </div>
        </div>
      </section>

      <div className="mx-auto max-w-7xl space-y-24 px-4 pt-20 md:px-6">
        {/* Categories */}
        <section>
          <SectionHead eyebrow="Shop by category" title="دسته‌بندی‌های منتخب" />
          <div className="grid grid-cols-2 gap-3 md:grid-cols-3 md:gap-5 lg:grid-cols-6">
            {categories.map((c, i) => (
              <Link key={c.id} to="/shop" search={{ cat: c.id }} className="group relative aspect-[3/4] overflow-hidden rounded-2xl animate-rise" style={{ animationDelay: `${i * 80}ms` }}>
                <img src={c.image} alt={c.label} loading="lazy" className="absolute inset-0 size-full object-cover transition duration-700 group-hover:scale-110" />
                <div className="bg-dark-veil absolute inset-0" />
                <span className="absolute bottom-4 start-4 text-base font-medium text-ink-foreground">{c.label}</span>
              </Link>
            ))}
          </div>
        </section>

        {/* Trending */}
        <section>
          <SectionHead eyebrow="Trending now" title="محبوب‌ترین انتخاب‌ها" to />
          <div className="grid grid-cols-2 gap-x-3 gap-y-8 md:grid-cols-4 md:gap-x-5">
            {products.filter((p) => p.discount > 0).slice(0, 8).map((p) => <ProductCard key={p.id} p={p} />)}
          </div>
        </section>

        {/* International */}
        <section className="grid gap-5 md:grid-cols-[1.1fr_1fr_1fr]">
          <div className="flex flex-col justify-center rounded-3xl bg-champagne p-8 md:p-10">
            <p className="eyebrow mb-3">Global shopping</p>
            <h2 className="text-3xl font-semibold leading-tight">خرید از فروشگاه‌های بین‌المللی، بدون مرز</h2>
            <p className="mt-4 text-sm leading-7 text-muted-foreground">محصولات را مستقیم از فروشگاه‌های ترکیه و امارات انتخاب کن؛ مارال خرید، ارسال و تحویل را با قیمت نهایی تومانی انجام می‌دهد.</p>
          </div>
          {[{ n: "Trendyol", c: "ترکیه", d: "پوشاک روزمره و برندهای ترک", img: images.trench }, { n: "Namshi", c: "امارات", d: "برندهای پریمیوم خاورمیانه", img: images.blackbag }].map((s) => (
            <Link key={s.n} to="/shop" search={{ source: s.n }} className="group relative min-h-80 overflow-hidden rounded-3xl">
              <img src={s.img} alt="" loading="lazy" className="absolute inset-0 size-full object-cover transition duration-700 group-hover:scale-105" />
              <div className="bg-dark-veil absolute inset-0" />
              <div className="glass-dark absolute inset-x-4 bottom-4 rounded-2xl p-5 text-ink-foreground">
                <p className="text-xs opacity-70">{s.c}</p>
                <p className="font-display text-3xl" dir="ltr" style={{ textAlign: "right" }}>{s.n}</p>
                <p className="mt-1 text-sm opacity-80">{s.d}</p>
              </div>
            </Link>
          ))}
        </section>

        {/* New arrivals carousel */}
        <section>
          <SectionHead eyebrow="Just in" title="تازه‌ها" to />
          <div className="no-scrollbar -mx-4 flex snap-x gap-4 overflow-x-auto px-4 pb-2">
            {products.filter((p) => p.isNew).map((p) => <div key={p.id} className="w-[46%] shrink-0 snap-start md:w-[23%]"><ProductCard p={p} /></div>)}
          </div>
        </section>

        {/* Brands */}
        <section className="rounded-3xl border bg-card px-6 py-12 text-center">
          <p className="eyebrow mb-8">Premium brands</p>
          <div className="flex flex-wrap items-center justify-center gap-x-12 gap-y-6">
            {brands.map((b) => <Link key={b} to="/shop" search={{ brand: b }} className="font-display text-2xl text-muted-foreground transition hover:text-foreground md:text-3xl">{b}</Link>)}
          </div>
        </section>

        {/* Offer */}
        <section className="relative overflow-hidden rounded-3xl bg-ink">
          <img src={images.offer} alt="" loading="lazy" className="absolute inset-0 size-full object-cover opacity-90" />
          <div className="relative max-w-lg p-8 py-20 text-ink-foreground md:p-16">
            <p className="eyebrow mb-4">Limited offer</p>
            <h2 className="text-4xl font-bold leading-tight md:text-5xl">تا ۳۰٪ تخفیف<br />روی کالکشن شب</h2>
            <p className="mt-4 opacity-75">انتخاب‌های منتخب از طراحان مستقل، فقط تا پایان هفته.</p>
            <Link to="/shop" search={{ sale: true }} className="mt-8 inline-block rounded-full bg-ink-foreground px-8 py-4 text-sm font-medium text-ink transition hover:opacity-90">مشاهده تخفیف‌ها</Link>
          </div>
        </section>

        {/* AI */}
        <section className="relative overflow-hidden rounded-3xl bg-gradient-to-br from-champagne to-card p-8 md:p-16">
          <div className="grid items-center gap-10 md:grid-cols-2">
            <div>
              <p className="eyebrow mb-3" dir="ltr" style={{ textAlign: "right" }}>Maral AI</p>
              <h2 className="text-3xl font-semibold md:text-4xl">انتخاب هوشمند، خرید آسان</h2>
              <p className="mt-4 leading-8 text-muted-foreground">دنبال محصول خاصی هستی؟ مارال کمکت می‌کنه بهترین انتخاب رو پیدا کنی.</p>
              <button onClick={() => setChatOpen(true)} className="mt-8 flex items-center gap-2 rounded-full bg-primary px-8 py-4 text-sm font-medium text-primary-foreground transition hover:-translate-y-0.5"><Sparkles className="size-4" /> شروع گفت‌وگو</button>
            </div>
            <div className="glass space-y-3 rounded-3xl p-5">
              <div className="w-fit max-w-[85%] rounded-2xl rounded-br-sm bg-card px-4 py-3 text-sm">سلام! برای استایل زمستونی دنبال چی هستی؟</div>
              <div className="ms-auto w-fit max-w-[85%] rounded-2xl rounded-bl-sm bg-ink px-4 py-3 text-sm text-ink-foreground">یه پالتو شیک و گرم</div>
              <div className="flex gap-2">{products.filter((p) => ["p1", "p8"].includes(p.id)).map((p) => <img key={p.id} src={p.images[0]} alt="" className="aspect-square w-24 rounded-xl object-cover" />)}</div>
            </div>
          </div>
        </section>

        {/* Trust */}
        <section className="grid grid-cols-2 gap-4 md:grid-cols-5">
          {[{ i: ShoppingCart, t: "خرید آسان" }, { i: Headphones, t: "پشتیبانی مشتریان" }, { i: Receipt, t: "شفافیت قیمت" }, { i: PackageSearch, t: "پیگیری سفارش" }, { i: Globe2, t: "ارسال بین‌المللی" }].map(({ i: I, t }) => (
            <div key={t} className="flex flex-col items-center gap-3 rounded-2xl bg-card p-6 text-center">
              <span className="grid size-12 place-items-center rounded-full bg-champagne"><I className="size-5" /></span>
              <p className="text-sm font-medium">{t}</p>
            </div>
          ))}
        </section>

        {/* Newsletter */}
        <section className="mx-auto max-w-2xl text-center">
          <p className="eyebrow mb-3">Newsletter</p>
          <h2 className="text-3xl font-semibold">اولین نفری باش که می‌دونه</h2>
          <p className="mt-3 text-muted-foreground">کالکشن‌های تازه و پیشنهادهای ویژه، مستقیم در ایمیل تو.</p>
          {subscribed ? <p className="mt-8 text-success">عضویت شما ثبت شد (نسخه نمایشی).</p> : (
            <form onSubmit={(e) => { e.preventDefault(); if (/\S+@\S+\.\S+/.test(email)) setSubscribed(true); }} className="glass mx-auto mt-8 flex max-w-md rounded-full p-1.5">
              <input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} placeholder="ایمیل شما" dir="ltr" className="flex-1 bg-transparent px-4 text-sm outline-none" />
              <button className="rounded-full bg-primary px-6 py-3 text-sm text-primary-foreground">عضویت</button>
            </form>
          )}
        </section>
      </div>
    </div>
  );
}
