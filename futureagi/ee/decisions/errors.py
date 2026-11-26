"""Body-free errors: never expose upstream text, credentials or input data."""

from ee.jev.client import JevGatewayError
from ee.jev.mapping import JevMappingError
from ee.jev.validation import JevResponseError
from evaluations.decision_models import CONNECTION_LABELS


class DecisionGatewayError(JevGatewayError):
    def __init__(self, provider, cause):
        self.provider = provider
        super().__init__(
            cause.code.replace("JEV_", "DECISION_"), cause.retryable, cause.retry_after
        )


class DecisionMappingError(JevMappingError):
    pass


class DecisionResponseError(JevResponseError):
    pass


def mapping_error(error):
    return DecisionMappingError(
        error.code.replace("JEV_", "DECISION_"),
        error.field,
        error.detail.replace("Jev", "This decision model"),
        {
            key: [value.replace("JEV_", "DECISION_") for value in values]
            for key, values in error.details.items()
        },
    )


def describe(exc):
    seen = set()
    while isinstance(exc, BaseException) and id(exc) not in seen:
        if isinstance(exc, DecisionMappingError):
            return {
                "code": exc.code,
                "message": exc.detail,
                "attr": exc.field,
                "details": exc.details,
            }
        if isinstance(exc, DecisionGatewayError):
            provider = CONNECTION_LABELS.get(exc.provider, "The selected provider")
            message = {
                "DECISION_KEY_INVALID": f"{provider} rejected the request with the saved API key. Check the key and its model permissions in Settings → AI Providers.",
                "DECISION_KEY_MISSING": f"Add your {provider} API key in Settings → AI Providers.",
                "DECISION_RATE_LIMITED": f"{provider}'s rate limit was reached. Try again shortly.",
                "DECISION_TIMEOUT": f"{provider} didn't respond in time. Try again.",
            }.get(exc.code, f"{provider} couldn't complete this decision evaluation.")
            return {"code": exc.code, "message": message, "attr": None, "details": None}
        if isinstance(exc, DecisionResponseError):
            message = {
                "DECISION_REFUSED": "The model declined to answer this question. No score was recorded.",
                "DECISION_MODEL_MISMATCH": "The provider returned a different model than the selected decision model. No score was recorded.",
            }.get(
                exc.code,
                "The model returned an invalid decision answer. No score was recorded.",
            )
            return {"code": exc.code, "message": message, "attr": None, "details": None}
        seen.add(id(exc))
        wrapped = exc.args[0] if exc.args else None
        exc = wrapped if isinstance(wrapped, BaseException) else exc.__cause__
    return None
