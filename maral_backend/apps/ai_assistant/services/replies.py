"""
Deterministic reply composer (fa / en / ar / tr) and the facts handed to the language model.

The template is both the demo-mode assistant and the fallback when a model reply fails validation. It only
states what the structured results contain: no invented products, prices, stock, sizes or delivery times.
"""

from decimal import Decimal

from ..constants import Availability, ProviderStatus
from .safety import safe_fact_text, safe_size_token

_FA_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
_AR_DIGITS = str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")
UNITS = {"fa": "تومان", "en": "Toman", "ar": "تومان", "tr": "Toman"}
SOURCE_LABEL = {
    "fa": {"maral": "مارال", "trendyol": "ترندیول", "namshi": "نمشی"},
    "en": {"maral": "Maral", "trendyol": "Trendyol", "namshi": "Namshi"},
    "ar": {"maral": "مارال", "trendyol": "ترنديول", "namshi": "نمشي"},
    "tr": {"maral": "Maral", "trendyol": "Trendyol", "namshi": "Namshi"},
}


def fmt_amount(amount: str | int | Decimal, lang: str) -> str:
    n = f"{int(Decimal(str(amount))):,}"
    if lang == "fa":
        return n.replace(",", "٬").translate(_FA_DIGITS)
    if lang == "ar":
        return n.replace(",", "٬").translate(_AR_DIGITS)
    return n


def fmt_price(amount, lang: str) -> str:
    return f"{fmt_amount(amount, lang)} {UNITS[lang]}"


def num(n: int, lang: str) -> str:
    return str(n).translate(_FA_DIGITS if lang == "fa" else _AR_DIGITS if lang == "ar" else {})


T = {
    "fa": {
        "greeting": "سلام! من دستیار خرید مارال هستم. بگویید دنبال چه محصولی می‌گردید (مثلاً دسته، برند، رنگ، سایز و بودجه) تا از کاتالوگ مارال و داده‌های ذخیره‌شده‌ی ترندیول و نمشی برایتان پیدا کنم.",
        "ask_what": "چه محصولی مدنظرتان است؟ اگر دسته، برند، رنگ، سایز یا بودجه را هم بگویید، دقیق‌تر جستجو می‌کنم.",
        "clarify_currency": "بودجه‌تان به تومان است یا ریال؟ لطفاً مبلغ را با واحد بنویسید، مثلاً «۵ میلیون تومان».",
        "clarify_foreign": "برای نمایش دقیق قیمت‌ها، بودجه را به تومان بگویید (مثلاً «۵ میلیون تومان»).",
        "found": "{n} مورد مرتبط پیدا کردم:",
        "line": "{i}. {title} — {source}، {price}{flag}",
        "line_noprice": "{i}. {title} — {source}، قیمت تخمینی در دسترس نیست",
        "why": "مطابق با: {things}",
        "provisional": "(موقت)",
        "snap_note": "نتایج {sources} از اطلاعات ذخیره‌شده‌ی قبلی است، نه جستجوی زنده، و موجودی آن‌ها هنوز تأیید نشده است.",
        "prov_note": "قیمت‌های «موقت» هنوز هزینه‌های قطعی ندارند (مثلاً ارسال یا گمرک)؛ قیمت نهایی هنگام ثبت سفارش دوباره بررسی می‌شود.",
        "relaxed": "مورد دقیقاً مطابق با {things} پیدا نشد؛ نزدیک‌ترین نتایج را نشان دادم.",
        "ask_pick": "کدام‌یک را ترجیح می‌دهید؟",
        "none": "متأسفانه با این مشخصات محصولی پیدا نکردم.",
        "none_budget": "با این بودجه محصولی پیدا نشد. اگر بودجه یا مشخصات را کمی تغییر دهید دوباره جستجو می‌کنم.",
        "limit_line": "جستجوی {source}: {reason}.",
        "st": {
            "AUTHORIZATION_REQUIRED": "نیازمند مجوز رسمی است",
            "NOT_CONFIGURED": "تنظیم نشده است",
            "UNSUPPORTED": "جستجوی زنده پشتیبانی نمی‌شود",
            "TEMPORARILY_UNAVAILABLE": "موقتاً در دسترس نیست",
            "RATE_LIMITED": "محدودیت تعداد درخواست",
            "ERROR": "با خطا مواجه شد",
            "AVAILABLE": "",
        },
        "compare_intro": "مقایسه‌ی {n} محصول (فقط بر اساس اطلاعات موجود):",
        "compare_cheapest": "ارزان‌ترین قیمت تخمینی: {title}.",
        "compare_need_two": "برای مقایسه حداقل دو محصول لازم است. ابتدا جستجو کنید.",
        "avail_intro": "وضعیت موجودی (طبق اطلاعات موجود):",
        "avail_in": "{title}: سایز {size} موجود است ({basis}).",
        "avail_not_verified": "{title}: سایز {size} در اطلاعات ذخیره‌شده دیده می‌شود ولی موجودی آن تأیید نشده است.",
        "avail_out": "{title}: سایز {size} ناموجود است.",
        "avail_none": "{title}: اطلاعاتی درباره‌ی سایز {size} ندارم.",
        "avail_generic": "{title}: وضعیت موجودی تأیید نشده است.",
        "basis_catalog": "طبق موجودی کاتالوگ مارال؛ هنگام ثبت سفارش دوباره بررسی می‌شود",
        "select_internal": "«{title}» را انتخاب کردید. برای ادامه، محصول را به سبد خرید اضافه کنید و از مسیر عادی پرداخت جلو بروید؛ سفارشی ثبت نشده است.",
        "select_external": "«{title}» از {source} را انتخاب کردید. این محصول هنوز در فروشگاه مارال قابل سفارش مستقیم نیست؛ می‌توانید «درخواست خرید» ثبت کنید تا همکاران ما قیمت، موجودی و امکان خرید را بررسی کنند. هیچ سفارش یا پرداختی ثبت نشده است.",
        "select_which": "کدام محصول را انتخاب می‌کنید؟ شماره‌ی آن را بگویید.",
        "no_context": "هنوز نتیجه‌ای نشان نداده‌ام. ابتدا بگویید دنبال چه محصولی هستید.",
        "unavailable": "دستیار در حال حاضر در دسترس نیست. لطفاً کمی بعد دوباره تلاش کنید.",
        "things": {
            "brand": "برند",
            "category": "دسته‌بندی",
            "color": "رنگ",
            "size": "سایز",
            "budget": "بودجه",
            "use": "کاربری",
        },
    },
    "en": {
        "greeting": "Hi! I'm Maral's shopping assistant. Tell me what you're looking for (category, brand, colour, size, budget) and I'll search Maral's catalogue and the stored Trendyol and Namshi data.",
        "ask_what": "What product are you looking for? Adding a category, brand, colour, size or budget helps me narrow it down.",
        "clarify_currency": "Is your budget in Toman or Rial? Please include the unit, for example “5 million Toman”.",
        "clarify_foreign": "To show accurate prices please give your budget in Toman (for example “5 million Toman”).",
        "found": "I found {n} relevant item(s):",
        "line": "{i}. {title} — {source}, {price}{flag}",
        "line_noprice": "{i}. {title} — {source}, no estimated price available",
        "why": "matches: {things}",
        "provisional": "(provisional)",
        "snap_note": "{sources} results come from previously stored data, not a live search, and their stock has not been verified.",
        "prov_note": "“Provisional” prices do not yet include every confirmed cost (such as shipping or customs); the final price is re-checked when you order.",
        "relaxed": "I couldn't find an exact match for {things}, so I'm showing the closest results.",
        "ask_pick": "Which one would you like?",
        "none": "I couldn't find any product matching that.",
        "none_budget": "Nothing matched within that budget. Adjust the budget or details and I'll search again.",
        "limit_line": "{source} search: {reason}.",
        "st": {
            "AUTHORIZATION_REQUIRED": "official authorisation is required",
            "NOT_CONFIGURED": "not configured",
            "UNSUPPORTED": "live search is not supported",
            "TEMPORARILY_UNAVAILABLE": "temporarily unavailable",
            "RATE_LIMITED": "rate limited",
            "ERROR": "an error occurred",
            "AVAILABLE": "",
        },
        "compare_intro": "Comparing {n} products (verified attributes only):",
        "compare_cheapest": "Lowest estimated price: {title}.",
        "compare_need_two": "I need at least two products to compare. Search first.",
        "avail_intro": "Availability (from the data I have):",
        "avail_in": "{title}: size {size} is in stock ({basis}).",
        "avail_not_verified": "{title}: size {size} appears in stored data but its availability is not verified.",
        "avail_out": "{title}: size {size} is out of stock.",
        "avail_none": "{title}: I have no information about size {size}.",
        "avail_generic": "{title}: availability is not verified.",
        "basis_catalog": "per Maral's catalogue stock; re-checked at checkout",
        "select_internal": "You chose “{title}”. Add it to your cart and continue through the normal checkout; no order has been placed.",
        "select_external": "You chose “{title}” from {source}. It can't be ordered directly from Maral yet; you can submit a purchase request so our team checks price, availability and feasibility. No order or payment has been created.",
        "select_which": "Which product would you like? Tell me its number.",
        "no_context": "I haven't shown any results yet. Tell me what you're looking for first.",
        "unavailable": "The assistant is unavailable right now. Please try again shortly.",
        "things": {
            "brand": "brand",
            "category": "category",
            "color": "colour",
            "size": "size",
            "budget": "budget",
            "use": "intended use",
        },
    },
    "ar": {
        "greeting": "مرحباً! أنا مساعد التسوق في مارال. أخبرني بما تبحث عنه (الفئة، العلامة، اللون، المقاس، الميزانية) وسأبحث في كتالوج مارال وبيانات ترنديول ونمشي المخزّنة.",
        "ask_what": "ما المنتج الذي تبحث عنه؟ ذكر الفئة أو العلامة أو اللون أو المقاس أو الميزانية يساعدني.",
        "clarify_currency": "هل ميزانيتك بالتومان أم بالريال؟ يرجى كتابة الوحدة، مثل «5 ملايين تومان».",
        "clarify_foreign": "لعرض الأسعار بدقة، يرجى ذكر الميزانية بالتومان (مثل «5 ملايين تومان»).",
        "found": "وجدت {n} نتيجة ذات صلة:",
        "line": "{i}. {title} — {source}، {price}{flag}",
        "line_noprice": "{i}. {title} — {source}، لا يوجد سعر تقديري",
        "why": "يطابق: {things}",
        "provisional": "(مؤقت)",
        "snap_note": "نتائج {sources} مأخوذة من بيانات مخزّنة سابقاً وليست بحثاً مباشراً، ولم يتم التحقق من توفرها.",
        "prov_note": "الأسعار «المؤقتة» لا تشمل بعد كل التكاليف المؤكدة (مثل الشحن أو الجمارك)؛ يُعاد التحقق من السعر النهائي عند الطلب.",
        "relaxed": "لم أجد تطابقاً تاماً مع {things}، لذلك عرضت أقرب النتائج.",
        "ask_pick": "أيّها تفضّل؟",
        "none": "لم أجد منتجاً مطابقاً.",
        "none_budget": "لا توجد نتائج ضمن هذه الميزانية. عدّل الميزانية أو التفاصيل وسأبحث مجدداً.",
        "limit_line": "البحث في {source}: {reason}.",
        "st": {
            "AUTHORIZATION_REQUIRED": "يتطلب ترخيصاً رسمياً",
            "NOT_CONFIGURED": "غير مهيأ",
            "UNSUPPORTED": "البحث المباشر غير مدعوم",
            "TEMPORARILY_UNAVAILABLE": "غير متاح مؤقتاً",
            "RATE_LIMITED": "تم بلوغ حد الطلبات",
            "ERROR": "حدث خطأ",
            "AVAILABLE": "",
        },
        "compare_intro": "مقارنة {n} منتجات (بالمعلومات المتوفرة فقط):",
        "compare_cheapest": "أقل سعر تقديري: {title}.",
        "compare_need_two": "أحتاج إلى منتجين على الأقل للمقارنة. ابحث أولاً.",
        "avail_intro": "التوفر (حسب البيانات المتاحة):",
        "avail_in": "{title}: المقاس {size} متوفر ({basis}).",
        "avail_not_verified": "{title}: المقاس {size} ظاهر في البيانات المخزنة لكن توفره غير مؤكد.",
        "avail_out": "{title}: المقاس {size} غير متوفر.",
        "avail_none": "{title}: لا توجد لدي معلومات عن المقاس {size}.",
        "avail_generic": "{title}: التوفر غير مؤكد.",
        "basis_catalog": "حسب مخزون كتالوج مارال؛ يُعاد التحقق عند إتمام الطلب",
        "select_internal": "اخترت «{title}». أضفه إلى السلة وتابع عبر الدفع المعتاد؛ لم يتم إنشاء أي طلب.",
        "select_external": "اخترت «{title}» من {source}. لا يمكن طلبه مباشرة من مارال بعد؛ يمكنك إرسال «طلب شراء» ليتحقق فريقنا من السعر والتوفر وإمكانية الشراء. لم يتم إنشاء أي طلب أو دفع.",
        "select_which": "أي منتج تختار؟ أخبرني برقمه.",
        "no_context": "لم أعرض نتائج بعد. أخبرني أولاً بما تبحث عنه.",
        "unavailable": "المساعد غير متاح حالياً. يرجى المحاولة لاحقاً.",
        "things": {
            "brand": "العلامة",
            "category": "الفئة",
            "color": "اللون",
            "size": "المقاس",
            "budget": "الميزانية",
            "use": "الاستخدام",
        },
    },
    "tr": {
        "greeting": "Merhaba! Ben Maral alışveriş asistanıyım. Ne aradığınızı söyleyin (kategori, marka, renk, beden, bütçe); Maral kataloğunda ve kayıtlı Trendyol/Namshi verilerinde arayayım.",
        "ask_what": "Hangi ürünü arıyorsunuz? Kategori, marka, renk, beden veya bütçe belirtirseniz daha iyi ararım.",
        "clarify_currency": "Bütçeniz Toman mı Riyal mi? Lütfen birimi belirtin, örneğin “5 milyon Toman”.",
        "clarify_foreign": "Fiyatları doğru göstermek için bütçenizi Toman olarak yazın (örneğin “5 milyon Toman”).",
        "found": "{n} ilgili ürün buldum:",
        "line": "{i}. {title} — {source}, {price}{flag}",
        "line_noprice": "{i}. {title} — {source}, tahmini fiyat yok",
        "why": "eşleşme: {things}",
        "provisional": "(geçici)",
        "snap_note": "{sources} sonuçları daha önce kaydedilmiş verilerden gelir, canlı arama değildir ve stok durumu doğrulanmamıştır.",
        "prov_note": "“Geçici” fiyatlar henüz tüm kesin maliyetleri (ör. kargo veya gümrük) içermez; nihai fiyat sipariş sırasında yeniden kontrol edilir.",
        "relaxed": "{things} için tam eşleşme bulamadım; en yakın sonuçları gösteriyorum.",
        "ask_pick": "Hangisini tercih edersiniz?",
        "none": "Bu kriterlere uygun ürün bulamadım.",
        "none_budget": "Bu bütçeye uygun ürün bulunamadı. Bütçeyi veya ayrıntıları değiştirirseniz tekrar ararım.",
        "limit_line": "{source} araması: {reason}.",
        "st": {
            "AUTHORIZATION_REQUIRED": "resmi yetki gerekiyor",
            "NOT_CONFIGURED": "yapılandırılmamış",
            "UNSUPPORTED": "canlı arama desteklenmiyor",
            "TEMPORARILY_UNAVAILABLE": "geçici olarak kullanılamıyor",
            "RATE_LIMITED": "istek sınırı aşıldı",
            "ERROR": "hata oluştu",
            "AVAILABLE": "",
        },
        "compare_intro": "{n} ürün karşılaştırması (yalnızca mevcut bilgiler):",
        "compare_cheapest": "En düşük tahmini fiyat: {title}.",
        "compare_need_two": "Karşılaştırmak için en az iki ürün gerekir. Önce arama yapın.",
        "avail_intro": "Stok durumu (elimdeki verilere göre):",
        "avail_in": "{title}: {size} beden stokta ({basis}).",
        "avail_not_verified": "{title}: {size} beden kayıtlı verilerde görünüyor ancak stok doğrulanmadı.",
        "avail_out": "{title}: {size} beden tükendi.",
        "avail_none": "{title}: {size} beden hakkında bilgim yok.",
        "avail_generic": "{title}: stok durumu doğrulanmadı.",
        "basis_catalog": "Maral katalog stokuna göre; siparişte yeniden kontrol edilir",
        "select_internal": "“{title}” ürününü seçtiniz. Sepete ekleyip normal ödeme adımlarıyla devam edin; sipariş oluşturulmadı.",
        "select_external": "{source} üzerindeki “{title}” ürününü seçtiniz. Henüz doğrudan Maral'dan sipariş edilemiyor; ekibimizin fiyat, stok ve satın alma olanağını kontrol etmesi için bir satın alma talebi oluşturabilirsiniz. Sipariş veya ödeme oluşturulmadı.",
        "select_which": "Hangi ürünü seçiyorsunuz? Numarasını yazın.",
        "no_context": "Henüz sonuç göstermedim. Önce ne aradığınızı söyleyin.",
        "unavailable": "Asistan şu anda kullanılamıyor. Lütfen biraz sonra tekrar deneyin.",
        "things": {
            "brand": "marka",
            "category": "kategori",
            "color": "renk",
            "size": "beden",
            "budget": "bütçe",
            "use": "kullanım",
        },
    },
}


def _things(codes, lang) -> str:
    names = T[lang]["things"]
    seen = []
    for code in codes:
        key = code.split(":")[0]
        if names.get(key) and names[key] not in seen:
            seen.append(names[key])
    return "، ".join(seen) if lang in ("fa", "ar") else ", ".join(seen)


def _texts(values, max_len: int, limit: int = 12) -> list:
    out = []
    for value in values or []:
        text = safe_fact_text(value["name"] if isinstance(value, dict) else value, max_len)
        if text:
            out.append(text)
    return out[:limit]


def product_facts(index: int, data: dict) -> dict:
    """
    Facts for one product. Everything outside `untrusted_text` is backend-generated and authoritative (prices,
    statuses, availability, codes). Everything inside it (title, brand, sizes, colours) originates from a marketplace
    or the catalogue and is DATA only: sanitised, injection-withheld and kept apart from the authoritative fields.
    """
    est = data["prices"]["estimated_maral"]
    avail = data["availability"]
    snap = data.get("snapshot")
    in_stock_sizes = sorted(
        {
            token
            for v in data.get("variants", [])
            if v.get("size") and v.get("availability") == Availability.IN_STOCK
            for token in [safe_size_token(v["size"])]
            if token
        }
    )
    return {
        "index": index,
        "ref": data["ref"],
        "source": data["source"]["code"],
        "is_stored_snapshot": bool(snap),
        "estimated_price_toman": est.get("amount"),
        "price_status": est.get("status"),
        "missing_costs": est.get("missing_costs", []),
        "availability": avail["status"],
        "last_known_availability": avail.get("last_known"),
        # sizes whose variant is IN_STOCK, as strictly allow-listed tokens (never populated for stored snapshots)
        "in_stock_sizes": in_stock_sizes,
        "matches": (data.get("match") or {}).get("matched", []),
        "not_matched": (data.get("match") or {}).get("unmet", []),
        "untrusted_text": {
            "title": safe_fact_text(data.get("title"), 160) or WITHHELD_TITLE,
            "brand": safe_fact_text(data.get("brand"), 60) or None,
            "sizes": _texts(data.get("sizes"), 24),
            "colors": _texts(data.get("colors"), 30),
        },
    }


WITHHELD_TITLE = "(untitled)"


def filter_facts(spec_public: dict | None, *, suspected_injection: bool = False) -> dict | None:
    """Applied filters for the model: backend vocabulary stays as is; words that came from the customer or the
    catalogue move into `untrusted_text`. When the customer message looked like an injection its free words are dropped."""
    if not spec_public:
        return spec_public
    out = {k: v for k, v in spec_public.items() if k not in ("brands", "keywords", "colors", "sizes")}
    out["untrusted_text"] = {
        "brands": _texts(spec_public.get("brands"), 60, 6),
        "keywords": [] if suspected_injection else _texts(spec_public.get("keywords"), 30, 6),
        "colors": _texts(spec_public.get("colors"), 30, 6),
        "sizes": _texts(spec_public.get("sizes"), 16, 6),
    }
    return out


def comparison_facts(cmp: dict) -> dict:
    """Comparison for the model: free-text rows become `untrusted_values`; prices/availability/status rows stay authoritative."""
    free_text = {"brand": 60, "category": 80, "sizes": 24, "colors": 30}
    rows = []
    for row in cmp["rows"]:
        limit = free_text.get(row["attribute"])
        if limit is None:
            rows.append({"attribute": row["attribute"], "values": row["values"]})
            continue
        values = [
            _texts(v, limit) if isinstance(v, list) else (safe_fact_text(v, limit) or None) for v in row["values"]
        ]
        rows.append({"attribute": row["attribute"], "untrusted_values": values})
    return {
        "products": [
            {
                "ref": p["ref"],
                "source": p["source"],
                "untrusted_text": {"title": safe_fact_text(p["title"], 160) or WITHHELD_TITLE},
            }
            for p in cmp["products"]
        ],
        "rows": rows,
        "cheapest_estimated": cmp.get("cheapest_estimated"),
        "null_means": cmp.get("null_means"),
    }


def build_facts(
    *, language, intent, spec_public, products, reports, relaxed, mode, extra=None, suspected_injection=False
) -> dict:
    return {
        "language": language,
        "intent": intent,
        "mode": mode,
        "currency": "Toman (IRT)",
        "applied_filters": filter_facts(spec_public, suspected_injection=suspected_injection),
        "relaxed_filters": relaxed,
        "products": [product_facts(i + 1, p) for i, p in enumerate(products)],
        "source_status": [
            {
                "source": r["source"],
                "status": r["status"],
                "searched": r["searched"],
                "mode": r["mode"],
                "result_count": r["result_count"],
            }
            for r in reports
        ],
        **(extra or {}),
    }


def allowed_numbers(facts: dict) -> set:
    out = set()
    for p in facts["products"]:
        if p.get("estimated_price_toman"):
            out.add(str(int(Decimal(str(p["estimated_price_toman"])))))
    budget = (facts.get("applied_filters") or {}).get("budget")
    if budget:
        for key in ("min", "max"):
            if budget.get(key):
                out.add(str(budget[key]))
    out.update(str(n) for n in facts.get("extra_numbers", []))
    return out


def _limits(lang, reports) -> list[str]:
    """One line per source whose status is not AVAILABLE (for Trendyol/Namshi that is the live-search status)."""
    labels = SOURCE_LABEL[lang]
    return [
        T[lang]["limit_line"].format(source=labels[r["source"]], reason=T[lang]["st"].get(r["status"], r["status"]))
        for r in reports
        if r["status"] != ProviderStatus.AVAILABLE
    ]


def render_search(lang: str, *, products: list, reports: list, relaxed: list, has_budget: bool, mode: str = "") -> str:
    t = T[lang]
    labels = SOURCE_LABEL[lang]
    limits = _limits(lang, reports)
    if not products:
        parts = [t["none_budget"] if has_budget else t["none"], *limits]
        return " ".join(parts)
    lines = [t["found"].format(n=num(len(products), lang))]
    for i, p in enumerate(products, 1):
        est = p["prices"]["estimated_maral"]
        source = labels[p["source"]["code"]]
        if est.get("amount") is not None:
            flag = f" {t['provisional']}" if est.get("provisional") else ""
            line = t["line"].format(
                i=num(i, lang), title=p["title"], source=source, price=fmt_price(est["amount"], lang), flag=flag
            )
        else:
            line = t["line_noprice"].format(i=num(i, lang), title=p["title"], source=source)
        matched = (p.get("match") or {}).get("matched", [])
        if matched:
            line += f" ({t['why'].format(things=_things(matched, lang))})"
        lines.append(line)
    notes = []
    snap_sources = sorted({p["source"]["code"] for p in products if p.get("snapshot")})
    if snap_sources:
        sep = "، " if lang in ("fa", "ar") else ", "
        notes.append(t["snap_note"].format(sources=sep.join(labels[s] for s in snap_sources)))
    if any(
        p["prices"]["estimated_maral"].get("provisional") and p["prices"]["estimated_maral"].get("amount")
        for p in products
    ):
        notes.append(t["prov_note"])
    if relaxed:
        notes.append(t["relaxed"].format(things=_things(relaxed, lang)))
    notes.extend(limits)
    notes.append(t["ask_pick"])
    return "\n".join(lines) + "\n" + " ".join(notes)


def render_compare(lang: str, cmp: dict, products: list) -> str:
    t = T[lang]
    out = [t["compare_intro"].format(n=num(len(products), lang))]
    if cmp.get("cheapest_estimated"):
        ref = cmp["cheapest_estimated"]
        title = next(p["title"] for p in products if p["ref"] == ref)
        out.append(t["compare_cheapest"].format(title=title))
    out.append(t["ask_pick"])
    return " ".join(out)


def render_availability(lang: str, rows: list) -> str:
    t = T[lang]
    out = [t["avail_intro"]]
    out.extend(rows)
    return "\n".join(out)


def availability_line(lang, title, size, state, basis_catalog=False):
    t = T[lang]
    if state == "in_stock":
        return t["avail_in"].format(title=title, size=size, basis=t["basis_catalog"] if basis_catalog else "")
    if state == "not_verified":
        return t["avail_not_verified"].format(title=title, size=size)
    if state == "out_of_stock":
        return t["avail_out"].format(title=title, size=size)
    if state == "generic":
        return t["avail_generic"].format(title=title)
    return t["avail_none"].format(title=title, size=size)
