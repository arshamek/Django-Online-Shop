import { createFileRoute, Link } from "@tanstack/react-router";
import { Heart } from "lucide-react";
import { products } from "@/lib/shop-data";
import { useShop } from "@/lib/shop-store";
import { ProductCard } from "@/components/shop/ProductCard";

export const Route = createFileRoute("/wishlist")({
  head: () => ({
    meta: [
      { title: "علاقه‌مندی‌ها — MaralOnlineShop" },
      { name: "description", content: "محصولات ذخیره‌شده شما در مارال." },
      { property: "og:title", content: "علاقه‌مندی‌های مارال" },
      { property: "og:description", content: "محصولات ذخیره‌شده شما." },
    ],
  }),
  component: Wishlist,
});

export function WishlistGrid() {
  const { wishlist, addToCart, toggleWish } = useShop();
  const list = products.filter((p) => wishlist.includes(p.id));
  if (!list.length) return (
    <div className="flex flex-col items-center gap-3 py-24 text-center text-muted-foreground">
      <Heart className="size-10 stroke-1" /><p>هنوز محصولی ذخیره نکرده‌اید.</p>
      <Link to="/shop" className="text-foreground underline underline-offset-4">کشف محصولات</Link>
    </div>
  );
  return (
    <div className="grid grid-cols-2 gap-x-3 gap-y-8 md:grid-cols-4 md:gap-x-5">
      {list.map((p) => (
        <div key={p.id} className="space-y-2">
          <ProductCard p={p} />
          <div className="flex gap-2 text-xs">
            <button disabled={!p.available} onClick={() => addToCart({ id: p.id, size: p.sizes[0] ?? "", color: p.colors[0]?.name ?? "", qty: 1 })} className="flex-1 rounded-full bg-primary py-2.5 text-primary-foreground disabled:opacity-40">افزودن به سبد</button>
            <button onClick={() => toggleWish(p.id)} className="rounded-full border px-3 py-2.5">حذف</button>
          </div>
        </div>
      ))}
    </div>
  );
}

function Wishlist() {
  return (
    <div className="mx-auto max-w-7xl px-4 pt-10 md:px-6">
      <h1 className="mb-8 text-3xl font-semibold md:text-5xl">علاقه‌مندی‌ها</h1>
      <WishlistGrid />
    </div>
  );
}
