"""
Provider error taxonomy. `retryable` is the only thing the job runner looks at: retryable errors are
retried later with back-off (never sooner than `retry_after`), everything else fails fast so we never
hammer a source that has told us "no".
"""


class ProviderError(Exception):
    code = "provider_error"
    retryable = False

    def __init__(self, message: str = "", *, code: str | None = None, retry_after: float | None = None):
        super().__init__(message or self.code)
        if code:
            self.code = code
        self.retry_after = retry_after


class ProviderNotAuthorized(ProviderError):
    """No authorisation record, or the source answered 401/403. Never retried automatically."""

    code = "not_authorized"


class ProviderNotConfigured(ProviderError):
    code = "not_configured"


class UnsupportedOperation(ProviderError):
    code = "unsupported_operation"


class ProviderNotFound(ProviderError):
    code = "not_found"


class InvalidProviderResponse(ProviderError):
    """The payload is not what the provider contract says it should be."""

    code = "invalid_response"


class ProviderRateLimited(ProviderError):
    code = "rate_limited"
    retryable = True

    def __init__(self, message: str = "", *, retry_after: float | None = 60, code: str | None = None):
        super().__init__(message, retry_after=retry_after, code=code)


class ProviderTimeout(ProviderError):
    code = "timeout"
    retryable = True


class ProviderUnavailable(ProviderError):
    code = "unavailable"
    retryable = True
