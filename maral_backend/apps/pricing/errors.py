from apps.common.exceptions import BusinessRuleViolation


class PriceUnavailable(BusinessRuleViolation):
    default_detail = "A price cannot be given for this item right now."
    default_code = "price_unavailable"

    def __init__(self, detail=None, code=None, blockers=None):
        super().__init__(detail=detail, code=code)
        self.blockers = list(blockers or [])
