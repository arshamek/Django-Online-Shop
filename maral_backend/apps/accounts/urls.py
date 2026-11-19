"""Routes for /api/v1/ (no trailing slashes, kebab-case: see API_CONVENTIONS section 2)."""

from django.urls import path

from .views import auth, me, staff

urlpatterns = [
    # Authentication
    path("auth/csrf", auth.CSRFView.as_view(), name="auth-csrf"),
    path("auth/register", auth.RegisterView.as_view(), name="auth-register"),
    path("auth/login", auth.LoginView.as_view(), name="auth-login"),
    path("auth/logout", auth.LogoutView.as_view(), name="auth-logout"),
    path("auth/refresh", auth.RefreshView.as_view(), name="auth-refresh"),
    path("auth/password/change", auth.PasswordChangeView.as_view(), name="auth-password-change"),
    path("auth/password-reset/request", auth.PasswordResetRequestView.as_view(), name="auth-password-reset-request"),
    path("auth/password-reset/confirm", auth.PasswordResetConfirmView.as_view(), name="auth-password-reset-confirm"),
    path(
        "auth/email/verification/request", auth.EmailVerificationRequestView.as_view(), name="auth-email-verify-request"
    ),
    path(
        "auth/email/verification/confirm", auth.EmailVerificationConfirmView.as_view(), name="auth-email-verify-confirm"
    ),
    path(
        "auth/phone/verification/request", auth.PhoneVerificationRequestView.as_view(), name="auth-phone-verify-request"
    ),
    path(
        "auth/phone/verification/confirm", auth.PhoneVerificationConfirmView.as_view(), name="auth-phone-verify-confirm"
    ),
    path("auth/sessions", auth.SessionListView.as_view(), name="auth-sessions"),
    path("auth/sessions/revoke-others", auth.SessionRevokeOthersView.as_view(), name="auth-sessions-revoke-others"),
    path("auth/sessions/<uuid:id>", auth.SessionRevokeView.as_view(), name="auth-session-revoke"),
    # Customer profile
    path("me", me.MeView.as_view(), name="me"),
    path("me/email", me.MeEmailView.as_view(), name="me-email"),
    path("me/phone", me.MePhoneView.as_view(), name="me-phone"),
    path("me/avatar", me.MeAvatarView.as_view(), name="me-avatar"),
    path("me/addresses", me.AddressListCreateView.as_view(), name="me-addresses"),
    path("me/addresses/<uuid:id>", me.AddressDetailView.as_view(), name="me-address"),
    # Staff (permission gated, audited)
    path("staff/customers", staff.StaffCustomerListView.as_view(), name="staff-customers"),
    path("staff/customers/<uuid:id>", staff.StaffCustomerDetailView.as_view(), name="staff-customer"),
    path("staff/customers/<uuid:id>/suspend", staff.StaffCustomerSuspendView.as_view(), name="staff-customer-suspend"),
    path(
        "staff/customers/<uuid:id>/reactivate",
        staff.StaffCustomerReactivateView.as_view(),
        name="staff-customer-reactivate",
    ),
    path("staff/customers/<uuid:id>/unlock", staff.StaffCustomerUnlockView.as_view(), name="staff-customer-unlock"),
    path(
        "staff/customers/<uuid:id>/revoke-sessions",
        staff.StaffCustomerRevokeSessionsView.as_view(),
        name="staff-customer-revoke-sessions",
    ),
    path("staff/audit-logs", staff.StaffAuditLogListView.as_view(), name="staff-audit-logs"),
]
