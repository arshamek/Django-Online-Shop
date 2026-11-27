"""Shared vocabulary of the Phase 9 assistant (provider status, availability, price status)."""


class ProviderStatus:
    AVAILABLE = "AVAILABLE"
    NOT_CONFIGURED = "NOT_CONFIGURED"
    AUTHORIZATION_REQUIRED = "AUTHORIZATION_REQUIRED"
    TEMPORARILY_UNAVAILABLE = "TEMPORARILY_UNAVAILABLE"
    RATE_LIMITED = "RATE_LIMITED"
    UNSUPPORTED = "UNSUPPORTED"
    ERROR = "ERROR"

    ALL = (
        AVAILABLE,
        NOT_CONFIGURED,
        AUTHORIZATION_REQUIRED,
        TEMPORARILY_UNAVAILABLE,
        RATE_LIMITED,
        UNSUPPORTED,
        ERROR,
    )


class Availability:
    IN_STOCK = "IN_STOCK"
    OUT_OF_STOCK = "OUT_OF_STOCK"
    UNKNOWN = "UNKNOWN"
    NOT_VERIFIED = "NOT_VERIFIED"


class PriceStatus:
    LISTED = "listed"  # Maral's published catalogue price (still not a confirmed order price)
    ESTIMATED = "estimated"  # Phase 6 engine ran with all configured costs
    PROVISIONAL = "provisional"  # engine ran but some costs are unknown / the source price is stale
    INCOMPLETE = "incomplete"  # blocked: no usable price can be shown
    UNAVAILABLE = "unavailable"  # nothing to price from


SOURCE_MARAL = "maral"
EXTERNAL_SOURCES = ("trendyol", "namshi")
ALL_SOURCES = (SOURCE_MARAL, *EXTERNAL_SOURCES)
SUPPORTED_LANGUAGES = ("fa", "en", "ar", "tr")
