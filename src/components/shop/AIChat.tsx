import { Link } from "@tanstack/react-router";
import { Minus, Send, Sparkles, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { finalPrice, products, toman, type Product } from "@/lib/shop-data";
import { useShop } from "@/lib/shop-store";
import { cn } from "@/lib/utils";

interface Msg { from: "bot" | "me"; text: string; items?: Product[] }

const suggestions = [
  "یه کتونی زیر ۵ میلیون پیشنهاد بده.",
  "جدیدترین کیف‌های زنانه رو نشون بده.",
  "بین این دو محصول مقایسه انجام بده.",
  "برای استایل زمستونی چی پیشنهاد می‌کنی؟",
];

// Scripted demo replies — no real AI provider is connected.
function reply(q: string): Msg {
  if (q.includes("کتونی") || q.includes("کفش")) return { from: "bot", text: "این دو کتانی زیر ۵ میلیون تومان هستند و هر دو سبک و راحت‌اند:", items: products.filter((p) => p.category === "shoes" && finalPrice(p) < 5_000_000) };
  if (q.includes("کیف")) return { from: "bot", text: "جدیدترین کیف‌های کالکشن این فصل:", items: products.filter((p) => p.category === "bags") };
  if (q.includes("مقایسه")) return { from: "bot", text: "مقایسه کیف دستی چرم و کیف کوئیلت: اولی ساختارمند و رسمی‌تر است، دومی با زنجیر طلایی برای مهمانی مناسب‌تر و با ۲۵٪ تخفیف اقتصادی‌تر.", items: products.filter((p) => ["p3", "p4"].includes(p.id)) };
  if (q.includes("زمست")) return { from: "bot", text: "برای زمستان یک پالتو بلند، بافت یقه‌اسکی و بوت چلسی ترکیبی بی‌نقص می‌سازند:", items: products.filter((p) => ["p1", "p2", "p6"].includes(p.id)) };
  return { from: "bot", text: "متوجه شدم! این‌ها محبوب‌ترین انتخاب‌های این هفته هستند:", items: products.slice(0, 3) };
}

export function AIChat() {
  const { chatOpen, setChatOpen } = useShop();
  const [msgs, setMsgs] = useState<Msg[]>([{ from: "bot", text: "سلام! من دستیار هوشمند مارال هستم. برای پیدا کردن بهترین محصول کمکت می‌کنم." }]);
  const [typing, setTyping] = useState(false);
  const [input, setInput] = useState("");
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => { end.current?.scrollIntoView({ behavior: "smooth" }); }, [msgs, typing]);

  const send = (t: string) => {
    if (!t.trim() || typing) return;
    setMsgs((m) => [...m, { from: "me", text: t }]); setInput(""); setTyping(true);
    setTimeout(() => { setMsgs((m) => [...m, reply(t)]); setTyping(false); }, 1100);
  };

  return (
    <>
      {!chatOpen && (
        <button onClick={() => setChatOpen(true)} className="glass fixed bottom-6 end-6 z-40 flex items-center gap-2 rounded-full bg-background/70 py-2 pe-5 ps-2 shadow-soft transition hover:scale-105 max-md:hidden">
          <span className="grid size-10 place-items-center rounded-full bg-ink text-ink-foreground"><Sparkles className="size-4" /></span>
          <span className="text-sm font-medium" dir="ltr">Maral AI</span>
        </button>
      )}
      <div className={cn("glass fixed bottom-4 end-4 z-[65] flex h-[min(620px,calc(100vh-6rem))] w-[calc(100%-2rem)] max-w-sm origin-bottom-left flex-col overflow-hidden rounded-3xl bg-background/85 transition-all duration-300",
        chatOpen ? "scale-100 opacity-100" : "pointer-events-none scale-90 opacity-0")}>
        <header className="flex items-center gap-3 border-b p-4">
          <span className="grid size-10 place-items-center rounded-full bg-ink text-ink-foreground"><Sparkles className="size-4" /></span>
          <div className="flex-1"><p className="font-semibold" dir="ltr" style={{ textAlign: "right" }}>Maral AI</p><p className="text-xs text-success">● آنلاین (نسخه نمایشی)</p></div>
          <button aria-label="کوچک کردن" onClick={() => setChatOpen(false)} className="grid size-8 place-items-center rounded-full hover:bg-muted"><Minus className="size-4" /></button>
          <button aria-label="بستن گفتگو" onClick={() => { setChatOpen(false); setMsgs((m) => m.slice(0, 1)); }} className="grid size-8 place-items-center rounded-full hover:bg-muted"><X className="size-4" /></button>
        </header>
        <div className="flex-1 space-y-3 overflow-y-auto p-4">
          {msgs.map((m, i) => (
            <div key={i} className={cn("flex flex-col gap-2 animate-in fade-in slide-in-from-bottom-2", m.from === "me" ? "items-end" : "items-start")}>
              <div className={cn("max-w-[85%] rounded-2xl px-4 py-2.5 text-sm leading-7", m.from === "me" ? "rounded-bl-sm bg-ink text-ink-foreground" : "rounded-br-sm bg-card")}>{m.text}</div>
              {m.items && (
                <div className="no-scrollbar flex w-full gap-2 overflow-x-auto">
                  {m.items.map((p) => (
                    <Link key={p.id} to="/product/$id" params={{ id: p.id }} onClick={() => setChatOpen(false)} className="w-32 shrink-0 rounded-2xl bg-card p-2 text-xs hover:shadow-soft">
                      <img src={p.images[0]} alt="" className="aspect-square w-full rounded-xl object-cover" />
                      <p className="mt-1.5 line-clamp-1">{p.name}</p><p className="font-semibold">{toman(finalPrice(p))}</p>
                    </Link>
                  ))}
                </div>
              )}
            </div>
          ))}
          {typing && <div className="flex w-fit gap-1 rounded-2xl bg-card px-4 py-3">{[0, 1, 2].map((i) => <span key={i} className="size-1.5 animate-blink rounded-full bg-muted-foreground" style={{ animationDelay: `${i * 0.2}s` }} />)}</div>}
          {msgs.length === 1 && (
            <div className="flex flex-col items-start gap-2 pt-2">
              {suggestions.map((s) => <button key={s} onClick={() => send(s)} className="rounded-full border bg-card/60 px-3 py-1.5 text-xs transition hover:border-accent hover:text-accent">{s}</button>)}
            </div>
          )}
          <div ref={end} />
        </div>
        <form onSubmit={(e) => { e.preventDefault(); send(input); }} className="flex items-center gap-2 border-t p-3">
          <input value={input} onChange={(e) => setInput(e.target.value)} placeholder="سوالت رو بپرس…" className="flex-1 rounded-full bg-card px-4 py-2.5 text-sm outline-none focus:ring-2 focus:ring-ring" />
          <button aria-label="ارسال" className="grid size-10 place-items-center rounded-full bg-primary text-primary-foreground"><Send className="size-4 -scale-x-100" /></button>
        </form>
      </div>
    </>
  );
}
