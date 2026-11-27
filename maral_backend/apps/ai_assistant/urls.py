from django.urls import re_path

from .views import public, staff


# Trailing slash optional, like the catalogue routes: the Phase 9 spec writes /api/v1/ai/chat/ and the rest of the
# API is slash-less; both work.
def r(pattern, view, name):
    return re_path(rf"^ai/{pattern}/?$", view.as_view(), name=name)


uuid = r"(?P<public_id>[0-9a-fA-F-]{36})"
urlpatterns = [
    r("chat", public.ChatView, "ai-chat"),
    r("conversations", public.ConversationListView, "ai-conversations"),
    r(f"conversations/{uuid}", public.ConversationDetailView, "ai-conversation-detail"),
    r("recommendations", public.RecommendationsView, "ai-recommendations"),
    r("compare", public.CompareView, "ai-compare"),
    r("purchase-inquiries", public.InquiryListView, "ai-inquiries"),
    r(f"purchase-inquiries/{uuid}", public.InquiryDetailView, "ai-inquiry-detail"),
    re_path(r"^staff/ai/config/?$", staff.ConfigView.as_view(), name="staff-ai-config"),
    re_path(r"^staff/ai/status/?$", staff.StatusView.as_view(), name="staff-ai-status"),
    re_path(r"^staff/ai/provider-test/?$", staff.ProviderTestView.as_view(), name="staff-ai-provider-test"),
    re_path(r"^staff/ai/purchase-inquiries/?$", staff.InquiryListView.as_view(), name="staff-ai-inquiries"),
    re_path(
        rf"^staff/ai/purchase-inquiries/{uuid}/?$", staff.InquiryDetailView.as_view(), name="staff-ai-inquiry-detail"
    ),
]
