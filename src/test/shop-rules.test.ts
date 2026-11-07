import { describe, expect, it } from "vitest";
import { cartTotals } from "@/lib/shop-store";
import { finalPrice, products, shippingFor } from "@/lib/shop-data";

describe("shop pricing", () => {
  it("applies discount percent to Toman price", () => {
    const p1 = products.find((p) => p.id === "p1")!; // 18.4M, 20%
    expect(finalPrice(p1)).toBe(14_720_000);
  });
  it("ships free from 20M Toman", () => {
    expect(shippingFor(20_000_000)).toBe(0);
    expect(shippingFor(5_000_000)).toBe(450_000);
  });
  it("sums cart with quantities", () => {
    const t = cartTotals([{ id: "p2", size: "M", color: "کرم", qty: 2 }]);
    expect(t.subtotal).toBe(10_400_000);
    expect(t.total).toBe(10_850_000);
  });
});
