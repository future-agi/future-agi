"""One typed Jev question, with normalization owned exclusively here."""

import json
import re
import time

from jinja2 import Environment, StrictUndefined, TemplateError, UndefinedError

from agentic_eval.core.llm.llm import LLM
from agentic_eval.core.utils.llm_payloads import compute_choices_failure
from ee.evals.llm.custom_prompt_evaluator.rendering import render_prompt
from ee.jev.client import JevSystemOneClient
from ee.jev.mapping import (
    JevMappingError,
    build_question,
    build_state,
    check_input_size,
    mapping_revision,
    validate_jev_mapping,
)
from ee.jev.validation import JevResponseError, validate_response
from tfc.ee_gates import is_jev_model

# A value that is nothing but a link to an image, PDF, audio or video file.
# A link inside prose is text, whatever it points to, and goes to Jev as such.
_MEDIA_URL = re.compile(
    r"https?://\S+\."
    r"(?:png|jpe?g|gif|webp|svg|bmp|tiff?|pdf|mp3|wav|ogg|m4a|flac|mp4|webm)"
    r"(?:[?#]\S*)?",
    re.IGNORECASE,
)
_MEDIA_DATA_URI = ("data:image/", "data:audio/", "data:application/pdf")


def _reject_media(value):
    if isinstance(value, (bytes, bytearray)):
        raise JevMappingError(
            "JEV_INPUT_UNSUPPORTED", "state", "Jev accepts text and JSON inputs only."
        )
    if isinstance(value, str) and (
        _MEDIA_URL.fullmatch(value.strip())
        or value.strip().lower().startswith(_MEDIA_DATA_URI)
    ):
        raise JevMappingError(
            "JEV_INPUT_UNSUPPORTED", "state", "Jev does not accept media inputs."
        )
    if isinstance(value, dict):
        for item in value.values():
            _reject_media(item)
    elif isinstance(value, list):
        for item in value:
            _reject_media(item)


class JevEvaluator(LLM):
    name = "jev_eval"
    display_name = "Jev Evaluation"

    def __init__(
        self,
        rule_prompt,
        model,
        *,
        output_type="Pass/Fail",
        required_keys=None,
        choice_scores=None,
        choices=None,
        pass_threshold=0.5,
        reverse_output=False,
        client=None,
        **config,
    ):
        if not is_jev_model(model):
            raise JevMappingError(
                "JEV_MODEL_UNKNOWN", "model", "Unsupported Jev model."
            )
        self._model = model
        self.rule_prompt = rule_prompt
        self.required_keys = required_keys
        self._output_type = output_type
        self._choice_scores = choice_scores
        self._choices = choices or list(choice_scores or {})
        self._pass_threshold = pass_threshold
        self._reverse_output = bool(reverse_output)
        # The org's own TypeSafe key: kept on the client only, never in config,
        # which flows into result metadata.
        api_key = config.pop("api_key", None)
        self._jev_config = config
        self.client = (
            client if client is not None else JevSystemOneClient(api_key=api_key)
        )
        self.mapping = validate_jev_mapping(
            None,
            output=output_type,
            choice_scores=choice_scores,
            multi_choice=config.get("multi_choice", False),
            config=config,
        )
        self.env = Environment(undefined=StrictUndefined)
        self.last_jev = None
        super().__init__(model_name=model, provider="typesafe", api_key=None)

    def _init_client(self, api_key):
        """The typed TypeSafe client is the only transport for this evaluator."""

    def format_result(self, result_data, eval_template):
        """Use effective binding/version scores in the existing formatter."""
        from types import SimpleNamespace

        from evaluations.engine.formatting import format_eval_value

        result_data["output"] = self._output_type
        effective_template = SimpleNamespace(
            config=eval_template.config,
            choices=self._choices,
            choice_scores=self._choice_scores,
            multi_choice=False,
        )
        return format_eval_value(result_data, effective_template)

    def _reason(self, answer, value, level_count):
        """What Jev answered, restated from the typed answer alone. TypeSafe
        returns no rationale, so this never offers one."""
        if answer.question_type == "noul":
            reason = (
                f"Jev probability {answer.probability:.2f} vs threshold "
                f"{self._pass_threshold:.2f} → {value}"
            )
            return reason + " (reversed)" if self._reverse_output else reason
        if answer.question_type == "choice":
            return f'Jev chose "{value}" (probability {answer.distribution[value]:.2f})'
        score = answer.raw_score
        if float(score).is_integer():
            level = f'{int(score)} of {level_count} "{answer.legend[str(int(score))]}"'
        else:
            # Between two levels: no single rubric label to name.
            level = f"{score:.2f} of {level_count}"
        return f"Jev scored level {level} (normalized {answer.normalized_score:.2f})"

    def _evaluate(self, **kwargs):
        started = time.monotonic()
        config = {**self._jev_config}
        for key in ("input_data_types", "ground_truth_few_shot"):
            if key in kwargs:
                config[key] = kwargs[key]
        validate_jev_mapping(
            None,
            output=self._output_type,
            choice_scores=self._choice_scores,
            multi_choice=config.get("multi_choice", False),
            config=config,
        )
        required = (
            self.required_keys
            if self.required_keys is not None
            else kwargs.get("required_keys", [])
        )
        if not isinstance(required, list) or any(
            key not in kwargs or kwargs[key] is None for key in required
        ):
            raise JevMappingError(
                "JEV_MISSING_VARIABLE",
                "required_keys",
                "A required Jev input variable is missing.",
            )
        if kwargs.get("image_urls"):
            raise JevMappingError(
                "JEV_INPUT_UNSUPPORTED",
                "image_urls",
                "Jev does not accept media inputs.",
            )
        context = {key: kwargs[key] for key in required}
        _reject_media(context)
        parts = []
        if config.get("system_prompt"):
            parts.append(config["system_prompt"])
        parts.append(self.rule_prompt)
        if self.mapping.include_messages:
            parts.extend(
                message.get("content", "")
                for message in config.get("messages", [])
                if message.get("role") != "system" and message.get("content")
            )
        try:
            rendered = [
                render_prompt(
                    part, context, config.get("template_format", "mustache"), self.env
                )[0]
                for part in parts
            ]
        except UndefinedError:
            raise JevMappingError(
                "JEV_MISSING_VARIABLE",
                "required_keys",
                "A required Jev input variable is missing.",
            ) from None
        except (TemplateError, ValueError, TypeError):
            raise JevMappingError(
                "JEV_MAPPING_INVALID",
                "rule_prompt",
                "Could not render Jev instructions.",
            ) from None
        if context:
            rendered.append(
                "The state contains the input variables: " + ", ".join(context) + "."
            )
        question_id, question = build_question(
            self.mapping, rendered[0] if len(rendered) == 1 else rendered
        )
        state = build_state(context)
        check_input_size(
            {"model": self._model, "state": state, "questions": {question_id: question}}
        )
        response = self.client.evaluate(
            self._model, state, {question_id: question}, timeout=35
        )
        levels = self.mapping.score.get("levels")
        try:
            answer = validate_response(
                response,
                self.mapping.question_type,
                requested_model=self._model,
                expected_labels=self.mapping.choice.get("labels"),
                expected_levels=levels,
                level_count=len(levels) if levels else None,
            )
        except JevResponseError as exc:
            # Logged with the gateway's id for the call, so the answer that
            # came back can be found in the gateway's logs.
            self.client.invalid_response("invalid_answer", exc.code)
            raise
        if answer.question_type == "noul":
            metric = answer.probability
            failure = metric < self._pass_threshold
            value = None
        elif answer.question_type == "choice":
            value = answer.choice
            metric = self._choice_scores[value]
            failure = compute_choices_failure(
                value, self._choices, self._choice_scores, self._pass_threshold
            )
        else:
            value = metric = answer.normalized_score
            failure = metric < self._pass_threshold
        if self._reverse_output:
            failure = not failure
        if answer.question_type == "noul":
            value = "Fail" if failure else "Pass"
        reason = self._reason(answer, value, len(levels) if levels else None)
        usage = response["usage"]
        self.token_usage = {
            "prompt_tokens": usage["input_tokens"],
            "completion_tokens": usage["output_tokens"],
            "total_tokens": usage["input_tokens"] + usage["output_tokens"],
        }
        # Jev runs on the org's own TypeSafe key, so TypeSafe bills them, not
        # us. No provider cost here leaves the flat per-run eval fee as the
        # whole platform charge.
        self.cost.update(
            {
                "model": response["model"],
                "prompt_cost": 0.0,
                "completion_cost": 0.0,
                "total_cost": 0.0,
            }
        )
        runtime = int((time.monotonic() - started) * 1000)
        jev = {
            "requested_model": self._model,
            "actual_model": response["model"],
            "mapping_revision": mapping_revision(self.mapping),
            "question_type": answer.question_type,
            "probability": answer.probability,
            "threshold": self._pass_threshold,
            "reverse_output": self._reverse_output,
            "verdict": (
                None
                if answer.question_type == "choice"
                else ("fail" if failure else "pass")
            ),
            "distribution": answer.distribution,
            "confidence": answer.confidence,
            "raw_score": answer.raw_score,
            "legend": answer.legend,
            "normalized_score": answer.normalized_score,
            "usage": {
                "input_tokens": usage["input_tokens"],
                "output_tokens": usage["output_tokens"],
            },
            # The call's id in the gateway's logs (None on the direct
            # transport), to trace a disputed answer.
            "gateway_request_id": self.client.gateway_request_id,
        }
        self.last_jev = jev
        return {
            "name": self.name,
            "display_name": self.display_name,
            "data": {"result": value},
            "failure": failure,
            "reason": reason,
            "runtime": runtime,
            "model": response["model"],
            "metrics": [{"id": "custom_eval_score", "value": metric}],
            "metadata": json.dumps(
                {
                    "usage": self.token_usage,
                    "cost": self.cost,
                    "response_time": runtime,
                    "jev": jev,
                }
            ),
            "datapoint_field_annotations": None,
        }
