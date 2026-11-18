from rest_framework.authentication import SessionAuthentication


class SessionAuthentication401(SessionAuthentication):
    """
    Session-cookie auth that answers 401 (not 403) for unauthenticated requests, as the
    API conventions require. DRF only emits 401 when the authenticator advertises a
    WWW-Authenticate challenge; "Session" is a non-browser-prompting scheme name.
    """

    def authenticate_header(self, request):
        return 'Session realm="api"'
