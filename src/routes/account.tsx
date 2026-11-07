import { createFileRoute } from "@tanstack/react-router";
import { Globe, Heart, LogOut, MapPin, Package, User } from "lucide-react";
import { useState } from "react";
import { useShop, type Lang } from "@/lib/shop-store";
import { WishlistGrid } from "./wishlist";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/account")({
  head: () => ({
    meta: [
      { title: "حساب کاربری — MaralOnlineShop" },
      { name: "description", content: "پروفایل، سفارش‌ها و آدرس‌های شما در مارال (نمایشی)." },
      { property: "og:title", content: "حساب کاربری مارال" },
      { property: "og:description", content: "مدیریت سفارش‌ها و اطلاعات حساب." },
    ],
  }),
  component: Account,
});

const tabs = [
  { k: "profile", l: "اطلاعات حساب", i: User }, { k: "orders", l: "سفارش‌ها", i: Package }, { k: "addr", l: "آدرس‌ها", i: MapPin },
  { k: "wish", l: "علاقه‌مندی‌ها", i: Heart }, { k: "lang", l: "زبان", i: Globe },
] as const;

const orders = [
  { id: "MRL-482910", d: "۱۲ مهر ۱۴۰۵", s: "تحویل شده", t: "۱۸٬۴۰۰٬۰۰۰ تومان" },
  { id: "MRL-519034", d: "۲ آبان ۱۴۰۵", s: "در حال ارسال از ترکیه", t: "۹٬۸۰۰٬۰۰۰ تومان" },
];

function Account() {
  const [tab, setTab] = useState<(typeof tabs)[number]["k"]>("profile");
  const [out, setOut] = useState(false);
  const { lang, setLang } = useShop();
  if (out) return <div className="py-32 text-center"><p className="text-lg">از حساب خارج شدید (نمایشی).</p><button onClick={() => setOut(false)} className="mt-6 rounded-full bg-primary px-8 py-3 text-primary-foreground">ورود دوباره</button></div>;
  return (
    <div className="mx-auto max-w-7xl px-4 pt-10 md:px-6">
      <div className="flex items-center gap-4"><span className="grid size-16 place-items-center rounded-full bg-champagne font-display text-2xl">س</span><div><h1 className="text-2xl font-semibold">سارا محمدی</h1><p className="text-sm text-muted-foreground">کاربر نمونه · عضو طلایی</p></div></div>
      <div className="mt-10 grid gap-8 md:grid-cols-[220px_1fr]">
        <nav className="no-scrollbar flex gap-2 overflow-x-auto md:flex-col">
          {tabs.map(({ k, l, i: I }) => <button key={k} onClick={() => setTab(k)} className={cn("flex shrink-0 items-center gap-3 rounded-full px-4 py-3 text-sm", tab === k ? "bg-primary text-primary-foreground" : "hover:bg-muted")}><I className="size-4" />{l}</button>)}
          <button onClick={() => setOut(true)} className="flex shrink-0 items-center gap-3 rounded-full px-4 py-3 text-sm text-destructive hover:bg-destructive/10"><LogOut className="size-4" />خروج</button>
        </nav>
        <div className="rounded-3xl bg-card/60 p-6 md:p-8">
          {tab === "profile" && <dl className="grid gap-4 text-sm sm:grid-cols-2">{[["نام", "سارا محمدی"], ["موبایل", "۰۹۱۲ ۰۰۰ ۰۰۰۰"], ["ایمیل", "sara@example.com"], ["تاریخ تولد", "۱۵ اسفند"]].map(([a, b]) => <div key={a} className="rounded-2xl bg-background p-4"><dt className="text-muted-foreground">{a}</dt><dd className="mt-1">{b}</dd></div>)}</dl>}
          {tab === "orders" && <div className="space-y-3">{orders.map((o) => <div key={o.id} className="flex flex-wrap items-center justify-between gap-2 rounded-2xl bg-background p-4 text-sm"><span dir="ltr" className="font-mono">{o.id}</span><span className="text-muted-foreground">{o.d}</span><span className="rounded-full bg-champagne px-3 py-1 text-xs">{o.s}</span><span>{o.t}</span></div>)}</div>}
          {tab === "addr" && <div className="rounded-2xl bg-background p-5 text-sm"><p className="font-medium">خانه</p><p className="mt-1 text-muted-foreground">تهران، خیابان ولیعصر، کوچه نمونه، پلاک ۱۲ — کد پستی ۱۲۳۴۵۶۷۸۹۰</p></div>}
          {tab === "wish" && <WishlistGrid />}
          {tab === "lang" && <div className="flex flex-wrap gap-2">{([["fa", "فارسی"], ["en", "English"], ["ar", "العربية"], ["tr", "Türkçe"]] as [Lang, string][]).map(([k, l]) => <button key={k} onClick={() => setLang(k)} className={cn("rounded-full border px-5 py-2.5 text-sm", lang === k && "border-foreground bg-foreground text-background")}>{l}</button>)}</div>}
        </div>
      </div>
    </div>
  );
}
