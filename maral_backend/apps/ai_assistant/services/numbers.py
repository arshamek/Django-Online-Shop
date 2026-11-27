"""
Persian-first number, budget and size extraction.

Everything works on folded tokens (see textnorm). Persian number words are understood (چهل و دو -> 42,
پونصد هزار -> 500000, یه میلیون -> 1000000); English number words are understood for the common cases; Turkish and
Arabic are supported with digits.

Currency rules (the part that must never be silently guessed):
  * «تومان / تومن», «ریال» and their English/Arabic spellings state the currency explicitly.
  * No currency word but a magnitude word (میلیون / هزار / million / k ...): Toman, and this is reported as an
    assumption (everyday Iranian shopping convention).
  * Colloquial «تومن»: «پنج تومن» = 5,000,000 Toman, «پونصد تومن» = 500,000 Toman (reported as an assumption).
  * Any other bare amount, or a foreign currency (TRY/AED/USD...), is AMBIGUOUS -> the assistant asks.
All money is returned as Decimal IRR (Rial); 1 Toman = 10 Rial.
"""

import re
from dataclasses import dataclass, field
from decimal import Decimal

D = Decimal
_NUM = re.compile(r"^\d+(?:\.\d+)?$")

UNITS = {
    "صفر": 0,
    "یک": 1,
    "یه": 1,
    "دو": 2,
    "سه": 3,
    "چهار": 4,
    "چار": 4,
    "پنج": 5,
    "شش": 6,
    "شیش": 6,
    "هفت": 7,
    "هشت": 8,
    "نه": 9,
    "ده": 10,
    "یازده": 11,
    "دوازده": 12,
    "سیزده": 13,
    "چهارده": 14,
    "چارده": 14,
    "پانزده": 15,
    "پونزده": 15,
    "شانزده": 16,
    "شونزده": 16,
    "هفده": 17,
    "هجده": 18,
    "هیجده": 18,
    "نوزده": 19,
    "بیست": 20,
    "سی": 30,
    "چهل": 40,
    "پنجاه": 50,
    "شصت": 60,
    "هفتاد": 70,
    "هشتاد": 80,
    "نود": 90,
    "صد": 100,
    "یکصد": 100,
    "دویست": 200,
    "سیصد": 300,
    "چهارصد": 400,
    "چارصد": 400,
    "پانصد": 500,
    "پونصد": 500,
    "پانسد": 500,
    "ششصد": 600,
    "شیشصد": 600,
    "هفتصد": 700,
    "هشتصد": 800,
    "نهصد": 900,
    # English
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
    "hundred": 100,
}
MULTIPLIERS = {
    "هزار": D(10) ** 3,
    "هزارتا": D(10) ** 3,
    "thousand": D(10) ** 3,
    "k": D(10) ** 3,
    "bin": D(10) ** 3,
    "الف": D(10) ** 3,
    "میلیون": D(10) ** 6,
    "ملیون": D(10) ** 6,
    "ملایین": D(10) ** 6,
    "million": D(10) ** 6,
    "milyon": D(10) ** 6,
    "میلیارد": D(10) ** 9,
    "ملیارد": D(10) ** 9,
    "billion": D(10) ** 9,
    "milyar": D(10) ** 9,
}
TOMAN = {"تومان", "تومانی", "toman", "tomans", "tuman", "tumans", "تومانه"}
TOMAN_COLLOQUIAL = {"تومن", "تومنی", "تومنه", "تومنم"}
RIAL = {"ریال", "ریالی", "rial", "rials", "riyal", "ریاله"}
FOREIGN = {
    "tl",
    "try",
    "lira",
    "لیر",
    "لیره",
    "aed",
    "dirham",
    "dirhem",
    "درهم",
    "usd",
    "dollar",
    "dollars",
    "dolar",
    "دلار",
    "euro",
    "eur",
    "یورو",
    "$",
    "€",
    "₺",
    "gbp",
    "pound",
}
SIZE_WORDS = {"سایز", "size", "numara", "beden", "مقاس", "شماره", "numarasi"}
SIZE_LETTERS = {"xxs", "xs", "s", "m", "l", "xl", "xxl", "xxxl", "3xl"}
SIZE_LETTERS_FA = {"اسمال": "s", "مدیوم": "m", "لارج": "l", "ایکس لارج": "xl"}

MAX_TRIGGERS = (
    "کمتر از",
    "کمتر",
    "زیر",
    "حداکثر",
    "نهایتا",
    "نهایتاً",
    "تا سقف",
    "تا",
    "under",
    "below",
    "less than",
    "up to",
    "at most",
    "max",
    "maximum",
    "within",
    "en fazla",
    "altinda",
    "alti",
    "اقل من",
    "اقل",
    "تحت",
    "حتی",
    "بودجه",
    "budget",
)
MIN_TRIGGERS = (
    "بیشتر از",
    "بالای",
    "بالاتر از",
    "حداقل",
    "above",
    "over",
    "more than",
    "at least",
    "min",
    "minimum",
    "uzeri",
    "en az",
    "اکثر من",
    "اکثر",
    "فوق",
)
RANGE_START = ("بین", "از", "between", "from", "arasinda")
APPROX = ("حدود", "تقریبا", "تقریباً", "around", "about", "approximately", "yaklasik", "حوالی")
RANGE_SEP = {"تا", "و", "to", "and", "-", "ila", "الی", "الي", "till", "until"}
TRAILING_MAX = {"altinda", "alti", "altı", "ve altı"}  # Turkish "5 milyon toman altı"
TRAILING_MIN = {"uzeri", "ustu", "ve uzeri"}


@dataclass
class Budget:
    min_irr: Decimal | None = None
    max_irr: Decimal | None = None
    ambiguous: bool = False
    reason: str = ""  # currency_unclear | foreign_currency
    assumptions: list = field(default_factory=list)
    consumed: set = field(default_factory=set)  # token indexes that belong to the budget phrase

    @property
    def present(self) -> bool:
        return self.min_irr is not None or self.max_irr is not None or self.ambiguous


@dataclass
class _Span:
    start: int
    end: int  # exclusive (includes the currency word)
    value: Decimal
    has_mult: bool
    mult: Decimal | None
    currency: str | None  # IRT | IRT_COLLOQUIAL | IRR | FOREIGN | None
    words: bool
    eligible: bool = False


def read_number(tokens: list, i: int):
    """(value, next_index, multiplier_or_None, used_words) or None."""
    n = len(tokens)
    if i >= n:
        return None
    tok = tokens[i]
    if _NUM.match(tok):
        value = D(tok)
        if i + 1 < n and tokens[i + 1] in MULTIPLIERS:
            mult = MULTIPLIERS[tokens[i + 1]]
            return value * mult, i + 2, mult, False
        return value, i + 1, None, False
    if tok not in UNITS and tok not in MULTIPLIERS:
        return None
    total, current, j, mult_used, any_number = D(0), D(0), i, None, False
    while j < n:
        w = tokens[j]
        if w in UNITS and not (w == "یه" and not (j + 1 < n and tokens[j + 1] in MULTIPLIERS)):
            current += UNITS[w]
            any_number = True
            j += 1
        elif w in MULTIPLIERS and w != "k":
            if current == 0:
                current = D(1)
            total += current * MULTIPLIERS[w]
            mult_used = MULTIPLIERS[w] if mult_used is None else max(mult_used, MULTIPLIERS[w])
            current = D(0)
            any_number = True
            j += 1
        elif (
            w in ("و", "and") and any_number and j + 1 < n and (tokens[j + 1] in UNITS or tokens[j + 1] in MULTIPLIERS)
        ):
            j += 1
        else:
            break
    if not any_number:
        return None
    return total + current, j, mult_used, True


def _currency_at(tokens: list, k: int):
    if k >= len(tokens):
        return None, k
    w = tokens[k]
    if w in TOMAN:
        return "IRT", k + 1
    if w in TOMAN_COLLOQUIAL:
        return "IRT_COLLOQUIAL", k + 1
    if w in RIAL:
        return "IRR", k + 1
    if w in FOREIGN:
        return "FOREIGN", k + 1
    return None, k


def _prev_words(tokens: list, i: int, limit: int = 3) -> list:
    return tokens[max(0, i - limit) : i]


def _find_trigger(tokens: list, start: int, group: tuple) -> tuple | None:
    """Longest trigger phrase ending right before `start` (skipping approximation words)."""
    window = tokens[max(0, start - 4) : start]
    for size in (3, 2, 1):
        if len(window) >= size:
            phrase = " ".join(window[-size:])
            if phrase in group:
                return phrase, start - size
    return None


def _size_positions(tokens: list) -> tuple[list, set]:
    sizes, used = [], set()
    n = len(tokens)
    for i, tok in enumerate(tokens):
        if tok in ("numara", "beden", "numarasi") and i >= 1 and _NUM.match(tokens[i - 1]):
            value = D(tokens[i - 1])
            if 1 <= value <= 70:
                sizes.append(str(int(value)))
                used.update({i - 1, i})
                continue
        if tok in SIZE_WORDS and i + 1 < n:
            nxt = tokens[i + 1]
            if nxt in SIZE_LETTERS:
                sizes.append(nxt)
                used.update({i, i + 1})
                continue
            if nxt == "ایکس" and i + 2 < n and tokens[i + 2] == "لارج":
                sizes.append("xl")
                used.update({i, i + 1, i + 2})
                continue
            if nxt in SIZE_LETTERS_FA:
                sizes.append(SIZE_LETTERS_FA[nxt])
                used.update({i, i + 1})
                continue
            r = read_number(tokens, i + 1)
            if r and 1 <= r[0] <= 70 and r[0] == r[0].to_integral_value():
                sizes.append(str(int(r[0])))
                used.update(range(i, r[1]))
                # size ranges: "سایز 42 تا 44" / "size 42 to 44"
                if r[1] + 1 < n and tokens[r[1]] in ("تا", "to", "-", "ila", "یا", "or"):
                    r2 = read_number(tokens, r[1] + 1)
                    if r2 and 1 <= r2[0] <= 70 and r2[0] == r2[0].to_integral_value() and r2[0] >= r[0]:
                        sizes.append(str(int(r2[0])))
                        used.update(range(r[1], r2[1]))
    return list(dict.fromkeys(sizes)), used


def parse_sizes(tokens: list) -> tuple[list, set]:
    return _size_positions(tokens)


def _to_irr(span: _Span, assumptions: list):
    """(Decimal IRR | None, ambiguity_reason)."""
    value = span.value
    cur = span.currency
    if cur == "FOREIGN":
        return None, "foreign_currency"
    if cur == "IRR":
        if not span.has_mult and value < 1000:
            return None, "currency_unclear"
        return value, ""
    if cur == "IRT_COLLOQUIAL":
        if not span.has_mult:
            if value < 100:
                value *= D(10) ** 6
                assumptions.append("colloquial_toman_means_million")
            elif value < 1000:
                value *= D(10) ** 3
                assumptions.append("colloquial_toman_means_thousand")
        return value * 10, ""
    if cur == "IRT":
        if not span.has_mult and value < 1000:
            return None, "currency_unclear"
        return value * 10, ""
    # no currency word
    if span.has_mult:
        assumptions.append("toman_assumed_from_magnitude")
        return value * 10, ""
    return None, "currency_unclear"


def parse_budget(tokens: list, exclude: set | None = None) -> Budget:
    exclude = set(exclude or ())
    spans: list[_Span] = []
    i = 0
    while i < len(tokens):
        if i in exclude:
            i += 1
            continue
        r = read_number(tokens, i)
        if r is None:
            i += 1
            continue
        value, j, mult, words = r
        if any(k in exclude for k in range(i, j)):
            i = j
            continue
        cur, k = _currency_at(tokens, j)
        spans.append(_Span(i, k, value, mult is not None, mult, cur, words))
        i = max(k, i + 1)

    for sp in spans:
        trig = _find_trigger(tokens, sp.start, MAX_TRIGGERS + MIN_TRIGGERS + RANGE_START + APPROX) or (
            sp.start >= 1 and tokens[sp.start - 1] in APPROX
        )
        sp.eligible = bool(trig) or sp.currency is not None or sp.has_mult
        # bare word-numbers ("سه") are never a budget on their own
        if sp.words and not (trig or sp.currency or sp.has_mult):
            sp.eligible = False
        # "5 milyon toman altı": trailing bound
        if sp.end < len(tokens) and tokens[sp.end] in (TRAILING_MAX | TRAILING_MIN):
            sp.eligible = True

    budget = Budget()
    # pair ranges: A <sep> B
    pairs, taken = [], set()
    for a in range(len(spans) - 1):
        s1, s2 = spans[a], spans[a + 1]
        between = tokens[s1.end : s2.start]
        if between and all(t in RANGE_SEP for t in between) and (s2.eligible or s1.eligible):
            if s2.eligible or _find_trigger(tokens, s1.start, RANGE_START):
                pairs.append((s1, s2))
                taken.update({a, a + 1})
    eligible_single = [sp for idx, sp in enumerate(spans) if idx not in taken and sp.eligible]

    def convert(sp, other=None):
        if other is not None:
            if sp.currency is None and other.currency is not None:
                sp = _Span(**{**sp.__dict__, "currency": other.currency})
            if not sp.has_mult and other.has_mult and sp.value < 1000 and sp.value * other.mult <= other.value:
                sp = _Span(**{**sp.__dict__, "value": sp.value * other.mult, "has_mult": True, "mult": other.mult})
        return _to_irr(sp, budget.assumptions)

    for s1, s2 in pairs:
        lo, r1 = convert(s1, s2)
        hi, r2 = convert(s2, s1)
        budget.consumed.update(range(s1.start, s2.end))
        reason = r1 or r2
        if reason:
            budget.ambiguous, budget.reason = True, reason
            continue
        budget.min_irr, budget.max_irr = min(lo, hi), max(lo, hi)
    for sp in eligible_single:
        irr, reason = convert(sp)
        budget.consumed.update(range(sp.start, sp.end))
        trig_start = _find_trigger(tokens, sp.start, MAX_TRIGGERS + MIN_TRIGGERS + RANGE_START + APPROX)
        if trig_start:
            budget.consumed.update(range(trig_start[1], sp.start))
        if sp.end < len(tokens) and tokens[sp.end] in (TRAILING_MAX | TRAILING_MIN):
            budget.consumed.add(sp.end)
        if reason:
            budget.ambiguous, budget.reason = True, reason
            continue
        phrase = trig_start[0] if trig_start else ""
        trailing_min = sp.end < len(tokens) and tokens[sp.end] in TRAILING_MIN
        if phrase in MIN_TRIGGERS or trailing_min:
            budget.min_irr = irr
        elif phrase in APPROX or (sp.start >= 1 and tokens[sp.start - 1] in APPROX):
            budget.min_irr, budget.max_irr = (irr * D("0.8")).quantize(D(1)), (irr * D("1.2")).quantize(D(1))
            budget.assumptions.append("approximate_budget_plus_minus_20_percent")
        elif phrase in MAX_TRIGGERS or (sp.end < len(tokens) and tokens[sp.end] in TRAILING_MAX):
            budget.max_irr = irr
        else:
            budget.max_irr = irr
            budget.assumptions.append("amount_without_bound_word_treated_as_maximum")
    if budget.ambiguous:
        budget.min_irr = budget.max_irr = None
    budget.assumptions = list(dict.fromkeys(budget.assumptions))
    return budget
