import trench from "@/assets/p-trench.jpg";
import women from "@/assets/women.jpg";
import men from "@/assets/men.jpg";
import shirt from "@/assets/p-shirt.jpg";
import bag from "@/assets/bag.jpg";
import blackbag from "@/assets/p-blackbag.jpg";
import shoes from "@/assets/shoes.jpg";
import sneaker from "@/assets/p-sneaker.jpg";
import boots from "@/assets/p-boots.jpg";
import accessories from "@/assets/accessories.jpg";
import scarf from "@/assets/p-scarf.jpg";
import perfume from "@/assets/p-perfume.jpg";
import hero from "@/assets/hero.jpg";
import offer from "@/assets/offer.jpg";

// All products, prices and availability below are fictitious demo data.

export const images = { hero, offer, women, men, shoes, bag, accessories, perfume, trench, scarf, sneaker, boots, blackbag, shirt };

export type CategoryId = "women" | "men" | "shoes" | "bags" | "accessories" | "beauty";
export type Source = "Trendyol" | "Namshi" | "Maral";
export type Fulfillment = "stock" | "order";

export interface Product {
  id: string;
  brand: string;
  name: string;
  originalPrice: number;
  originalCurrency: "TRY" | "AED" | "IRT";
  priceToman: number;
  discount: number; // percent
  category: CategoryId;
  source: Source;
  images: string[];
  sizes: string[];
  colors: { name: string; value: string }[];
  available: boolean;
  fulfillment: Fulfillment;
  isNew?: boolean;
  description: string;
}

export const categories: { id: CategoryId; label: string; image: string; desc: string }[] = [
  { id: "women", label: "پوشاک زنانه", image: women, desc: "کالکشن‌های فصل از طراحان و برندهای محبوب جهان." },
  { id: "men", label: "پوشاک مردانه", image: men, desc: "دوخت دقیق، پارچه‌های ممتاز و استایلی بی‌زمان." },
  { id: "shoes", label: "کفش", image: shoes, desc: "از کتانی‌های روزمره تا کفش‌های رسمی چرم." },
  { id: "bags", label: "کیف", image: bag, desc: "کیف‌های چرمی و دست‌دوز برای هر موقعیت." },
  { id: "accessories", label: "اکسسوری", image: accessories, desc: "جزئیاتی که استایل تو را کامل می‌کنند." },
  { id: "beauty", label: "آرایشی", image: perfume, desc: "عطر و مراقبت پوست از برندهای منتخب." },
];

const C = {
  black: { name: "مشکی", value: "oklch(0.2 0 0)" },
  cream: { name: "کرم", value: "oklch(0.94 0.02 85)" },
  camel: { name: "شتری", value: "oklch(0.65 0.08 65)" },
  brown: { name: "قهوه‌ای", value: "oklch(0.42 0.06 50)" },
  navy: { name: "سرمه‌ای", value: "oklch(0.3 0.06 260)" },
  white: { name: "سفید", value: "oklch(0.98 0 0)" },
  gold: { name: "طلایی", value: "oklch(0.78 0.11 85)" },
};

const p = (x: Omit<Product, "priceToman"> & { priceToman?: number }): Product => ({
  priceToman: x.priceToman ?? 0,
  ...x,
});

export const products: Product[] = [
  p({ id: "p1", brand: "Atelier Nour", name: "پالتو بلند کمربنددار پشمی", originalPrice: 8900, originalCurrency: "TRY", priceToman: 18_400_000, discount: 20, category: "women", source: "Trendyol", images: [trench, women], sizes: ["S", "M", "L", "XL"], colors: [C.camel, C.black], available: true, fulfillment: "order", isNew: true, description: "پالتویی بلند از ترکیب پشم و کشمیر با کمربند هم‌رنگ و یقه کت‌شلواری؛ انتخابی بی‌زمان برای روزهای سرد." }),
  p({ id: "p2", brand: "Lumière", name: "یقه‌اسکی بافت گیس‌باف", originalPrice: 2400, originalCurrency: "TRY", priceToman: 5_200_000, discount: 0, category: "women", source: "Trendyol", images: [women, trench], sizes: ["XS", "S", "M", "L"], colors: [C.cream, C.camel], available: true, fulfillment: "stock", description: "بافت درشت و نرم با یقه بلند و فرم آزاد؛ مناسب ست با شلوار پاچه‌گشاد." }),
  p({ id: "p3", brand: "Maison Sable", name: "کیف دستی چرم طبیعی", originalPrice: 1450, originalCurrency: "AED", priceToman: 38_500_000, discount: 15, category: "bags", source: "Namshi", images: [bag, blackbag], sizes: ["One Size"], colors: [C.camel, C.brown], available: true, fulfillment: "order", isNew: true, description: "کیف دستی ساختارمند از چرم گاوی تمام‌دانه با یراق برنجی و بند قابل تنظیم." }),
  p({ id: "p4", brand: "Noir Atelier", name: "کیف دوشی کوئیلت با زنجیر", originalPrice: 980, originalCurrency: "AED", priceToman: 26_900_000, discount: 25, category: "bags", source: "Namshi", images: [blackbag, bag], sizes: ["One Size"], colors: [C.black, C.cream], available: true, fulfillment: "stock", description: "کیف کوئیلت‌دوزی شده با زنجیر طلایی؛ قابل استفاده به‌صورت دوشی و کراس‌بادی." }),
  p({ id: "p5", brand: "Strada", name: "کتانی چانکی بژ", originalPrice: 3100, originalCurrency: "TRY", priceToman: 4_650_000, discount: 10, category: "shoes", source: "Trendyol", images: [sneaker, shoes], sizes: ["38", "39", "40", "41", "42", "43"], colors: [C.cream, C.white], available: true, fulfillment: "stock", isNew: true, description: "کتانی سبک با زیره فوم‌دار و ترکیب جیر و مش تنفسی برای استفاده روزمره." }),
  p({ id: "p6", brand: "Oxford & Co", name: "بوت چلسی جیر", originalPrice: 5200, originalCurrency: "TRY", priceToman: 9_800_000, discount: 0, category: "shoes", source: "Trendyol", images: [boots, shoes], sizes: ["40", "41", "42", "43", "44"], colors: [C.brown, C.black], available: true, fulfillment: "order", description: "بوت چلسی کلاسیک از جیر ایتالیایی با کش پهلو و زیره چرمی دوخت گودیر." }),
  p({ id: "p7", brand: "Strada", name: "کتانی چرمی سفید مینیمال", originalPrice: 2800, originalCurrency: "TRY", priceToman: 4_200_000, discount: 30, category: "shoes", source: "Trendyol", images: [shoes, sneaker], sizes: ["39", "40", "41", "42", "43", "44"], colors: [C.white, C.black], available: false, fulfillment: "order", description: "کتانی کم‌ارتفاع از چرم نرم با طراحی تمیز و بدون لوگو." }),
  p({ id: "p8", brand: "Hollis", name: "پالتو پشمی مردانه ذغالی", originalPrice: 1890, originalCurrency: "AED", priceToman: 32_000_000, discount: 20, category: "men", source: "Namshi", images: [men, shirt], sizes: ["M", "L", "XL", "XXL"], colors: [C.black, C.navy], available: true, fulfillment: "order", isNew: true, description: "پالتو تک‌سینه با برش راسته از پشم مرینو؛ ستون اصلی کمد زمستانی." }),
  p({ id: "p9", brand: "Hollis", name: "ست پیراهن آکسفورد و پولو بافت", originalPrice: 1650, originalCurrency: "TRY", priceToman: 3_600_000, discount: 0, category: "men", source: "Trendyol", images: [shirt, men], sizes: ["S", "M", "L", "XL"], colors: [C.white, C.navy], available: true, fulfillment: "stock", description: "پیراهن آکسفورد یقه‌دکمه‌دار به همراه پولو بافت پنبه‌ای." }),
  p({ id: "p10", brand: "Soie Persane", name: "روسری ابریشم طرح زنجیر", originalPrice: 340, originalCurrency: "AED", priceToman: 7_400_000, discount: 10, category: "accessories", source: "Namshi", images: [scarf, accessories], sizes: ["90×90"], colors: [C.camel, C.cream], available: true, fulfillment: "stock", description: "روسری ابریشم خالص با لبه دست‌دوز و طرح زنجیر در تناژ کاراملی." }),
  p({ id: "p11", brand: "Ora", name: "ساعت مچی طلایی کلاسیک", originalPrice: 2100, originalCurrency: "AED", priceToman: 46_000_000, discount: 12, category: "accessories", source: "Namshi", images: [accessories, scarf], sizes: ["One Size"], colors: [C.gold], available: true, fulfillment: "order", description: "ساعت مستطیلی با بند فلزی آبکاری طلا و صفحه اعداد رومی." }),
  p({ id: "p12", brand: "Ambre Studio", name: "ادوپرفیوم عنبر و چوب", originalPrice: 4200, originalCurrency: "TRY", priceToman: 6_900_000, discount: 15, category: "beauty", source: "Trendyol", images: [perfume, accessories], sizes: ["50ml", "100ml"], colors: [C.gold], available: true, fulfillment: "stock", isNew: true, description: "رایحه‌ای گرم با نت‌های عنبر، صندل و وانیل؛ ماندگاری بالا." }),
  p({ id: "p13", brand: "Lumière", name: "شلوار پاچه‌گشاد پلیسه", originalPrice: 1900, originalCurrency: "TRY", priceToman: 3_950_000, discount: 20, category: "women", source: "Maral", images: [women, hero], sizes: ["S", "M", "L"], colors: [C.cream, C.black], available: true, fulfillment: "stock", description: "شلوار فاق‌بلند با پلیسه جلو از کرپ سنگین و ریزش زیبا." }),
  p({ id: "p14", brand: "Atelier Nour", name: "ست ساتن شامپاینی", originalPrice: 6200, originalCurrency: "TRY", priceToman: 12_700_000, discount: 0, category: "women", source: "Trendyol", images: [hero, women], sizes: ["XS", "S", "M", "L"], colors: [C.cream, C.camel], available: true, fulfillment: "order", isNew: true, description: "ست ساتن براق با کت پشمی بلند؛ برای مهمانی‌های عصرانه." }),
  p({ id: "p15", brand: "Noir Atelier", name: "کت تاکسیدو زنانه مشکی", originalPrice: 2250, originalCurrency: "AED", priceToman: 41_500_000, discount: 18, category: "women", source: "Namshi", images: [offer, hero], sizes: ["S", "M", "L"], colors: [C.black], available: false, fulfillment: "order", description: "کت تاکسیدو با یقه ساتن و سرشانه ساختارمند؛ قدرت در سادگی." }),
  p({ id: "p16", brand: "Ambre Studio", name: "کرم مرطوب‌کننده ابریشمی", originalPrice: 1300, originalCurrency: "TRY", priceToman: 2_450_000, discount: 0, category: "beauty", source: "Maral", images: [perfume, scarf], sizes: ["50ml"], colors: [C.cream], available: true, fulfillment: "stock", description: "کرم سبک با پپتید و اسکوالان برای پوستی نرم و درخشان." }),
];

export const finalPrice = (pr: Product) => Math.round((pr.priceToman * (100 - pr.discount)) / 100 / 1000) * 1000;

export const brands = Array.from(new Set(products.map((x) => x.brand)));

export const faDigits = (v: string | number) => String(v).replace(/\d/g, (d) => "۰۱۲۳۴۵۶۷۸۹"[Number(d)] ?? d);
export const toman = (n: number) => `${faDigits(n.toLocaleString("en-US").replace(/,/g, "٬"))} تومان`;

export const navCategories = [
  { label: "زنانه", cat: "women" as CategoryId },
  { label: "مردانه", cat: "men" as CategoryId },
  { label: "بچگانه", cat: undefined },
  { label: "کفش", cat: "shoes" as CategoryId },
  { label: "کیف و اکسسوری", cat: "bags" as CategoryId },
  { label: "آرایشی و بهداشتی", cat: "beauty" as CategoryId },
  { label: "برندها", cat: undefined },
  { label: "تخفیف‌ها", cat: undefined, sale: true },
];

export const SHIPPING_FLAT = 450_000;
export const FREE_SHIPPING_FROM = 20_000_000;
export const shippingFor = (subtotal: number) => (subtotal === 0 || subtotal >= FREE_SHIPPING_FROM ? 0 : SHIPPING_FLAT);
