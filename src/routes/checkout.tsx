import { createFileRoute, Link } from "@tanstack/react-router";
import { Check } from "lucide-react";
import { useState } from "react";
import { faDigits, finalPrice, products, toman } from "@/lib/shop-data";
import { useShop } from "@/lib/shop-store";
import { Row } from "@/components/shop/CartDrawer";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/checkout")({
  head: () => ({
    meta: [
      { title: "تسویه حساب — MaralOnlineShop" },
      { name: "description", content: "تکمیل سفارش در نسخه نمایشی مارال." },
      { property: "og:title", content: "تسویه حساب مارال" },
      { property: "og:description", content: "پرداخت آزمایشی؛ هیچ تراکنش واقعی انجام نمی‌شود." },
    ],
  }),
  component: Checkout,
});

const steps = ["اطلاعات مشتری", "آدرس ارسال", "روش ارسال", "خلاصه سفارش", "پرداخت آزمایشی"];
type F = Record<string, string | undefined>;

function Checkout() {
  const { cart, totals, clearCart } = useShop();
  const [step, setStep] = useState(0);
  const [f, setF] = useState<F>({ ship: "standard" });
  const [errs, setErrs] = useState<F>({});
  const [done, setDone] = useState<string | null>(null);
  const set = (k: string) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => setF({ ...f, [k]: e.target.value });

  const validate = () => {
    const e: F = {};
    if (step === 0) {
      if ((f["name"] ?? "").trim().length < 3) e["name"] = "نام و نام خانوادگی را کامل وارد کنید.";
      if (!/^(09|۰۹)[0-9۰-۹]{9}$/.test(f["phone"] ?? "")) e["phone"] = "شماره موبایل معتبر نیست (مثال: ۰۹۱۲۳۴۵۶۷۸۹).";
      if (f["email"] && !/\S+@\S+\.\S+/.test(f["email"])) e["email"] = "ایمیل معتبر نیست.";
    }
    if (step === 1) {
      if (!f["city"]?.trim()) e["city"] = "شهر را وارد کنید.";
      if ((f["addr"] ?? "").trim().length < 10) e["addr"] = "آدرس کامل را وارد کنید.";
      if (!/^[0-9۰-۹]{10}$/.test(f["zip"] ?? "")) e["zip"] = "کد پستی ۱۰ رقمی است.";
    }
    setErrs(e);
    return Object.keys(e).length === 0;
  };
  const shipCost = f["ship"] === "express" ? 950_000 : totals.shipping;
  const next = () => {
    if (!validate()) return;
    if (step === 4) { setDone(`MRL-${Math.floor(100000 + Math.random() * 900000)}`); clearCart(); return; }
    setStep(step + 1);
  };

  if (done) return (
    <div className="mx-auto max-w-lg px-6 py-24 text-center animate-rise">
      <span className="mx-auto grid size-20 place-items-center rounded-full bg-success/15 text-success"><Check className="size-9" /></span>
      <h1 className="mt-6 text-3xl font-semibold">سفارش شما ثبت شد</h1>
      <p className="mt-3 text-muted-foreground">کد پیگیری: <span dir="ltr" className="font-mono text-foreground">{done}</span></p>
      <p className="mt-2 text-xs text-muted-foreground">این یک سفارش آزمایشی است و هیچ پرداختی انجام نشده.</p>
      <Link to="/shop" className="mt-8 inline-block rounded-full bg-primary px-8 py-3.5 text-primary-foreground">ادامه خرید</Link>
    </div>
  );

  if (!cart.length) return <div className="py-32 text-center text-muted-foreground">سبد خرید خالی است. <Link to="/shop" className="text-foreground underline">رفتن به فروشگاه</Link></div>;

  const field = (k: string, label: string, opts: { dir?: "ltr"; area?: boolean } = {}) => (
    <label className="block space-y-1.5 text-sm">
      <span>{label}</span>
      {opts.area ? <textarea value={f[k] ?? ""} onChange={set(k)} rows={3} className={cn("w-full rounded-2xl border bg-card px-4 py-3 outline-none focus:ring-2 focus:ring-ring", errs[k] && "border-destructive")} />
        : <input value={f[k] ?? ""} onChange={set(k)} dir={opts.dir} className={cn("w-full rounded-full border bg-card px-4 py-3 outline-none focus:ring-2 focus:ring-ring", errs[k] && "border-destructive")} />}
      {errs[k] && <span className="text-xs text-destructive">{errs[k]}</span>}
    </label>
  );

  return (
    <div className="mx-auto max-w-6xl px-4 pt-10 md:px-6">
      <h1 className="text-3xl font-semibold">تسویه حساب</h1>
      <ol className="no-scrollbar mt-8 flex gap-2 overflow-x-auto">
        {steps.map((s, i) => (
          <li key={s} className={cn("flex shrink-0 items-center gap-2 rounded-full px-4 py-2 text-xs", i === step ? "bg-primary text-primary-foreground" : i < step ? "bg-champagne" : "bg-muted text-muted-foreground")}>
            <span className="grid size-5 place-items-center rounded-full bg-background/20">{i < step ? <Check className="size-3" /> : faDigits(i + 1)}</span>{s}
          </li>
        ))}
      </ol>
      <div className="mt-8 grid gap-8 lg:grid-cols-[1fr_360px]">
        <div className="space-y-4 rounded-3xl bg-card/60 p-6 md:p-8">
          {step === 0 && <>{field("name", "نام و نام خانوادگی")}{field("phone", "شماره موبایل", { dir: "ltr" })}{field("email", "ایمیل (اختیاری)", { dir: "ltr" })}</>}
          {step === 1 && <>{field("city", "شهر")}{field("addr", "آدرس کامل", { area: true })}{field("zip", "کد پستی", { dir: "ltr" })}</>}
          {step === 2 && [{ k: "standard", t: "ارسال استاندارد", d: totals.shipping ? toman(totals.shipping) : "رایگان" }, { k: "express", t: "ارسال سریع", d: toman(950_000) }].map((o) => (
            <button key={o.k} onClick={() => setF({ ...f, ship: o.k })} className={cn("flex w-full justify-between rounded-2xl border p-5 text-start", f["ship"] === o.k && "border-foreground bg-background")}><span>{o.t}</span><span className="text-muted-foreground">{o.d}</span></button>
          ))}
          {step === 3 && <div className="space-y-3">{cart.map((c, i) => { const p = products.find((x) => x.id === c.id)!; return <div key={i} className="flex items-center gap-3 text-sm"><img src={p.images[0]} alt="" className="h-16 w-12 rounded-lg object-cover" /><span className="flex-1">{p.name} × {faDigits(c.qty)}</span><span>{toman(finalPrice(p) * c.qty)}</span></div>; })}
            <p className="pt-3 text-sm text-muted-foreground">ارسال به: {f["city"]}، {f["addr"]}</p></div>}
          {step === 4 && <div className="space-y-4"><p className="rounded-2xl bg-accent/10 p-4 text-sm text-accent">این مرحله آزمایشی است. هیچ اطلاعات کارتی دریافت نمی‌شود و پرداخت واقعی انجام نمی‌گیرد.</p>
            <div className="rounded-2xl border bg-background p-5 text-sm">درگاه پرداخت نمایشی — با زدن «پرداخت آزمایشی» سفارش ثبت می‌شود.</div></div>}
          <div className="flex justify-between pt-4">
            <button disabled={step === 0} onClick={() => setStep(step - 1)} className="rounded-full border px-6 py-3 text-sm disabled:opacity-30">مرحله قبل</button>
            <button onClick={next} className="rounded-full bg-primary px-8 py-3 text-sm text-primary-foreground">{step === 4 ? "پرداخت آزمایشی" : "ادامه"}</button>
          </div>
        </div>
        <aside className="glass h-fit space-y-2 rounded-3xl p-6 text-sm">
          <p className="mb-3 font-semibold">خلاصه سفارش</p>
          <Row k={`کالاها (${faDigits(totals.count)})`} v={toman(totals.subtotal)} />
          <Row k="ارسال" v={shipCost ? toman(shipCost) : "رایگان"} />
          <Row k="مبلغ قابل پرداخت" v={toman(totals.subtotal + shipCost)} bold />
        </aside>
      </div>
    </div>
  );
}
