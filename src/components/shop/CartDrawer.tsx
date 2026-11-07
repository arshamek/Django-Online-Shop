import { Link } from "@tanstack/react-router";
import { Minus, Plus, ShoppingBag, Trash2, X } from "lucide-react";
import { faDigits, finalPrice, products, toman } from "@/lib/shop-data";
import { useShop } from "@/lib/shop-store";
import { cn } from "@/lib/utils";

export function CartDrawer() {
  const { cart, cartOpen, setCartOpen, updateQty, removeItem, totals } = useShop();
  return (
    <div className={cn("fixed inset-0 z-[60] transition", cartOpen ? "visible" : "invisible")}>
      <div onClick={() => setCartOpen(false)} className={cn("absolute inset-0 bg-ink/30 backdrop-blur-sm transition-opacity duration-300", cartOpen ? "opacity-100" : "opacity-0")} />
      <aside
        className={cn("glass absolute inset-y-2 start-2 flex w-[calc(100%-1rem)] max-w-md flex-col rounded-3xl bg-background/80 transition-transform duration-500 ease-out",
          cartOpen ? "translate-x-0" : "translate-x-[110%]")}
        aria-label="سبد خرید"
      >
        <header className="flex items-center justify-between border-b p-5">
          <h2 className="text-lg font-semibold">سبد خرید <span className="text-muted-foreground">({faDigits(totals.count)})</span></h2>
          <button onClick={() => setCartOpen(false)} aria-label="بستن" className="grid size-9 place-items-center rounded-full hover:bg-muted"><X className="size-4" /></button>
        </header>
        <div className="flex-1 space-y-4 overflow-y-auto p-5">
          {cart.length === 0 && (
            <div className="flex h-full flex-col items-center justify-center gap-3 text-center text-muted-foreground">
              <ShoppingBag className="size-10 stroke-1" />
              <p>سبد خرید شما خالی است.</p>
              <Link to="/shop" onClick={() => setCartOpen(false)} className="text-sm text-foreground underline underline-offset-4">شروع خرید</Link>
            </div>
          )}
          {cart.map((c, i) => {
            const p = products.find((x) => x.id === c.id);
            if (!p) return null;
            return (
              <div key={i} className="flex gap-4 rounded-2xl bg-card/70 p-3">
                <img src={p.images[0]} alt={p.name} className="h-28 w-20 rounded-xl object-cover" />
                <div className="flex flex-1 flex-col gap-1 text-sm">
                  <p className="font-display text-muted-foreground">{p.brand}</p>
                  <p className="font-medium">{p.name}</p>
                  <p className="text-xs text-muted-foreground">سایز: {c.size} · رنگ: {c.color}</p>
                  <p className="text-xs text-muted-foreground">قیمت واحد: {toman(finalPrice(p))}</p>
                  <div className="mt-auto flex items-center justify-between">
                    <div className="flex items-center gap-2 rounded-full border px-1">
                      <button aria-label="افزایش" onClick={() => updateQty(i, c.qty + 1)} className="p-1.5"><Plus className="size-3" /></button>
                      <span className="w-4 text-center">{faDigits(c.qty)}</span>
                      <button aria-label="کاهش" onClick={() => updateQty(i, c.qty - 1)} className="p-1.5"><Minus className="size-3" /></button>
                    </div>
                    <span className="font-semibold">{toman(finalPrice(p) * c.qty)}</span>
                  </div>
                </div>
                <button aria-label="حذف" onClick={() => removeItem(i)} className="self-start text-muted-foreground hover:text-destructive"><Trash2 className="size-4" /></button>
              </div>
            );
          })}
        </div>
        {cart.length > 0 && (
          <footer className="space-y-2 border-t p-5 text-sm">
            <Row k="جمع کالاها" v={toman(totals.subtotal)} />
            <Row k="هزینه ارسال (تخمینی)" v={totals.shipping ? toman(totals.shipping) : "رایگان"} />
            <Row k="مبلغ نهایی" v={toman(totals.total)} bold />
            <Link to="/checkout" onClick={() => setCartOpen(false)} className="mt-3 block rounded-full bg-primary py-3.5 text-center font-medium text-primary-foreground transition hover:opacity-90">
              ادامه و تسویه حساب
            </Link>
          </footer>
        )}
      </aside>
    </div>
  );
}

export function Row({ k, v, bold }: { k: string; v: string; bold?: boolean }) {
  return (
    <div className={cn("flex justify-between", bold && "pt-2 text-base font-semibold")}>
      <span className={bold ? "" : "text-muted-foreground"}>{k}</span><span>{v}</span>
    </div>
  );
}
