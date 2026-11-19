from django.middleware.csrf import get_token
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.common.api.serializers import ErrorEnvelopeSerializer

from .. import serializers as s
from ..models import SessionEnd
from ..services import auth as auth_service
from ..services import credentials, sessions, verification
from .base import NoStoreMixin, PrivateView, PublicAuthView

TAG = ["auth"]
NO_CONTENT = OpenApiResponse(description="Success, no body.")
ERR = {409: ErrorEnvelopeSerializer, 422: ErrorEnvelopeSerializer}


class CSRFView(NoStoreMixin, APIView):
    permission_classes = [AllowAny]

    @extend_schema(tags=TAG, operation_id="auth_csrf", responses=s.CSRFSerializer)
    def get(self, request):
        """Issue the CSRF cookie and return the token. Call once on app start; send it as `X-CSRFToken`."""
        return Response({"csrf_token": get_token(request._request)})


class RegisterView(PublicAuthView):
    throttle_scope = "auth_register"

    @extend_schema(
        tags=TAG,
        operation_id="auth_register",
        request=s.RegisterSerializer,
        responses={201: s.AuthResponseSerializer, **ERR},
    )
    def post(self, request):
        """Create an account and sign in. Email verification is sent asynchronously when an email is given."""
        data = s.RegisterSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        user = auth_service.register(request, **data.validated_data)
        csrf_token = auth_service.login(request._request, user)
        body = {"user": s.UserSerializer(user, context={"request": request}).data, "csrf_token": csrf_token}
        return Response(body, status=status.HTTP_201_CREATED, headers={"Location": "/api/v1/me"})


class LoginView(PublicAuthView):
    throttle_scope = "auth_login"

    @extend_schema(
        tags=TAG, operation_id="auth_login", request=s.LoginSerializer, responses={200: s.AuthResponseSerializer}
    )
    def post(self, request):
        """Sign in with email-or-phone + password. Sets the HttpOnly session cookie.

        Errors: `invalid_credentials` (401), `account_suspended` / `account_deactivated` (403),
        `rate_limited` (429 + Retry-After) after repeated failures."""
        data = s.LoginSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        user = auth_service.authenticate_credentials(request, **data.validated_data)
        csrf_token = auth_service.login(request._request, user)
        return Response({"user": s.UserSerializer(user, context={"request": request}).data, "csrf_token": csrf_token})


class LogoutView(PrivateView):
    @extend_schema(tags=TAG, operation_id="auth_logout", request=None, responses={204: NO_CONTENT})
    def post(self, request):
        """End the current session (server-side revocation + cookie cleared)."""
        auth_service.logout(request._request, request.auth)
        return Response(status=status.HTTP_204_NO_CONTENT)


class RefreshView(PrivateView):
    @extend_schema(tags=TAG, operation_id="auth_refresh", request=None, responses=s.RefreshResponseSerializer)
    def post(self, request):
        """Renew the session: rotates the session key and CSRF token, extends idle expiry (bounded by the 30-day cap)."""
        csrf_token, expires_at = auth_service.refresh(request, request.auth)
        return Response({"csrf_token": csrf_token, "expires_at": expires_at})


class PasswordChangeView(PrivateView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth_login"

    @extend_schema(
        tags=TAG, operation_id="auth_password_change", request=s.PasswordChangeSerializer, responses={204: NO_CONTENT}
    )
    def post(self, request):
        """Change the password. All OTHER sessions are revoked; this one stays signed in."""
        data = s.PasswordChangeSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        credentials.change_password(request._request, request.user, request.auth, **data.validated_data)
        return Response(status=status.HTTP_204_NO_CONTENT)


class PasswordResetRequestView(PublicAuthView):
    throttle_scope = "auth_password_reset"

    @extend_schema(
        tags=TAG,
        operation_id="auth_password_reset_request",
        request=s.PasswordResetRequestSerializer,
        responses={202: OpenApiResponse(description="Always 202, whether or not the account exists.")},
    )
    def post(self, request):
        """Email a single-use reset link (valid 1 hour). The response never reveals whether the account exists."""
        data = s.PasswordResetRequestSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        credentials.request_password_reset(request._request, data.validated_data["identifier"])
        return Response(status=status.HTTP_202_ACCEPTED)


class PasswordResetConfirmView(PublicAuthView):
    throttle_scope = "auth_password_reset"

    @extend_schema(
        tags=TAG,
        operation_id="auth_password_reset_confirm",
        request=s.PasswordResetConfirmSerializer,
        responses={204: NO_CONTENT},
    )
    def post(self, request):
        """Set a new password with the emailed `uid` + `token`. Revokes every session of the account."""
        data = s.PasswordResetConfirmSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        credentials.confirm_password_reset(request._request, **data.validated_data)
        return Response(status=status.HTTP_204_NO_CONTENT)


class EmailVerificationRequestView(PrivateView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth_verify"

    @extend_schema(
        tags=TAG, operation_id="auth_email_verification_request", request=None, responses={202: NO_CONTENT, **ERR}
    )
    def post(self, request):
        """(Re)send the verification email. One request per minute."""
        verification.request_email_verification(request._request, request.user)
        return Response(status=status.HTTP_202_ACCEPTED)


class EmailVerificationConfirmView(PublicAuthView):
    throttle_scope = "auth_verify"

    @extend_schema(
        tags=TAG, operation_id="auth_email_verification_confirm", request=s.TokenSerializer, responses={204: NO_CONTENT}
    )
    def post(self, request):
        """Confirm the email with the token from the link. Works without being signed in."""
        data = s.TokenSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        verification.confirm_email(request._request, data.validated_data["token"])
        return Response(status=status.HTTP_204_NO_CONTENT)


class PhoneVerificationRequestView(PrivateView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "otp_ip"

    @extend_schema(
        tags=TAG,
        operation_id="auth_phone_verification_request",
        request=None,
        responses={202: NO_CONTENT, 503: ErrorEnvelopeSerializer, **ERR},
    )
    def post(self, request):
        """Send a 6-digit code by SMS (3 per 10 minutes per number). 503 `sms_unavailable` when no SMS provider is configured."""
        verification.request_phone_code(request._request, request.user)
        return Response(status=status.HTTP_202_ACCEPTED)


class PhoneVerificationConfirmView(PrivateView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth_verify"

    @extend_schema(
        tags=TAG, operation_id="auth_phone_verification_confirm", request=s.CodeSerializer, responses={204: NO_CONTENT}
    )
    def post(self, request):
        """Confirm the SMS code. Errors: `otp_invalid`, `otp_expired` (400). Max 5 attempts per code."""
        data = s.CodeSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        verification.confirm_phone_code(request._request, request.user, data.validated_data["code"])
        return Response(status=status.HTTP_204_NO_CONTENT)


class SessionListView(PrivateView):
    @extend_schema(tags=TAG, operation_id="auth_sessions_list", responses=s.SessionSerializer(many=True))
    def get(self, request):
        """Signed-in devices of the current user (newest activity first)."""
        qs = sessions.active_sessions(request.user)
        data = s.SessionSerializer(qs, many=True, context={"current_session": request.auth}).data
        return Response({"count": len(data), "results": data})


class SessionRevokeView(PrivateView):
    @extend_schema(
        tags=TAG,
        operation_id="auth_sessions_revoke",
        request=None,
        responses={204: NO_CONTENT, 404: ErrorEnvelopeSerializer},
    )
    def delete(self, request, id):
        """Revoke one of your sessions. Revoking the current one signs you out."""
        if not sessions.revoke_one(request._request, request.user, id, current=request.auth):
            raise NotFound()
        return Response(status=status.HTTP_204_NO_CONTENT)


class SessionRevokeOthersView(PrivateView):
    @extend_schema(tags=TAG, operation_id="auth_sessions_revoke_others", request=None, responses={204: NO_CONTENT})
    def post(self, request):
        """Sign out everywhere except this device."""
        sessions.revoke_user_sessions(request.user, SessionEnd.USER, keep=request.auth)
        from apps.audit import services as audit

        audit.record("auth.session.revoked_others", request=request._request, target=request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)
