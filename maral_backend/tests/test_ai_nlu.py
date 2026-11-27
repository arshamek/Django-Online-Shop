"""Phase 9: Persian / multilingual understanding. Pure parsing tests (no network, no model)."""

from decimal import Decimal

import pytest

from apps.ai_assistant.services import nlu
from apps.ai_assistant.services.language import detect_language
from apps.ai_assistant.services.numbers import parse_budget, parse_sizes
from apps.ai_assistant.services.textnorm import fold, tokenize

pytestmark = pytest.mark.django_db  # the brand index reads the catalogue


def budget(text):
    tokens = tokenize(text)
    _sizes, used = parse_sizes(tokens)
    return parse_budget(tokens, used)


def toman(irr):
    return None if irr is None else irr / 10


# ------------------------------------------------------------------------------------------------ budgets
@pytest.mark.parametrize(
    "text,lo,hi",
    [
        ("زیر ۵ میلیون تومان", None, 5_000_000),
        ("زیر پنج تومن باشه", None, 5_000_000),  # colloquial «تومن» = million Toman
        ("تا ۳ میلیون تومان", None, 3_000_000),
        ("بین ۴ تا ۶ میلیون", 4_000_000, 6_000_000),
        ("از ۴ تا ۶ میلیون تومان", 4_000_000, 6_000_000),
        ("کمتر از ۵ میلیون ریال", None, 500_000),  # Rial: 5,000,000 IRR = 500,000 Toman
        ("زیر ٥ مليون تومان", None, 5_000_000),  # Arabic-Indic digits + Arabic letters
        ("زیر 5,000,000 تومان", None, 5_000_000),
        ("حداکثر پونصد هزار تومان", None, 500_000),
        ("بالای ۲ میلیون", 2_000_000, None),
        ("حداقل یه میلیون و پانصد هزار تومان", 1_500_000, None),
        ("پونصد تومن", None, 500_000),  # colloquial «تومن» below 1000 = thousand Toman
        ("under 5 million toman", None, 5_000_000),
        ("between 4 and 6 million toman", 4_000_000, 6_000_000),
        ("5 milyon toman altı", None, 5_000_000),
        ("أقل من 5 ملايين تومان", None, 5_000_000),
    ],
)
def test_budget_extraction(text, lo, hi):
    b = budget(text)
    assert not b.ambiguous, (text, b)
    assert toman(b.min_irr) == (None if lo is None else Decimal(lo))
    assert toman(b.max_irr) == (None if hi is None else Decimal(hi))


def test_toman_and_rial_are_not_confused():
    toman_b, rial_b = budget("زیر ۵ میلیون تومان"), budget("زیر ۵ میلیون ریال")
    assert toman_b.max_irr == rial_b.max_irr * 10


@pytest.mark.parametrize("text", ["زیر ۵۰۰۰۰۰۰ باشه", "زیر ۵ تومان", "تا ۳۰۰۰۰۰۰"])
def test_bare_amounts_are_ambiguous_and_must_be_clarified(text):
    b = budget(text)
    assert b.ambiguous and b.reason == "currency_unclear" and b.max_irr is None


@pytest.mark.parametrize("text", ["زیر ۲۰۰ دلار", "under 200 USD", "500 TL altı", "تا ۱۰۰ یورو"])
def test_foreign_currency_budgets_ask_for_toman(text):
    b = budget(text)
    assert b.ambiguous and b.reason == "foreign_currency"


def test_assumptions_are_reported_not_hidden():
    assert "toman_assumed_from_magnitude" in budget("تا ۳ میلیون").assumptions
    assert "colloquial_toman_means_million" in budget("زیر پنج تومن").assumptions
    assert "approximate_budget_plus_minus_20_percent" in budget("حدود پانصد هزار تومان").assumptions


def test_sizes_are_not_mistaken_for_budgets():
    req = nlu.parse_message("سایز چهل و دو")
    assert req.sizes == ["42"] and not req.budget.present
    req = nlu.parse_message("کفش سایز ۴۲ تا ۴۴")
    assert req.sizes == ["42", "44"] and not req.budget.present


# ------------------------------------------------------------------------------------------------ normalisation
def test_persian_arabic_variants_and_half_space_normalise_for_search():
    assert fold("كتوني") == fold("کتونی")  # ك/ي vs ک/ی
    assert fold("می‌خوام") == fold("می خوام")  # half-space (ZWNJ)
    assert tokenize("۴۲") == tokenize("٤٢") == ["42"]


def test_stored_text_is_never_modified_by_search_folding():
    from tests import ai_helpers as h

    h.sources()
    ext = h.snapshot(title="Nike Air Zoom ي")
    ext.refresh_from_db()
    assert ext.raw_title == "Nike Air Zoom ي"


# ------------------------------------------------------------------------------------------------ the example sentences
def test_primary_example_sentence():
    req = nlu.parse_message("یه کتونی نایک برای پیاده‌روی می‌خوام، زیر ۵ میلیون تومان.")
    assert req.language == "fa" and req.intent == "search"
    assert req.concept == "sneakers" and "nike" in [b.lower() for b in req.brands]
    assert req.uses == ["walking"] and toman(req.max_irr) == 5_000_000


def test_informal_and_mixed_persian_english():
    req = nlu.parse_message("یه Nike کتونی مشکی سایز ۴۲ می‌خوام تا ۴ میلیون")
    assert req.language == "fa" and req.colors == ["black"] and req.sizes == ["42"] and toman(req.max_irr) == 4_000_000
    assert req.concept == "sneakers"


def test_cheaper_follow_up_phrases():
    for text in ["ارزون‌ترشو پیدا کن", "ارزان‌تر می‌خوام", "show me cheaper ones", "daha ucuz göster", "أرخص من هذا"]:
        assert nlu.parse_message(text).cheaper, text


def test_source_commands():
    assert nlu.parse_message("فقط ترندیول").only_sources == ["trendyol"]
    assert nlu.parse_message("search only Trendyol").only_sources == ["trendyol"]
    assert nlu.parse_message("نمشی هم بگرد").add_sources == ["namshi"]
    assert nlu.parse_message("search Namshi too").add_sources == ["namshi"]


def test_colours_in_four_languages():
    assert nlu.parse_message("مدل مشکی").colors == ["black"]
    assert nlu.parse_message("black ones").colors == ["black"]
    assert nlu.parse_message("siyah olanlar").colors == ["black"]
    assert nlu.parse_message("حذاء الأسود").colors == ["black"]


def test_intents():
    assert nlu.parse_message("این دو تا رو مقایسه کن").intent == "compare"
    assert nlu.parse_message("compare these products").intent == "compare"
    assert nlu.parse_message("is size 42 available?").intent == "availability"
    assert nlu.parse_message("سایز ۴۲ موجوده؟").intent == "availability"
    assert nlu.parse_message("همینو می‌خوام").intent == "select"
    assert nlu.parse_message("اولی رو می‌خوام").ordinals == [1]
    assert nlu.parse_message("سلام").intent == "greeting"
    assert nlu.parse_message("!!!").intent == "unclear"


# ------------------------------------------------------------------------------------------------ languages
@pytest.mark.parametrize(
    "text,lang",
    [
        ("یه کتونی نایک می‌خوام", "fa"),
        ("کتونی Nike سایز 42 دارید؟", "fa"),
        ("I want black running shoes under 5 million toman", "en"),
        ("show me cheaper alternatives please", "en"),
        ("أريد حذاء رياضي أسود", "ar"),
        ("هل متوفر مقاس 42؟", "ar"),
        ("siyah spor ayakkabı istiyorum, bütçem 5 milyon toman", "tr"),
        ("daha ucuz bir şey var mı", "tr"),
    ],
)
def test_language_detection(text, lang):
    assert detect_language(text)[0] == lang


def test_forced_language_wins_over_detection():
    assert nlu.parse_message("hello", forced_language="fa").language == "fa"


def test_digit_only_message_keeps_the_fallback_language():
    code, confident = detect_language("42", fallback="ar")
    assert code == "ar" and not confident
