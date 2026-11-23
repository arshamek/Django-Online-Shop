class RateError(Exception):
    """A rate cannot be provided. `code` is stable and machine-readable."""

    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code
        self.message = message or code


class ProviderError(Exception):
    def __init__(self, code: str, message: str = "", transient: bool = True):
        super().__init__(message or code)
        self.code, self.message, self.transient = code, message or code, transient
