"""Customer-facing assistant API. Every endpoint needs an authenticated customer; conversations are owner-only."""

from django.db.models import Count
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.accounts.views.base import NoStoreMixin
from apps.audit import services as audit

from ..models import Conversation, PurchaseInquiry
from ..serializers import (
    ChatRequestSerializer,
    CompareRequestSerializer,
    ConversationListSerializer,
    InquiryCreateSerializer,
    InquirySerializer,
    MessageSerializer,
    RecommendationRequestSerializer,
)
from ..services import chat, inquiries, stateless

TAG = ["ai-assistant"]


class AIView(NoStoreMixin, APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "ai_read"


def _clean(data: dict) -> dict:
    """Serializer output -> plain dict (UUIDs as strings, nested budgets as dicts)."""
    out = dict(data)
    if "conversation_id" in out:
        out["conversation_id"] = str(out["conversation_id"])
    if "product_references" in out:
        out["product_references"] = [{"source": r["source"], "id": str(r["id"])} for r in out["product_references"]]
    if out.get("language") == "auto":
        out.pop("language")
    return out


class ChatView(AIView):
    throttle_scope = "ai_chat"

    @extend_schema(tags=TAG, request=ChatRequestSerializer, summary="Send a message to the shopping assistant")
    def post(self, request):
        ser = ChatRequestSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        return Response(chat.handle_turn(request, request.user, _clean(ser.validated_data)))


class ConversationListView(AIView):
    @extend_schema(tags=TAG, responses=ConversationListSerializer(many=True), summary="List my conversations")
    def get(self, request):
        qs = (
            Conversation.objects.filter(user=request.user)
            .annotate(message_count=Count("messages"))
            .order_by("-updated_at")[:50]
        )
        return Response({"results": ConversationListSerializer(qs, many=True).data})


class ConversationDetailView(AIView):
    @extend_schema(tags=TAG, summary="One of my conversations with its messages")
    def get(self, request, public_id):
        conv = chat.get_conversation(request.user, public_id)
        return Response(
            {
                **ConversationListSerializer(
                    Conversation.objects.filter(pk=conv.pk).annotate(message_count=Count("messages")).first()
                ).data,
                "messages": MessageSerializer(conv.messages.all(), many=True).data,
            }
        )

    @extend_schema(tags=TAG, summary="Delete one of my conversations (permanent)")
    def delete(self, request, public_id):
        conv = chat.get_conversation(request.user, public_id)
        audit.record("ai.conversation_deleted", request=request, actor=request.user, target=conv)
        conv.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class RecommendationsView(AIView):
    throttle_scope = "ai_chat"

    @extend_schema(tags=TAG, request=RecommendationRequestSerializer, summary="Stateless product recommendations")
    def post(self, request):
        ser = RecommendationRequestSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        return Response(stateless.recommendations(request, request.user, _clean(ser.validated_data)))


class CompareView(AIView):
    throttle_scope = "ai_chat"

    @extend_schema(
        tags=TAG, request=CompareRequestSerializer, summary="Compare 2-4 products (verified attributes only)"
    )
    def post(self, request):
        ser = CompareRequestSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        return Response(stateless.compare(request, request.user, _clean(ser.validated_data)))


class InquiryListView(AIView):
    @property
    def throttle_scope(self):
        # DRF resolves the scope in `initial()` (before the handler runs), so it must be decided here from the
        # method: creating an inquiry is the abusable action and gets its own, stricter budget.
        return "ai_inquiry" if self.request.method == "POST" else "ai_read"

    @extend_schema(tags=TAG, responses=InquirySerializer(many=True), summary="My purchase inquiries")
    def get(self, request):
        qs = PurchaseInquiry.objects.filter(user=request.user).select_related("source")[:100]
        return Response({"results": InquirySerializer(qs, many=True).data})

    @extend_schema(
        tags=TAG,
        request=InquiryCreateSerializer,
        responses=InquirySerializer,
        summary="Create a purchase inquiry (NOT an order)",
    )
    def post(self, request):
        ser = InquiryCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        conversation = None
        if d.get("conversation_id"):
            conversation = Conversation.objects.filter(user=request.user, public_id=d["conversation_id"]).first()
        inquiry = inquiries.create(
            request,
            request.user,
            ref={"source": d["product_ref"]["source"], "id": str(d["product_ref"]["id"])},
            size=d["size"],
            color=d["color"],
            quantity=d["quantity"],
            note=d["note"],
            conversation=conversation,
        )
        return Response(InquirySerializer(inquiry).data, status=status.HTTP_201_CREATED)


class InquiryDetailView(AIView):
    def _get(self, request, public_id):
        inquiry = (
            PurchaseInquiry.objects.filter(user=request.user, public_id=public_id).select_related("source").first()
        )
        if inquiry is None:
            raise NotFound()
        return inquiry

    @extend_schema(tags=TAG, responses=InquirySerializer, summary="One of my purchase inquiries")
    def get(self, request, public_id):
        return Response(InquirySerializer(self._get(request, public_id)).data)

    @extend_schema(tags=TAG, responses=InquirySerializer, summary="Cancel my purchase inquiry")
    def delete(self, request, public_id):
        return Response(InquirySerializer(inquiries.cancel(request, request.user, self._get(request, public_id))).data)
