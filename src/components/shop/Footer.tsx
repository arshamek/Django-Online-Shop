import { Link } from "@tanstack/react-router";
import { Instagram, Send, Phone, Mail } from "lucide-react";
import { Logo } from "./Header";

const cols = [
  { t: "خدمات مشتریان", l: ["پشتیبانی ۲۴ ساعته", "پیگیری سفارش", "سوالات متداول"] },
  { t: "راهنمای خرید", l: ["نحوه سفارش از خارج", "راهنمای سایز", "روش‌های پرداخت"] },
  { t: "ارسال و مرجوعی", l: ["اطلاعات ارسال", "شرایط مرجوعی", "ضمانت اصالت کالا"] },
];

export function Footer() {
  return (
    <footer className="mt-24 bg-ink pb-28 pt-16 text-ink-foreground md:pb-10">
      <div className="mx-auto grid max-w-7xl gap-10 px-6 md:grid-cols-5">
        <div className="md:col-span-2 space-y-4">
          <Logo className="text-ink-foreground" />
          <p className="max-w-sm text-sm leading-7 opacity-70">مارال، دروازه تو به دنیای مد بین‌المللی. خرید مستقیم از فروشگاه‌های معتبر جهان با قیمت شفاف به تومان.</p>
          <div className="flex gap-2">{[Instagram, Send].map((I, i) => <a key={i} href="#" aria-label="شبکه اجتماعی" className="grid size-10 place-items-center rounded-full border border-ink-foreground/20 hover:bg-ink-foreground/10"><I className="size-4" /></a>)}</div>
          <div className="space-y-1 text-sm opacity-70"><p className="flex items-center gap-2"><Phone className="size-4" /> <span dir="ltr">021 - 0000 0000</span></p><p className="flex items-center gap-2"><Mail className="size-4" /> <span dir="ltr">hello@maral.example</span></p></div>
        </div>
        {cols.map((c) => (
          <div key={c.t}><p className="mb-4 text-sm font-semibold">{c.t}</p><ul className="space-y-2.5 text-sm opacity-70">{c.l.map((x) => <li key={x}><Link to="/shop" className="hover:opacity-100">{x}</Link></li>)}</ul></div>
        ))}
      </div>
      <div className="mx-auto mt-12 flex max-w-7xl flex-wrap items-center justify-between gap-4 border-t border-ink-foreground/10 px-6 pt-6 text-xs opacity-60">
        <p>© ۱۴۰۵ MaralOnlineShop — نسخه نمایشی؛ تمام قیمت‌ها و اطلاعات نمونه هستند.</p>
        <div className="flex gap-2">{["نماد اعتماد", "درگاه امن", "ساماندهی"].map((x) => <span key={x} className="rounded-lg border border-ink-foreground/20 px-3 py-2">{x}</span>)}</div>
      </div>
    </footer>
  );
}
