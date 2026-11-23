class TranslationProviderError(Exception):
    def __init__(self, code: str, message: str = "", transient: bool = True):
        super().__init__(message or code)
        self.code, self.message, self.transient = code, message or code, transient


class TranslationValidationError(Exception):
    """The provider's output broke a protection rule (lost/duplicated token, changed number)."""

    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code, self.message = code, message or code
