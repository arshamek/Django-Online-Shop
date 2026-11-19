from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import generics, status
from rest_framework.parsers import JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle

from apps.common.api.serializers import ErrorEnvelopeSerializer

from .. import serializers as s
from ..models import Address
from ..permissions import IsOwner
from ..services import addresses as address_service
from ..services import credentials, profile
from .base import NoStoreMixin, PrivateView

TAG = ["me"]


class MeView(PrivateView):
    @extend_schema(tags=TAG, operation_id="me_retrieve", responses=s.UserSerializer)
    def get(self, request):
        """The signed-in customer: identity, verification flags, profile and preferences."""
        return Response(s.UserSerializer(request.user, context={"request": request}).data)

    @extend_schema(tags=TAG, operation_id="me_update", request=s.ProfileUpdateSerializer, responses=s.UserSerializer)
    def patch(self, request):
        """Update name, birth date, language and currency. Email/phone have their own endpoints."""
        data = s.ProfileUpdateSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        profile.update_profile(request._request, request.user, data.validated_data)
        request.user.refresh_from_db()
        return Response(s.UserSerializer(request.user, context={"request": request}).data)


class MeEmailView(PrivateView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth_login"

    @extend_schema(
        tags=TAG,
        operation_id="me_email_change",
        request=s.EmailChangeSerializer,
        responses={200: s.UserSerializer, 409: ErrorEnvelopeSerializer},
    )
    def put(self, request):
        """Change the email (needs the current password). Resets email verification and sends a new link."""
        data = s.EmailChangeSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        user = credentials.change_email(request._request, request.user, **data.validated_data)
        return Response(s.UserSerializer(user, context={"request": request}).data)


class MePhoneView(PrivateView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth_login"

    @extend_schema(
        tags=TAG,
        operation_id="me_phone_change",
        request=s.PhoneChangeSerializer,
        responses={200: s.UserSerializer, 409: ErrorEnvelopeSerializer},
    )
    def put(self, request):
        """Change the phone number (needs the current password). Resets phone verification."""
        data = s.PhoneChangeSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        user = credentials.change_phone(request._request, request.user, **data.validated_data)
        return Response(s.UserSerializer(user, context={"request": request}).data)


class MeAvatarView(PrivateView):
    parser_classes = [MultiPartParser, JSONParser]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "avatar_upload"

    @extend_schema(
        tags=TAG,
        operation_id="me_avatar_upload",
        request={"multipart/form-data": s.AvatarUploadSerializer},
        responses=s.UserSerializer,
    )
    def put(self, request):
        """Upload a profile image (JPEG/PNG/WebP, max 2 MB). It is re-encoded to a 512x512 WebP; metadata is stripped."""
        data = s.AvatarUploadSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        profile.set_avatar(request._request, request.user, data.validated_data["avatar"])
        return Response(s.UserSerializer(request.user, context={"request": request}).data)

    @extend_schema(tags=TAG, operation_id="me_avatar_delete", responses={204: OpenApiResponse(description="Removed.")})
    def delete(self, request):
        profile.remove_avatar(request._request, request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)


class AddressListCreateView(NoStoreMixin, generics.ListCreateAPIView):
    """Addresses of the signed-in customer only. An empty list is `{count: 0, results: []}`."""

    serializer_class = s.AddressSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Address.objects.none()
        return Address.objects.filter(user=self.request.user)

    @extend_schema(tags=TAG, operation_id="me_addresses_list")
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    @extend_schema(
        tags=TAG, operation_id="me_addresses_create", responses={201: s.AddressSerializer, 422: ErrorEnvelopeSerializer}
    )
    def post(self, request, *args, **kwargs):
        """Add an address. The first one becomes the default; `is_default: true` moves the default. Max 20."""
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        address = address_service.create_address(request.user, dict(serializer.validated_data))
        return Response(
            self.get_serializer(address).data,
            status=status.HTTP_201_CREATED,
            headers={"Location": f"/api/v1/me/addresses/{address.public_id}"},
        )


class AddressDetailView(NoStoreMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = s.AddressSerializer
    permission_classes = [IsAuthenticated, IsOwner]
    lookup_field = "public_id"
    lookup_url_kwarg = "id"
    http_method_names = ["get", "patch", "delete", "head", "options"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Address.objects.none()
        # Other customers' addresses are simply not found (404, never 403): no existence leak.
        return Address.objects.filter(user=self.request.user).select_related("user")

    @extend_schema(tags=TAG, operation_id="me_addresses_retrieve")
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    @extend_schema(tags=TAG, operation_id="me_addresses_update")
    def patch(self, request, *args, **kwargs):
        """Partial update. `is_default: true` makes this the default address (the previous default is unset)."""
        address = self.get_object()
        serializer = self.get_serializer(address, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        if data.get("is_default") is False and address.is_default:
            from rest_framework.exceptions import ValidationError

            raise ValidationError({"is_default": ["Choose another address as the default instead."]})
        address = address_service.update_address(address, data)
        return Response(self.get_serializer(address).data)

    @extend_schema(
        tags=TAG,
        operation_id="me_addresses_delete",
        responses={
            204: OpenApiResponse(
                description="Deleted. If it was the default, the newest remaining address becomes the default."
            )
        },
    )
    def delete(self, request, *args, **kwargs):
        address_service.delete_address(self.get_object())
        return Response(status=status.HTTP_204_NO_CONTENT)
