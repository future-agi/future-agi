"""Decision evaluator with general BYOK accounting, separate from native Jev."""

from ee.decisions.client import DecisionClient
from ee.decisions.errors import (
    DecisionMappingError,
    DecisionResponseError,
    mapping_error,
)
from ee.evals.llm.jev_evaluator.evaluator import TypedDecisionEvaluator
from ee.jev.mapping import JevMappingError
from ee.jev.validation import JevResponseError
from evaluations.decision_models import get_decision_model
from evaluations.decision_pricing import calculate_decision_cost


class DecisionEvaluator(TypedDecisionEvaluator):
    name = "decision_eval"
    display_name = "Decision Evaluation"
    metadata_key = "decision"

    def __init__(self, *args, **kwargs):
        # A sibling of JevEvaluator, not a subclass: only TypeSafe-authored
        # models use Jev billing; other models keep the general BYOK path.
        try:
            super().__init__(*args, **kwargs)
        except JevMappingError as exc:
            raise mapping_error(exc) from None

    def _validate_model(self, model):
        self.spec = get_decision_model(model)
        if self.spec is None or not model.startswith("decision/"):
            raise DecisionMappingError(
                "DECISION_MODEL_UNKNOWN",
                "model",
                "Select a supported decision model and connection.",
            )
        self.connection = self.spec.connection
        self.model_label = self.spec.label

    def _make_client(self, api_key):
        return DecisionClient(model=self._model, api_key=api_key)

    def _record_cost(self, response):
        try:
            self.cost.update(
                calculate_decision_cost(
                    self._model,
                    response["usage"],
                    provider_cost=response["usage"].get("cost"),
                )
            )
        except (ValueError, TypeError, ArithmeticError):
            raise DecisionResponseError("DECISION_RESPONSE_INVALID") from None

    def _evaluate(self, **kwargs):
        try:
            return super()._evaluate(**kwargs)
        except DecisionResponseError:
            raise
        except JevMappingError as exc:
            raise mapping_error(exc) from None
        except JevResponseError as exc:
            raise DecisionResponseError(exc.code.replace("JEV_", "DECISION_")) from None
