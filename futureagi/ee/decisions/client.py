"""Explicit BYOK decision transports on the existing internal gateway.

Reuse its bounded retry/deadline/header/error policy. Unlike native Jev, these
routes never honor JEV_TRANSPORT=direct or borrow an operator/provider key.
"""

import json
import time
from collections.abc import Callable

from ee.decisions.errors import (
    DecisionGatewayError,
    DecisionMappingError,
    DecisionResponseError,
)
from ee.decisions.openai import normalize_response, request_body
from ee.jev.client import JevGatewayError, JevSystemOneClient
from evaluations.decision_models import get_decision_model
from evaluations.types import (
    DecisionQuestions,
    DecisionRequest,
    JSONValue,
    OpenAIDecisionRequest,
)


class DecisionClient(JevSystemOneClient):
    def __init__(
        self,
        *,
        model: str,
        api_key: str | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        jitter: Callable[[float], float] | None = None,
    ) -> None:
        self.spec = get_decision_model(model)
        if self.spec is None or not model.startswith("decision/"):
            raise DecisionMappingError(
                "DECISION_MODEL_UNKNOWN",
                "model",
                "Select a supported decision model and connection.",
            )
        if self.spec.unavailable_reason:
            raise DecisionMappingError(
                "DECISION_MODEL_UNAVAILABLE",
                "model",
                self.spec.unavailable_reason,
            )
        if not isinstance(api_key, str) or not api_key.strip():
            raise DecisionGatewayError(
                self.spec.connection, JevGatewayError("JEV_KEY_MISSING")
            )
        super().__init__(api_key=api_key, sleep=sleep, clock=clock, jitter=jitter)

    def _validate_model(self, model):
        if model != self.spec.id:
            raise DecisionMappingError(
                "DECISION_MODEL_UNKNOWN",
                "model",
                "The decision model does not match the selected connection.",
            )

    def _transport(
        self, payload: DecisionRequest
    ) -> tuple[str, DecisionRequest | OpenAIDecisionRequest, bool]:
        base = self._gateway_url().removesuffix("/v1/systemone")
        if self.spec.connection == "openai":
            return (
                base + "/v1/decisions",
                request_body(payload, self.spec.gateway_model),
                True,
            )
        return (
            base + "/v1/systemone",
            {**payload, "model": self.spec.gateway_model},
            True,
        )

    def evaluate(
        self,
        model: str,
        state: JSONValue,
        questions: DecisionQuestions,
        *,
        timeout: float = 35,
    ) -> dict[str, JSONValue]:
        state_text = (
            state
            if isinstance(state, str)
            else json.dumps(state, ensure_ascii=False, allow_nan=False)
        )
        if len(state_text.encode("utf-8")) > self.spec.max_state_bytes:
            raise DecisionMappingError(
                "DECISION_INPUT_TOO_LARGE",
                "state",
                "This input exceeds the selected model's supported text size. Map a smaller field; no input was truncated.",
            )
        try:
            response = super().evaluate(model, state, questions, timeout=timeout)
        except JevGatewayError as exc:
            raise DecisionGatewayError(self.spec.connection, exc) from None
        # A 200 is never retried, including refusals and malformed answers.
        if self.spec.connection == "openai":
            response = normalize_response(response, questions)
        actual = response.get("model")
        if not self.spec.accepts_model(actual):
            raise DecisionResponseError("DECISION_MODEL_MISMATCH")
        answers = response.get("answers")
        if isinstance(answers, dict) and any(
            isinstance(answer, dict) and answer.get("type") == "refusal"
            for answer in answers.values()
        ):
            raise DecisionResponseError("DECISION_REFUSED")
        return response
