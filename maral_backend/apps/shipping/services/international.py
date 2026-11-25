"""
International order information: customs / duties / taxes and localized policy text.

Statuses (customer-facing, never blurred):
  estimated            an amount from configured pricing rules that are themselves estimates
  confirmed            an amount from rules marked confirmed
  payable_on_delivery  duties/taxes are collected by the carrier or customs; the amount is NOT known and NOT in our total
  not_applicable       no customs for this destination (domestic delivery, or the country policy says so)
  unknown              nothing configured: we say so, we do not show zero
Amounts exist only for the home country (import customs from the pricing rules); for any other destination duties are
never calculated here, so the status is `payable_on_delivery`, `not_applicable` or `unknown`, never an invented number.
Delivery times are ranges only when configured, otherwise `unknown`; exact dates exist only when a carrier supplied one.
"""

from decimal import Decimal

from ..models import CustomsMode
from . import methods as shipping_methods

HOME_COUNTRY = "IR"


def customs_info(
    country: str, *, estimated_irr: Decimal = Decimal(0), confirmed_irr: Decimal = Decimal(0), lang: str = "fa"
) -> dict:
    country = (country or "").upper()
    policy = shipping_methods.policy_for(country)
    mode = policy.customs_mode if policy else CustomsMode.UNKNOWN
    notice = policy.text("customs_notice", lang) if policy else ""
    taxes = policy.text("taxes_notice", lang) if policy else ""
    if country != HOME_COUNTRY:
        # The quote's customs components are the cost of importing goods INTO Iran. They say nothing about a foreign
        # destination's duties, so they are never shown as such: a foreign amount needs a destination duty source.
        estimated_irr = confirmed_irr = Decimal(0)
    amount = estimated_irr + confirmed_irr
    if mode == CustomsMode.ON_DELIVERY:
        status, basis, amount_value = "payable_on_delivery", "country_policy", None
    elif amount > 0:
        status, basis, amount_value = ("estimated" if estimated_irr > 0 else "confirmed"), "pricing_rules", amount
    elif mode == CustomsMode.NOT_APPLICABLE or (country == HOME_COUNTRY and mode == CustomsMode.UNKNOWN):
        status, basis, amount_value = "not_applicable", ("country_policy" if policy else "domestic_delivery"), None
    else:
        status, basis, amount_value = "unknown", "none_configured", None  # prepaid-without-rule or nothing configured
    return {
        "status": status, "basis": basis, "amount_irr": None if amount_value is None else str(amount_value),
        "is_estimate": status == "estimated", "notice": notice, "taxes_notice": taxes,
        "taxes_status": "see_notice" if taxes else "not_modeled",
    }  # fmt: skip


def customs_warnings(info: dict) -> list[str]:
    return {"unknown": ["customs_unknown"], "payable_on_delivery": ["customs_payable_on_delivery"]}.get(
        info["status"], []
    )


def policy_notes(country: str, lang: str) -> str:
    policy = shipping_methods.policy_for(country)
    return policy.text("policy_notes", lang) if policy else ""
