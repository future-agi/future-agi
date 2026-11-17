"""Test an evaluation's unsaved definition without saving it.

The evaluation editor's Test runs what is on screen. The eval playground
request carries the editor's fields under ``config.unsaved_definition``, the
same fields the template update takes (PUT
/model-hub/eval-templates/<id>/update/). They are applied here, the way that
update applies them, to an in-memory copy of the template that can never be
saved, so a Test never changes what the evaluation, its versions or its
experiments run. Saving stays the editor's Save Version.
"""

import copy
import re

UNSAVED_DEFINITION_KEY = "unsaved_definition"

# Fields of the template update that do not change what a run does.
_NOT_RUN_FIELDS = ("name", "description", "tags", "publish", "eval_type")

_AUTO_CTX_ROOT_TO_FLAG = {
    "row": "full_row",
    "span": "span_context",
    "trace": "trace_context",
    "session": "session_context",
    "call": "call_context",
}

_OUTPUT_MAP = {
    "pass_fail": "Pass/Fail",
    "percentage": "score",
    "deterministic": "choices",
}


class UnsavedDefinitionError(ValueError):
    """The unsaved definition cannot be tested; the message says why."""


def _never_save(*args, **kwargs):
    raise RuntimeError("An unsaved evaluation definition is never saved.")


def _first_error(errors):
    for field, messages in errors.items():
        message = messages[0] if isinstance(messages, list) and messages else messages
        return f"{field}: {message}"
    return "The unsaved changes are not valid."


def _required_keys_and_flags(instructions, messages, template_format):
    """Variables of the prompt and its messages, as the template update reads
    them: auto-context roots become data_injection flags, not inputs."""
    texts = [instructions or ""]
    for msg in messages or []:
        texts.append((msg.get("content") or "") if isinstance(msg, dict) else "")
    if template_format == "jinja":
        from model_hub.utils.jinja_variables import extract_jinja_variables

        raw = []
        for text in texts:
            if text.strip():
                raw.extend(extract_jinja_variables(text))
        raw = list(set(raw))
    else:
        combined = "\n".join(text for text in texts if text)
        raw = [v.strip() for v in re.findall(r"\{\{\s*([^{}]+?)\s*\}\}", combined)]
    flags = {}
    keys = []
    for name in raw:
        head = name.split(".", 1)[0].strip()
        if head in _AUTO_CTX_ROOT_TO_FLAG:
            flags[_AUTO_CTX_ROOT_TO_FLAG[head]] = True
        else:
            keys.append(name)
    return list(set(keys)), flags


def unsaved_eval_template(template, definition):
    """An in-memory copy of ``template`` with the editor's unsaved definition
    applied. Its ``save`` raises: nothing about it reaches the database.

    Raises UnsavedDefinitionError when the evaluation is not the caller's own
    (system evaluations are read-only) or a field is not valid.
    """
    from model_hub.models.choices import OwnerChoices
    from model_hub.serializers.contracts import EvalTemplateUpdateV2RequestSerializer
    from model_hub.types import EvalUpdateRequest
    from model_hub.utils.jev_templates import drop_jev_model_from_code_eval
    from model_hub.utils.scoring import validate_choice_scores, validate_pass_threshold

    if getattr(template, "owner", None) != OwnerChoices.USER.value:
        raise UnsavedDefinitionError(
            "Unsaved changes can be tested only on your own evaluations."
        )
    if not isinstance(definition, dict):
        raise UnsavedDefinitionError(
            "Unsaved changes must be an object of evaluation fields."
        )
    data = {k: v for k, v in definition.items() if k not in _NOT_RUN_FIELDS}
    serializer = EvalTemplateUpdateV2RequestSerializer(data=data)
    unknown = sorted(set(data) - set(serializer.fields))
    if unknown:
        raise UnsavedDefinitionError(f"{unknown[0]}: Unknown field.")
    if not serializer.is_valid():
        raise UnsavedDefinitionError(_first_error(serializer.errors))
    validated = dict(serializer.validated_data)
    try:
        req = EvalUpdateRequest(**validated)
    except Exception as exc:
        raise UnsavedDefinitionError("The unsaved changes are not valid.") from exc

    preview = copy.deepcopy(template)
    # Shadow the model's save on this instance only.
    preview.save = _never_save
    if not isinstance(preview.config, dict):
        preview.config = {}
    config = preview.config

    template_format = req.template_format or config.get("template_format", "mustache")

    # The same fields, in the same order, as the template update.
    if req.instructions is not None:
        # A code eval keeps its code in criteria.
        if config.get("eval_type_id") != "CustomCodeEval":
            preview.criteria = req.instructions
        keys, flags = _required_keys_and_flags(
            req.instructions,
            req.messages if req.messages else config.get("messages", []),
            template_format,
        )
        config["required_keys"] = keys
        config["rule_prompt"] = req.instructions
        if flags:
            data_injection = config.get("data_injection") or {}
            data_injection.update(flags)
            data_injection.pop("variables_only", None)
            data_injection.pop("variablesOnly", None)
            config["data_injection"] = data_injection

    if req.model is not None:
        preview.model = req.model
        config["model"] = req.model

    if req.output_type is not None:
        preview.output_type_normalized = req.output_type
        config["output"] = _OUTPUT_MAP.get(req.output_type, "Pass/Fail")
        if req.output_type == "pass_fail":
            config["choices"] = ["Passed", "Failed"]
            preview.choices = ["Passed", "Failed"]
            config.pop("choices_map", None)
            config.pop("multi_choice", None)

    if req.pass_threshold is not None:
        errors = validate_pass_threshold(req.pass_threshold)
        if errors:
            raise UnsavedDefinitionError("; ".join(errors))
        preview.pass_threshold = req.pass_threshold
        config["pass_threshold"] = req.pass_threshold

    if "choice_scores" in validated:
        if req.choice_scores:
            errors = validate_choice_scores(req.choice_scores)
            if errors:
                raise UnsavedDefinitionError("; ".join(errors))
            preview.choice_scores = req.choice_scores
            preview.choices = list(req.choice_scores.keys())
            config["choices"] = list(req.choice_scores.keys())
            config["choices_map"] = {
                k: "pass" if v >= 0.7 else ("neutral" if v >= 0.3 else "fail")
                for k, v in req.choice_scores.items()
            }
            config["choice_scores"] = req.choice_scores
        else:
            preview.choice_scores = None
            config.pop("choices_map", None)
            config.pop("choice_scores", None)

    if req.multi_choice is not None:
        preview.multi_choice = req.multi_choice
        config["multi_choice"] = req.multi_choice

    if req.check_internet is not None:
        config["check_internet"] = req.check_internet

    if req.code is not None:
        config["code"] = req.code
        config["eval_type_id"] = "CustomCodeEval"
        preview.eval_type = "code"
        preview.criteria = req.code

    if req.code_language is not None:
        config["language"] = req.code_language

    if req.messages is not None:
        config["messages"] = req.messages

    if req.few_shot_examples is not None:
        config["few_shot_examples"] = req.few_shot_examples

    if req.mode is not None:
        config["agent_mode"] = req.mode

    if req.tools is not None:
        config["tools"] = req.tools

    if req.knowledge_bases is not None:
        config["knowledge_bases"] = req.knowledge_bases

    if req.data_injection is not None:
        config["data_injection"] = req.data_injection

    if req.summary is not None:
        config["summary"] = req.summary

    if req.error_localizer_enabled is not None:
        preview.error_localizer_enabled = req.error_localizer_enabled
        config["error_localizer_enabled"] = bool(req.error_localizer_enabled)

    config["template_format"] = template_format

    if "jev_mapping" in validated:
        config["jev_mapping"] = req.jev_mapping
    drop_jev_model_from_code_eval(preview)
    if req.input_data_types is not None:
        config["input_data_types"] = req.input_data_types

    return preview
