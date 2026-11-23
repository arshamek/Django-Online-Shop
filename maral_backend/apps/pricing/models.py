"""
Pricing (docs/pricing/PHASE6_OVERVIEW.md, docs/architecture/DOMAIN_MODEL.md section 5).

* A `PricingRuleSet` is a numbered, versioned bundle of decisions (profit method, target, which costs are required ...)
  plus its `PricingRule` rows. Published sets are immutable; staff edit a draft, preview it, then publish.
* Undecided business choices are stored as `pending` and block publication of prices; nothing is silently assumed.
* `PriceQuote` / `PriceQuoteItem` / `PriceQuoteComponent` are immutable snapshots. A locked quote never changes.
* All money is Decimal. IRR amounts are whole rials; each component is rounded HALF_UP to whole IRR when it is created.
"""

import uuid
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models, transaction
from django.utils import timezone


# --------------------------------------------------------------------------------------------------- choices
class ProfitMethod(models.TextChoices):
    PENDING = "pending", "Pending owner decision"
    MARKUP = "markup", "Markup (profit = % of cost)"
    MARGIN = "margin", "Margin (profit = % of selling price)"


class EstimatesPolicy(models.TextChoices):
    PENDING = "pending", "Pending owner decision (treated as block)"
    ALLOW = "allow", "Allow estimated costs (flagged)"
    BLOCK = "block", "Only confirmed costs may be published"


class SourcePriceBasis(models.TextChoices):
    EFFECTIVE = "effective", "Customer price, else sale price, else list price"
    SALE_OR_LIST = "sale_or_list", "Sale price, else list price"
    LIST = "list", "List price only"


class BreakdownVisibility(models.TextChoices):
    NONE = "none", "Customers see no breakdown"
    SUMMARY = "summary", "Rate, original price and cost totals"
    ITEMIZED = "itemized", "Every non-profit line"


class RuleSetStatus(models.TextChoices):
    DRAFT = "draft", "Draft (editable)"
    PUBLISHED = "published", "Published (immutable)"
    ARCHIVED = "archived", "Archived"


class RuleKind(models.TextChoices):
    PURCHASE_FEE = "purchase_fee", "Purchase fee"
    SHIPPING = "shipping", "Shipping"
    CUSTOMS = "customs", "Customs / duties"
    SERVICE_FEE = "service_fee", "Service fee"
    PROFIT_PERCENT = "profit_percent", "Profit percentage (markup or margin per method)"
    PROFIT_FIXED = "profit_fixed", "Fixed profit amount"
    MIN_PROFIT = "min_profit", "Minimum profit"
    ROUNDING = "rounding", "Rounding"


COST_KINDS = (RuleKind.PURCHASE_FEE, RuleKind.SHIPPING, RuleKind.CUSTOMS, RuleKind.SERVICE_FEE)  # evaluation order
EXCLUSIVE_KINDS = (RuleKind.PROFIT_PERCENT, RuleKind.MIN_PROFIT, RuleKind.ROUNDING)  # one winner per calculation


class CalcMethod(models.TextChoices):
    FIXED = "fixed", "Fixed amount"
    PERCENT_OF_ITEM = "percent_of_item", "% of the converted item price"
    PERCENT_OF_BASE = "percent_of_base", "% of item + costs calculated before this one"
    PER_KG = "per_kg", "Amount per kilogram"


class ValueStatus(models.TextChoices):
    CONFIRMED = "confirmed", "Confirmed (from a tariff / contract / invoice)"
    ESTIMATED = "estimated", "Estimated"
    UNKNOWN = "unknown", "Unknown (value not provided yet)"


class ChargeMode(models.TextChoices):
    PENDING = "pending", "Pending owner decision"
    INCLUDED = "included", "Included in the product price"
    SEPARATE = "separate", "Charged to the customer as a separate line"
    ABSORBED = "absorbed", "Paid by Maral, not billed"


class RoundingMode(models.TextChoices):
    NEAREST = "nearest", "Nearest (half up)"
    UP = "up", "Always up"
    DOWN = "down", "Always down"


class PriceCause(models.TextChoices):
    INITIAL = "initial", "First price"
    RATE_CHANGE = "rate_change", "Exchange rate changed"
    SOURCE_PRICE_CHANGE = "source_price_change", "Source price changed"
    RULE_CHANGE = "rule_change", "Pricing rules changed"
    MANUAL = "manual", "Manual / staff request"


# --------------------------------------------------------------------------------------------------- rule sets
class PricingRuleSet(models.Model):
    version = models.PositiveIntegerField(unique=True, editable=False)
    name = models.CharField(max_length=120, blank=True)
    notes = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=RuleSetStatus.choices, default=RuleSetStatus.DRAFT)
    cloned_from = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    effective_from = models.DateTimeField(null=True, blank=True)
    published_at = models.DateTimeField(null=True, blank=True)
    published_by = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_by = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    # ---- owner decisions (see docs/pricing/PHASE6_DECISIONS.md)
    profit_method = models.CharField(max_length=8, choices=ProfitMethod.choices, default=ProfitMethod.PENDING)
    target_profit_percent = models.DecimalField(
        max_digits=7,
        decimal_places=4,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0"))],
        help_text="Default profit percentage when no profit_percent rule is more specific. Data, never code.",
    )
    target_profit_confirmed = models.BooleanField(
        default=False, help_text="False = an initial figure not yet approved by the owner."
    )
    required_cost_kinds = models.JSONField(
        default=list, blank=True, help_text="Cost kinds that must be addressed (never zero by omission)."
    )
    required_for_fulfillment = models.JSONField(default=list, blank=True)
    estimates_policy = models.CharField(max_length=8, choices=EstimatesPolicy.choices, default=EstimatesPolicy.PENDING)
    quote_validity_minutes = models.PositiveIntegerField(default=30, validators=[MinValueValidator(1)])
    source_price_basis = models.CharField(
        max_length=14, choices=SourcePriceBasis.choices, default=SourcePriceBasis.EFFECTIVE
    )
    customer_breakdown = models.CharField(
        max_length=10, choices=BreakdownVisibility.choices, default=BreakdownVisibility.NONE
    )
    default_destination_country = models.CharField(max_length=2, blank=True)

    class Meta:
        db_table = "pricing_rule_set"
        ordering = ["-version"]

    def __str__(self):
        return f"v{self.version} ({self.status})"

    def save(self, *args, **kwargs):
        if self.pk is not None:
            stored = PricingRuleSet.objects.filter(pk=self.pk).values_list("status", flat=True).first()
            if stored and stored != RuleSetStatus.DRAFT:
                fields = kwargs.get("update_fields")
                if not (fields and set(fields) <= {"status"} and self.status == RuleSetStatus.ARCHIVED):
                    raise ValidationError("A published rule set is immutable; clone it into a draft instead.")
        elif not self.version:
            with transaction.atomic():
                latest = PricingRuleSet.objects.order_by("-version").values_list("version", flat=True).first()
                self.version = (latest or 0) + 1
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.status != RuleSetStatus.DRAFT:
            raise ValidationError("Only a draft rule set can be deleted.")
        return super().delete(*args, **kwargs)

    @property
    def is_editable(self) -> bool:
        return self.status == RuleSetStatus.DRAFT


class PricingRule(models.Model):
    rule_set = models.ForeignKey(PricingRuleSet, on_delete=models.CASCADE, related_name="rules")
    code = models.SlugField(max_length=60)
    name = models.CharField(max_length=160)
    kind = models.CharField(max_length=16, choices=RuleKind.choices)
    calc_method = models.CharField(max_length=16, choices=CalcMethod.choices, default=CalcMethod.FIXED)
    amount = models.DecimalField(
        max_digits=18,
        decimal_places=6,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0"))],
        help_text="Percent for percent methods and profit_percent; otherwise an amount in `currency`.",
    )
    currency = models.ForeignKey(
        "currencies.Currency", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    value_status = models.CharField(max_length=10, choices=ValueStatus.choices, default=ValueStatus.UNKNOWN)
    charge_mode = models.CharField(max_length=10, choices=ChargeMode.choices, default=ChargeMode.PENDING)
    min_irr = models.DecimalField(
        max_digits=18, decimal_places=0, null=True, blank=True, validators=[MinValueValidator(Decimal("0"))]
    )
    max_irr = models.DecimalField(
        max_digits=18, decimal_places=0, null=True, blank=True, validators=[MinValueValidator(Decimal("0"))]
    )
    rounding_mode = models.CharField(max_length=8, choices=RoundingMode.choices, blank=True)
    rounding_step_irr = models.DecimalField(
        max_digits=18, decimal_places=0, null=True, blank=True, validators=[MinValueValidator(Decimal("10"))]
    )

    # ---- scope (empty = any)
    source = models.ForeignKey(
        "product_sources.ProductSource", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    source_country = models.CharField(max_length=2, blank=True)
    destination_country = models.CharField(max_length=2, blank=True)
    category = models.ForeignKey(
        "categories.Category", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    brand = models.ForeignKey("brands.Brand", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    product = models.ForeignKey("catalog.Product", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    fulfillment_type = models.CharField(max_length=20, blank=True)
    sales_model = models.CharField(max_length=40, blank=True)
    shipping_method = models.CharField(max_length=40, blank=True)

    slot = models.CharField(
        max_length=40, blank=True, help_text="Rules in one slot compete: the most specific wins. Empty = additive."
    )
    priority = models.IntegerField(default=0, help_text="Tie-breaker inside a slot; higher wins.")
    valid_from = models.DateTimeField(null=True, blank=True)
    valid_until = models.DateTimeField(null=True, blank=True)
    is_enabled = models.BooleanField(default=True)
    source_note = models.CharField(
        max_length=300, blank=True, help_text="Where the figure comes from (tariff sheet, contract ...)."
    )
    confirmed_by = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    confirmed_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "pricing_rule"
        ordering = ["rule_set", "kind", "code"]
        constraints = [models.UniqueConstraint(fields=["rule_set", "code"], name="prule_set_code_uniq")]
        indexes = [models.Index(fields=["rule_set", "kind", "is_enabled"], name="prule_set_kind_idx")]

    def __str__(self):
        return f"{self.rule_set_id}:{self.code}"

    def save(self, *args, **kwargs):
        self._guard()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        self._guard()
        return super().delete(*args, **kwargs)

    def _guard(self):
        stored = PricingRuleSet.objects.filter(pk=self.rule_set_id).values_list("status", flat=True).first()
        if stored != RuleSetStatus.DRAFT:
            raise ValidationError("Rules of a published rule set are immutable; edit a draft.")

    def clean(self):
        errors = {}
        percent_method = self.calc_method in (CalcMethod.PERCENT_OF_ITEM, CalcMethod.PERCENT_OF_BASE)
        if self.kind == RuleKind.ROUNDING:
            if not self.rounding_mode:
                errors["rounding_mode"] = "Required for a rounding rule."
            if not self.rounding_step_irr or self.rounding_step_irr % 10:
                errors["rounding_step_irr"] = "Required, and a multiple of 10 IRR (1 Toman)."
        elif self.kind == RuleKind.PROFIT_PERCENT:
            if self.amount is not None and self.amount >= 1000:
                errors["amount"] = "Unrealistic percentage."
        else:
            needs_currency = self.kind in (RuleKind.PROFIT_FIXED, RuleKind.MIN_PROFIT) or (
                self.kind in COST_KINDS and not percent_method
            )
            if self.value_status != ValueStatus.UNKNOWN and self.amount is None:
                errors["amount"] = "An amount is required unless the value status is 'unknown'."
            if needs_currency and self.amount is not None and self.currency_id is None:
                errors["currency"] = "Amounts need a currency."
        if self.min_irr is not None and self.max_irr is not None and self.min_irr > self.max_irr:
            errors["max_irr"] = "Must not be below min_irr."
        if self.valid_from and self.valid_until and self.valid_from >= self.valid_until:
            errors["valid_until"] = "Must be after valid_from."
        if self.kind in COST_KINDS and self.value_status == ValueStatus.CONFIRMED and not self.source_note:
            errors["source_note"] = "A confirmed cost needs a source note (tariff sheet, invoice, contract ...)."
        if errors:
            raise ValidationError(errors)


# --------------------------------------------------------------------------------------------------- quotes
class QuoteStatus(models.TextChoices):
    OPEN = "open", "Open"
    LOCKED = "locked", "Locked (accepted by an order)"
    EXPIRED = "expired", "Expired"


class _Immutable(models.Model):
    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise RuntimeError(f"{type(self).__name__} rows are immutable snapshots.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise RuntimeError(f"{type(self).__name__} rows cannot be deleted.")


class PriceQuote(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    user = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    guest_token_hash = models.CharField(max_length=64, blank=True)
    status = models.CharField(max_length=8, choices=QuoteStatus.choices, default=QuoteStatus.OPEN)
    rule_set = models.ForeignKey(PricingRuleSet, on_delete=models.PROTECT, related_name="quotes")
    rule_version = models.PositiveIntegerField()
    display_currency = models.ForeignKey("currencies.Currency", on_delete=models.PROTECT, related_name="+")
    destination_country = models.CharField(max_length=2, blank=True)
    shipping_method = models.CharField(max_length=40, blank=True)
    sales_model = models.CharField(max_length=40, blank=True)
    subtotal_irr = models.DecimalField(max_digits=18, decimal_places=0)
    separate_irr = models.DecimalField(max_digits=18, decimal_places=0, default=Decimal("0"))
    total_irr = models.DecimalField(max_digits=18, decimal_places=0)
    total_display = models.DecimalField(max_digits=24, decimal_places=6)
    display_rate = models.DecimalField(max_digits=24, decimal_places=10, null=True, blank=True)
    display_rate_source = models.CharField(max_length=40, blank=True)
    display_rate_at = models.DateTimeField(null=True, blank=True)
    has_estimated_costs = models.BooleanField(default=False)
    warnings = models.JSONField(default=list, blank=True)
    calculation_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField()
    expired_at = models.DateTimeField(null=True, blank=True)
    locked_at = models.DateTimeField(null=True, blank=True)
    locked_reference = models.CharField(
        max_length=64, null=True, blank=True, help_text="The order that accepted this quote."
    )

    class Meta:
        db_table = "pricing_quote"
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status", "expires_at"], name="pquote_status_exp_idx")]
        constraints = [
            models.UniqueConstraint(
                fields=["locked_reference"],
                condition=models.Q(locked_reference__isnull=False),
                name="pquote_locked_ref_uniq",
            ),
            models.CheckConstraint(
                condition=models.Q(expires_at__gt=models.F("created_at")), name="pquote_expiry_after_creation"
            ),
        ]

    _MUTABLE = {"status", "expired_at", "locked_at", "locked_reference"}

    def save(self, *args, **kwargs):
        if self.pk is not None:
            fields = kwargs.get("update_fields")
            if fields is None or not set(fields) <= self._MUTABLE:
                raise RuntimeError("A price quote is an immutable snapshot (only its status fields may change).")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise RuntimeError("Price quotes cannot be deleted.")

    @property
    def is_expired(self) -> bool:
        return self.status == QuoteStatus.EXPIRED or (
            self.status == QuoteStatus.OPEN and self.expires_at <= timezone.now()
        )


class PriceQuoteItem(_Immutable):
    quote = models.ForeignKey(PriceQuote, on_delete=models.PROTECT, related_name="items")
    variant = models.ForeignKey("catalog.ProductVariant", on_delete=models.PROTECT, related_name="+")
    sku = models.CharField(max_length=80)
    product_name = models.CharField(max_length=255, blank=True)
    quantity = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    original_amount = models.DecimalField(max_digits=18, decimal_places=4)
    original_currency = models.CharField(max_length=3)
    rate_applied = models.DecimalField(max_digits=24, decimal_places=10)
    rate_source = models.CharField(max_length=40)
    rate_kind = models.CharField(max_length=10)
    rate_id = models.BigIntegerField(null=True, blank=True)
    rate_at = models.DateTimeField(null=True, blank=True)
    rate_fetched_at = models.DateTimeField(null=True, blank=True)
    rate_is_stale = models.BooleanField(default=False)
    rate_is_manual = models.BooleanField(default=False)
    item_irr = models.DecimalField(max_digits=18, decimal_places=0)
    profit_method = models.CharField(max_length=8)
    profit_percent = models.DecimalField(max_digits=9, decimal_places=4, null=True, blank=True)
    profit_irr = models.DecimalField(max_digits=18, decimal_places=0)
    realized_markup_percent = models.DecimalField(max_digits=9, decimal_places=4, null=True, blank=True)
    realized_margin_percent = models.DecimalField(max_digits=9, decimal_places=4, null=True, blank=True)
    rounding_adjustment_irr = models.DecimalField(max_digits=18, decimal_places=0, default=Decimal("0"))
    unit_price_irr = models.DecimalField(max_digits=18, decimal_places=0)
    unit_separate_irr = models.DecimalField(max_digits=18, decimal_places=0, default=Decimal("0"))
    unit_internal_cost_irr = models.DecimalField(max_digits=18, decimal_places=0)
    unit_realized_profit_irr = models.DecimalField(max_digits=18, decimal_places=0)
    line_price_irr = models.DecimalField(max_digits=18, decimal_places=0)
    line_separate_irr = models.DecimalField(max_digits=18, decimal_places=0, default=Decimal("0"))
    line_total_irr = models.DecimalField(max_digits=18, decimal_places=0)
    unit_display = models.DecimalField(max_digits=24, decimal_places=6)
    line_display = models.DecimalField(max_digits=24, decimal_places=6)
    has_estimated_costs = models.BooleanField(default=False)
    rule_version = models.PositiveIntegerField()
    breakdown = models.JSONField(default=dict)

    class Meta:
        db_table = "pricing_quote_item"
        constraints = [
            models.CheckConstraint(condition=models.Q(quantity__gt=0), name="pqitem_qty_positive"),
            models.CheckConstraint(
                condition=models.Q(line_price_irr=models.F("unit_price_irr") * models.F("quantity")),
                name="pqitem_line_eq_unit_qty",
            ),
        ]
        indexes = [models.Index(fields=["quote"], name="pqitem_quote_idx")]


class PriceQuoteComponent(_Immutable):
    item = models.ForeignKey(PriceQuoteItem, on_delete=models.PROTECT, related_name="components")
    sort_order = models.PositiveSmallIntegerField()
    kind = models.CharField(max_length=20)  # item | <rule kind> | profit | rounding
    code = models.CharField(max_length=60, blank=True)
    name = models.CharField(max_length=160, blank=True)
    basis_irr = models.DecimalField(max_digits=18, decimal_places=0, null=True, blank=True)
    percent = models.DecimalField(max_digits=9, decimal_places=4, null=True, blank=True)
    amount_irr = models.DecimalField(max_digits=18, decimal_places=0)
    in_price = models.BooleanField(default=True, help_text="False for separate/absorbed lines.")
    charge_mode = models.CharField(max_length=10, blank=True)
    value_status = models.CharField(max_length=10, blank=True)
    rule = models.ForeignKey(PricingRule, null=True, blank=True, on_delete=models.PROTECT, related_name="+")

    class Meta:
        db_table = "pricing_quote_component"
        ordering = ["item", "sort_order"]


# --------------------------------------------------------------------------------------------------- history
class PriceOutcome(models.TextChoices):
    APPLIED = "applied", "Price written"
    BLOCKED = "blocked", "Not priced (blockers)"


class PriceHistory(_Immutable):
    """Every change of a variant's engine price, and every time pricing was blocked (old price left untouched)."""

    variant = models.ForeignKey("catalog.ProductVariant", on_delete=models.PROTECT, related_name="price_history")
    sequence = models.PositiveIntegerField()
    outcome = models.CharField(max_length=8, choices=PriceOutcome.choices)
    cause = models.CharField(max_length=20, choices=PriceCause.choices)
    old_final_irr = models.DecimalField(max_digits=18, decimal_places=0, null=True, blank=True)
    new_final_irr = models.DecimalField(max_digits=18, decimal_places=0, null=True, blank=True)
    old_list_irr = models.DecimalField(max_digits=18, decimal_places=0, null=True, blank=True)
    new_list_irr = models.DecimalField(max_digits=18, decimal_places=0, null=True, blank=True)
    rule_version = models.PositiveIntegerField(null=True, blank=True)
    rate_id = models.BigIntegerField(null=True, blank=True)
    rate_at = models.DateTimeField(null=True, blank=True)
    blockers = models.JSONField(default=list, blank=True)
    calculation = models.JSONField(default=dict, blank=True)
    input_hash = models.CharField(max_length=64, blank=True)
    actor = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "pricing_history"
        ordering = ["-created_at", "-id"]
        constraints = [models.UniqueConstraint(fields=["variant", "sequence"], name="phistory_variant_seq_uniq")]
        indexes = [models.Index(fields=["variant", "-created_at"], name="phistory_variant_at_idx")]
