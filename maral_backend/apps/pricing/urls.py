from django.urls import path

from .views import public, staff

urlpatterns = [
    path("pricing/calculate", public.CalculateView.as_view(), name="pricing-calculate"),
    path("pricing/products/<uuid:id>", public.ProductPriceView.as_view(), name="pricing-product"),
    path("pricing/quotes", public.QuoteCreateView.as_view(), name="pricing-quote-create"),
    path("pricing/quotes/<uuid:id>", public.QuoteDetailView.as_view(), name="pricing-quote"),
    path("pricing/quotes/<uuid:id>/expire", public.QuoteExpireView.as_view(), name="pricing-quote-expire"),
    path("staff/pricing/readiness", staff.ReadinessView.as_view(), name="staff-pricing-readiness"),
    path("staff/pricing/rulesets", staff.RuleSetListView.as_view(), name="staff-rulesets"),
    path("staff/pricing/rulesets/<int:ver>", staff.RuleSetDetailView.as_view(), name="staff-ruleset"),
    path("staff/pricing/rulesets/<int:ver>/publish", staff.RuleSetPublishView.as_view(), name="staff-ruleset-publish"),
    path("staff/pricing/rulesets/<int:ver>/rules", staff.RuleListView.as_view(), name="staff-rules"),
    path("staff/pricing/rulesets/<int:ver>/rules/<slug:code>", staff.RuleDetailView.as_view(), name="staff-rule"),
    path("staff/pricing/preview", staff.PreviewView.as_view(), name="staff-pricing-preview"),
    path("staff/pricing/reprice", staff.RepriceView.as_view(), name="staff-pricing-reprice"),
    path("staff/pricing/history", staff.HistoryView.as_view(), name="staff-pricing-history"),
    path("staff/pricing/quotes", staff.QuoteListView.as_view(), name="staff-quotes"),
    path("staff/pricing/quotes/<uuid:id>", staff.QuoteDetailView.as_view(), name="staff-quote"),
    path("staff/pricing/quotes/<uuid:id>/expire", staff.QuoteExpireView.as_view(), name="staff-quote-expire"),
]
