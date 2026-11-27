"""
One assistant turn. Everything before the model call is deterministic backend code:

  validate + limits -> conversation (owner only) -> parse (language, filters, budget) -> clarify OR discover ->
  facts -> the model phrases the reply -> reply validated (else template) -> persist -> usage record.

The model only receives facts and returns text. It has no tools; prices, stock, orders and payments are never
writable from here. The only state kept is the current conversation's own (`Conversation.state`).
"""

import time
import uuid
from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import NotFound

from apps.audit import services as audit
from apps.common.exceptions import BusinessRuleViolation

from ..constants import ALL_SOURCES, SOURCE_MARAL, SUPPORTED_LANGUAGES, Availability
from ..models import AssistantConfig, Conversation, Message, MessageRole, UsageStatus
from . import limits, nlu, pricing_estimate, recommend, replies
from .discovery import discover
from .providers.base import AIProviderError, AIRequest
from .providers.factory import resolve
from .providers.prompts import build_system_prompt
from .safety import clean_text, looks_like_injection, validate_reply
from .sources import internal, snapshots
from .sources.base import Candidate, SearchSpec


class AssistantDisabled(BusinessRuleViolation):
    status_code = 503
    default_code = "ai_disabled"
    default_detail = "The shopping assistant is currently disabled."


# ---------------------------------------------------------------------------------------------- conversations
def get_conversation(user, public_id) -> Conversation:
    """Ownership is part of the query: someone else's conversation is a plain 404."""
    conv = Conversation.objects.filter(user=user, public_id=public_id).first()
    if conv is None:
        raise NotFound("Conversation not found.")
    return conv


def _history(conversation: Conversation, config) -> list:
    """
    What the model may see of this conversation's past: the customer's earlier messages ONLY, as untrusted data.
    Assistant turns are not replayed (the facts carry all state), messages flagged as suspected prompt injection are
    dropped (also re-checked here for rows saved before the flag existed), and every message is sanitised and
    truncated. The provider presents the list as quoted data, never as instructions or as conversation roles.
    """
    rows = conversation.messages.filter(role=MessageRole.USER).order_by("-id")[: config.context_messages]
    out = []
    for message in reversed(list(rows)):
        if (message.data or {}).get("injection_suspected") or looks_like_injection(message.content):
            continue
        text = clean_text(message.content, 300)
        if text:
            out.append({"role": "user", "text": text})
    return out


# ---------------------------------------------------------------------------------------------- filters
def _build_spec(parsed, state, config, language) -> tuple[SearchSpec, dict]:
    prev = dict(state.get("filters") or {})
    fresh_search = parsed.has_new_search_terms
    f = {} if fresh_search else prev
    if parsed.concept:
        f["concept"] = parsed.concept
    if parsed.brands:
        f["brands"], f["brand_ids"] = parsed.brands, parsed.brand_ids
    if parsed.keywords and not parsed.concept:
        f["keywords"] = parsed.keywords
    if parsed.uses:
        f["uses"] = parsed.uses
    if parsed.colors:
        f["colors"] = parsed.colors
    if parsed.sizes:
        f["sizes"] = parsed.sizes
    if parsed.budget.min_irr is not None:
        f["min_irr"] = str(parsed.budget.min_irr)
    if parsed.budget.max_irr is not None:
        f["max_irr"] = str(parsed.budget.max_irr)
    sources = list(f.get("sources") or prev.get("sources") or ALL_SOURCES)
    if parsed.only_sources:
        sources = list(parsed.only_sources)
    for code in parsed.add_sources:
        if code not in sources:
            sources.append(code)
    f["sources"] = sources
    if parsed.cheaper:
        f["sort"] = "price_asc"
    elif fresh_search:
        f["sort"] = "relevance"
    return _spec_from(f, language, config), f


def _spec_from(f: dict, language: str, config) -> SearchSpec:
    sources = [s for s in f.get("sources", ALL_SOURCES) if s in ALL_SOURCES]
    return SearchSpec(
        language=language,
        concept=f.get("concept", ""),
        brand_ids=f.get("brand_ids", []),
        brands=f.get("brands", []),
        keywords=f.get("keywords", []),
        uses=f.get("uses", []),
        colors=f.get("colors", []),
        sizes=f.get("sizes", []),
        min_irr=Decimal(f["min_irr"]) if f.get("min_irr") else None,
        max_irr=Decimal(f["max_irr"]) if f.get("max_irr") else None,
        sort=f.get("sort", "relevance"),
        sources=sources,
    )


def _apply_request_overrides(payload: dict, spec: SearchSpec, f: dict) -> None:
    """Structured filters in the API body (budget in Toman) override what was parsed from the text."""
    flt = payload.get("filters") or {}
    if flt.get("colors"):
        spec.colors = f["colors"] = [str(c).lower() for c in flt["colors"]][:5]
    if flt.get("sizes"):
        spec.sizes = f["sizes"] = [str(s).lower() for s in flt["sizes"]][:5]
    for source in (payload.get("budget") or {}, {"min": flt.get("min_price"), "max": flt.get("max_price")}):
        if source.get("min") is not None:
            spec.min_irr = Decimal(str(source["min"])) * 10
            f["min_irr"] = str(spec.min_irr)
        if source.get("max") is not None:
            spec.max_irr = Decimal(str(source["max"])) * 10
            f["max_irr"] = str(spec.max_irr)
    preferred = [s for s in (payload.get("preferred_sources") or []) if s in ALL_SOURCES]
    if preferred:
        spec.sources = f["sources"] = preferred


def _refs(payload: dict, parsed, state: dict) -> list:
    refs = [r for r in (payload.get("product_references") or []) if r.get("source") in ALL_SOURCES]
    if not refs and parsed.ordinals:
        last = state.get("last_results") or []
        refs = [last[i - 1] for i in parsed.ordinals if 0 < i <= len(last)]
    return refs[:5]


def _load(refs: list, spec: SearchSpec, ctx, config) -> list[Candidate]:
    """
    Re-reads referenced products from the database; client-supplied product data is never trusted, and a reference
    is honoured only when the product would also be visible to a normal search (same source / status / pipeline /
    fixture rules - see `snapshots.visible_listings` and the catalogue's `visible_products`).
    """
    from apps.catalog import selectors

    out: list[Candidate] = []
    rule_set = None
    for ref in refs:
        try:
            pid = uuid.UUID(str(ref.get("id")))
        except (ValueError, TypeError):
            continue
        if ref["source"] not in config.enabled_sources:
            continue  # source disabled by staff
        if ref["source"] == SOURCE_MARAL:
            product = selectors.with_card_data(selectors.visible_products().filter(public_id=pid), ctx).first()
            if product is not None:
                out.append(internal.to_candidate(product, ctx, []))
        else:
            product = (
                snapshots.visible_listings(config, ref["source"])
                .select_related("source", "canonical_product__category")
                .filter(public_id=pid)
                .first()
            )
            if product is None:
                continue
            if rule_set is None:
                rule_set = pricing_estimate.active_rule_set()
            cand = snapshots.build_candidate(
                product, spec, config=config, rule_set=rule_set, now=timezone.now(), apply_filters=False
            )
            if cand is not None:
                out.append(recommend.score(cand, spec))
    return out


# ---------------------------------------------------------------------------------------------- facts + reply
def _extra_numbers(products: list) -> list:
    out = []
    for p in products:
        original = (p["prices"].get("original") or {}).get("amount")
        if original:
            out.append(str(int(Decimal(original))))
    return out


def _compose(*, choice, config, language, intent, text, facts, message, history, now_flagged) -> tuple[str, dict]:
    """Model-phrased text when a live provider is configured; the template otherwise or when the model fails."""
    meta = {"fallback": False, "error_code": "", "input_tokens": 0, "output_tokens": 0}
    if choice.demo_mode:
        return text, meta
    facts = {**facts, "template_text": text}
    request = AIRequest(
        language=language,
        system_prompt=build_system_prompt(language, config.extra_instructions),
        customer_message=(
            "[withheld: suspected prompt injection]" if now_flagged else clean_text(message, config.max_input_chars)
        ),
        history=history,
        facts=facts,
        timeout=config.provider_timeout_seconds,
    )
    try:
        reply = choice.provider.generate(request)
    except AIProviderError as exc:
        meta.update(fallback=True, error_code=exc.code)
        return text, meta
    meta.update(input_tokens=reply.input_tokens, output_tokens=reply.output_tokens)
    ok, reason = validate_reply(
        reply.text,
        allowed_numbers=replies.allowed_numbers(facts),
        allowed_urls=set(),  # the model is never given URLs, so none may appear in its reply
        all_in_stock=bool(facts["products"])
        and all(p["availability"] == Availability.IN_STOCK for p in facts["products"]),
        in_stock_sizes=[set(p.get("in_stock_sizes", [])) for p in facts["products"]],
    )
    if not ok:
        meta.update(fallback=True, error_code=f"invalid_reply:{reason}")
        return text, meta
    return reply.text.strip(), meta


def _lines_for_availability(language: str, cands: list, sizes: list) -> list:
    rows = []
    for cand in cands:
        d = cand.data
        title = d["title"]
        if not sizes:
            rows.append(replies.availability_line(language, title, "", "generic"))
            continue
        for size in sizes:
            matches = [
                v
                for v in d["variants"]
                if (v.get("size_code") or "").lower() == size or (v.get("size") or "").lower() == size
            ]
            if not matches:
                rows.append(replies.availability_line(language, title, size, "none"))
            elif cand.kind == "internal":
                if any(v["availability"] == Availability.IN_STOCK for v in matches):
                    rows.append(replies.availability_line(language, title, size, "in_stock", basis_catalog=True))
                elif all(v["availability"] == Availability.OUT_OF_STOCK for v in matches):
                    rows.append(replies.availability_line(language, title, size, "out_of_stock"))
                else:
                    rows.append(replies.availability_line(language, title, size, "not_verified"))
            else:
                rows.append(replies.availability_line(language, title, size, "not_verified"))
    return rows


def _selection(cand: Candidate) -> dict:
    d = cand.data
    options = {"sizes": d["sizes"], "colors": d["colors"]}
    if cand.kind == "internal":
        return {
            "type": "internal_product",
            "product_ref": d["ref"],
            "next_step": "add_to_cart",
            "cart_endpoint": "/api/v1/cart/items",
            "variants": [v for v in d["variants"] if v["availability"] != Availability.OUT_OF_STOCK],
            "order_created": False,
            "note": "Use the normal cart and checkout. Price and availability are re-validated at checkout.",
        }
    return {
        "type": "purchase_inquiry",
        "product_ref": d["ref"],
        "next_step": "create_purchase_inquiry",
        "inquiry_endpoint": "/api/v1/ai/purchase-inquiries",
        "options": options,
        "required": ["product_ref", "quantity"] + (["size"] if d["sizes"] else []) + (["color"] if d["colors"] else []),
        "order_created": False,
        "payment_created": False,
        "note": "A purchase inquiry is not an order. Staff re-check price, availability and feasibility first.",
    }


# ---------------------------------------------------------------------------------------------- the turn
def handle_turn(request, user, payload: dict) -> dict:
    config = AssistantConfig.load()
    started = time.monotonic()
    choice = resolve(config)
    if not config.enabled:
        limits.record(user, provider=choice.provider.name, status=UsageStatus.DISABLED, demo_mode=choice.demo_mode)
        raise AssistantDisabled()
    limits.check_daily(user, config)

    message = clean_text(payload["message"], config.max_input_chars)
    if not message:
        raise BusinessRuleViolation("The message is empty.", code="empty_message")
    injection = looks_like_injection(message)

    if payload.get("conversation_id"):
        conversation = get_conversation(user, payload["conversation_id"])
    else:
        limits.check_conversation_cap(user)
        conversation = Conversation.objects.create(user=user, title=message[:60], language="fa")
    state = dict(conversation.state or {})

    requested = (payload.get("language") or "").lower()
    forced = requested if requested in SUPPORTED_LANGUAGES and requested in config.supported_languages else None
    parsed = nlu.parse_message(message, fallback_language=conversation.language, forced_language=forced)
    language = parsed.language
    if not forced and not parsed.language_confident and conversation.messages.exists():
        language = conversation.language
    if language not in config.supported_languages:
        language = "fa" if "fa" in config.supported_languages else config.supported_languages[0]
    ctx = internal.make_context(request, language)
    history = _history(conversation, config)

    out = {
        "conversation_id": str(conversation.public_id),
        "language": language,
        "intent": parsed.intent,
        "products": [],
        "comparison": None,
        "selection": None,
        "availability_check": None,
        "applied_filters": None,
        "relaxed_filters": [],
        "provider_status": [],
        "clarification": None,
        "assumptions": list(parsed.budget.assumptions),
        "prompt_injection_suspected": injection,
    }
    new_state = dict(state)
    t = replies.T[language]
    text, facts, call_model = "", {}, False

    if parsed.budget.ambiguous:
        out["intent"] = "clarify"
        # keep what the customer already told us (this conversation only) so the answer to the question completes it
        _pending_spec, pending = _build_spec(parsed, state, config, language)
        new_state["filters"] = pending
        key = "clarify_foreign" if parsed.budget.reason == "foreign_currency" else "clarify_currency"
        text = t[key]
        out["clarification"] = {
            "reason": parsed.budget.reason,
            "question": text,
            "options": ["toman", "rial"] if key == "clarify_currency" else ["toman"],
        }
    elif parsed.intent == "greeting":
        text = t["greeting"]
    elif parsed.intent == "unclear":
        text = t["ask_what"]
        out["clarification"] = {"reason": "need_product_description", "question": text, "options": []}
    elif parsed.intent in ("compare", "availability", "select"):
        spec = _spec_from(dict(state.get("filters") or {}), language, config)
        if parsed.sizes:
            spec.sizes = parsed.sizes
        refs = _refs(payload, parsed, state)
        last = state.get("last_results") or []
        if not refs:
            refs = (
                last[:3]
                if parsed.intent == "compare"
                else (last[:1] if parsed.intent == "availability" else (last if len(last) == 1 else []))
            )
        cands = _load(refs, spec, ctx, config) if refs else []
        if parsed.intent == "compare":
            if len(cands) < 2:
                text = t["compare_need_two"]
            else:
                cmp = recommend.compare(cands)
                out["comparison"], out["products"] = cmp, [c.data for c in cands]
                text = replies.render_compare(language, cmp, out["products"])
                facts = replies.build_facts(
                    language=language,
                    intent="compare",
                    spec_public=spec.as_public(),
                    products=out["products"],
                    reports=[],
                    relaxed=[],
                    mode="demo" if choice.demo_mode else "live",
                    extra={
                        "comparison": replies.comparison_facts(cmp),
                        "extra_numbers": _extra_numbers(out["products"]),
                    },
                    suspected_injection=injection,
                )
                call_model = True
        elif parsed.intent == "availability":
            if not cands:
                text = t["no_context"]
            else:
                out["products"] = [c.data for c in cands]
                rows = _lines_for_availability(language, cands, parsed.sizes or spec.sizes)
                out["availability_check"] = {"sizes": parsed.sizes or spec.sizes, "lines": rows}
                text = replies.render_availability(language, rows)
        else:
            if len(cands) != 1:
                text = t["select_which"] if last else t["no_context"]
            else:
                cand = cands[0]
                out["products"] = [cand.data]
                out["selection"] = _selection(cand)
                key = "select_internal" if cand.kind == "internal" else "select_external"
                text = t[key].format(title=cand.data["title"], source=replies.SOURCE_LABEL[language][cand.source])
    else:  # search
        spec, f = _build_spec(parsed, state, config, language)
        _apply_request_overrides(payload, spec, f)
        if not spec.has_terms():
            out["intent"] = "clarify"
            text = t["ask_what"]
            out["clarification"] = {"reason": "need_product_description", "question": text, "options": []}
            new_state["filters"] = f
        else:
            if parsed.cheaper and spec.max_irr is None:
                prices = [Decimal(p) for p in (state.get("last_prices") or [])]
                ref_prices = prices if prices else []
                if ref_prices:
                    # one-turn refinement: strictly below the average of what was just shown (not remembered afterwards)
                    spec.max_irr = sum(ref_prices) / len(ref_prices) - 1
            result = discover(spec, ctx, config)
            out["products"] = result.products
            out["relaxed_filters"] = result.relaxed
            out["provider_status"] = [r.as_dict() for r in result.reports]
            out["applied_filters"] = spec.as_public()
            has_budget = spec.min_irr is not None or spec.max_irr is not None
            text = replies.render_search(
                language,
                products=out["products"],
                reports=out["provider_status"],
                relaxed=result.relaxed,
                has_budget=has_budget,
            )
            facts = replies.build_facts(
                language=language,
                intent="search",
                spec_public=spec.as_public(),
                products=out["products"],
                reports=out["provider_status"],
                relaxed=result.relaxed,
                mode="demo" if choice.demo_mode else "live",
                extra={"extra_numbers": _extra_numbers(out["products"])},
                suspected_injection=injection,
            )
            call_model = True
            new_state["filters"] = f
            new_state["last_results"] = [c.data["ref"] for c in result.candidates]
            new_state["last_prices"] = [str(c.price_irr) for c in result.candidates if c.price_irr is not None]

    meta = {"fallback": False, "error_code": "", "input_tokens": 0, "output_tokens": 0}
    if call_model:
        text, meta = _compose(
            choice=choice,
            config=config,
            language=language,
            intent=out["intent"],
            text=text,
            facts=facts,
            message=message,
            history=history,
            now_flagged=injection,
        )

    out["assistant_message"] = text
    out["demo_mode"] = choice.demo_mode
    out["provider"] = choice.public()
    out["ai_fallback"] = meta["fallback"]
    out["applied_filters"] = out["applied_filters"] or (spec_public(new_state, language, config))

    with transaction.atomic():
        Message.objects.create(
            conversation=conversation,
            role=MessageRole.USER,
            content=message,
            language=language,
            data={"injection_suspected": True} if injection else {},
        )
        reply = Message.objects.create(
            conversation=conversation,
            role=MessageRole.ASSISTANT,
            content=text,
            language=language,
            demo_mode=choice.demo_mode,
            data={
                "intent": out["intent"],
                "refs": [p["ref"] for p in out["products"]],
                "provider_status": out["provider_status"],
                "clarification": out["clarification"],
            },
        )
        new_state["language"] = language
        conversation.state, conversation.language, conversation.updated_at = new_state, language, timezone.now()
        conversation.save(update_fields=["state", "language", "updated_at"])
        limits.record(
            user,
            conversation=conversation,
            provider=choice.provider.name,
            model=choice.provider.model,
            demo_mode=choice.demo_mode,
            status=UsageStatus.PROVIDER_ERROR
            if meta["error_code"] and not meta["error_code"].startswith("invalid_reply") and meta["fallback"]
            else (UsageStatus.FALLBACK if meta["fallback"] else UsageStatus.OK),
            error_code=meta["error_code"],
            input_tokens=meta["input_tokens"],
            output_tokens=meta["output_tokens"],
            latency_ms=int((time.monotonic() - started) * 1000),
            flagged=injection,
        )
    if injection:
        audit.record("ai.prompt_injection_suspected", request=request, actor=user, target=conversation)
    out["message_id"] = reply.pk
    return out


def spec_public(state: dict, language: str, config) -> dict | None:
    f = state.get("filters")
    return _spec_from(f, language, config).as_public() if f else None
