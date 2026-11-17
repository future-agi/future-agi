from rest_framework import serializers

from evaluations.constants import CUSTOM_PROMPT_EVALUATOR_TYPE_ID
from evaluations.decision_models import has_decision_prefix
from model_hub.models.choices import OwnerChoices
from model_hub.models.evals_metric import EXTERNAL_EVAL_TYPE, EvalTemplate
from model_hub.utils.code_eval_inputs import code_eval_entry
from model_hub.utils.function_eval_params import normalize_eval_runtime_config
from model_hub.utils.jev_templates import binding_model
from sdk.utils.template_lookup import find_sdk_template

NO_SAVED_MODEL = (
    "This evaluation template has no saved model, so pass {field} to "
    "choose the model it runs on."
)


def unexpected_input_keys_message(eval_template, inputs, *, empty_inputs_allowed=True):
    """Why ``inputs`` holds keys ``eval_template`` does not take, or None.

    A template takes the keys its required_keys declare. A user's code eval
    with code also takes the parameters of its code's entry function, as the
    app's variable-mapping form offers them next to the declared keys
    (frontend/src/utils/codeEvalParams.js), or any key when that function
    takes **kwargs or cannot be read. Other templates, the built-in code evals
    included, take only the keys they declare.
    ``empty_inputs_allowed`` is False on a route that needs at least one input.
    """
    config = eval_template.config or {}
    declared = list(config.get("required_keys") or [])
    # "{{ }}" in instructions declares a blank key: still accepted, never named.
    declared_names = [key for key in declared if isinstance(key, str) and key.strip()]
    accepted = declared
    named = declared_names
    is_code_eval = config.get("eval_type_id") == "CustomCodeEval"
    code = config.get("code")
    entry = None
    if (
        is_code_eval
        and eval_template.owner == OwnerChoices.USER.value
        and isinstance(code, str)
        and code.strip()
    ):
        entry = code_eval_entry(code)
        if entry is None:
            return None
        accepted = list(dict.fromkeys([*entry.parameters, *declared]))
        named = list(dict.fromkeys([*entry.parameters, *declared_names]))

    unexpected = sorted(set(inputs) - set(accepted))
    if not unexpected:
        return None
    provided = f"Unexpected keys provided: {', '.join(unexpected)}."
    if entry is not None and declared_names:
        return (
            f"{provided} Code eval '{eval_template.name}' accepts the "
            f"parameters of its {entry.name}() function and the keys it "
            f"declares: {', '.join(named)}. Send inputs with these keys, or "
            f"add **kwargs to {entry.name}() to accept any key."
        )
    if entry is not None and named:
        return (
            f"{provided} Code eval '{eval_template.name}' reads only the "
            f"parameters of its {entry.name}() function: {', '.join(named)}. "
            f"Send inputs with these keys, or add **kwargs to {entry.name}() to "
            "accept any key."
        )
    if entry is not None:
        return (
            f"{provided} Code eval '{eval_template.name}' reads no inputs: its "
            f"{entry.name}() function takes no parameters. Add the keys you send "
            f"as parameters of {entry.name}(), or add **kwargs to accept any key."
        )
    if named:
        return f"{provided} Accepted keys are: {', '.join(named)}"

    fixes = []
    if empty_inputs_allowed:
        fixes.append("send an empty inputs object")
    if eval_template.owner != OwnerChoices.SYSTEM.value:
        if is_code_eval:
            fixes.append("add code with an evaluate() function that takes these keys")
        else:
            fixes.append(
                "add the keys to the template as variables, for example "
                f"{{{{{unexpected[0]}}}}} in its instructions"
            )
    declares = (
        f"{provided} Evaluation template '{eval_template.name}' declares no "
        "input keys"
    )
    if not fixes:
        return (
            f"{declares}, and this endpoint needs at least one input, so the "
            "template cannot run here."
        )
    advice = ", or ".join(fixes)
    return f"{declares}. {advice[0].upper()}{advice[1:]}."


def validate_run_config(eval_template: EvalTemplate, config: dict) -> None:
    """Refuse an evaluation config whose run_config, as the engine reads it,
    is set but is not an object, when the template's evaluator reads one.

    The engine reads ``config`` with the keys of its params lifted over it
    (sdk.utils.evaluations.engine_runtime_config). The evaluator types of
    its run_config allow-list (_RUNTIME_ALLOWED_KEYS: the agent, LLM-judge
    and Jev evaluators) take settings from that run_config, so for them a
    truthy run_config that is not an object is refused rather than run
    without the settings it was sent for. Every other evaluator reads no
    run_config, so it is not checked. A falsy one is read as none, and one
    that params replaces is never read: both are accepted.

    Raises:
        serializers.ValidationError: under ``config``, keyed by the path of
            the run_config the engine reads: params.run_config when params
            sets one, else run_config.
    """
    from evaluations.engine.instance import _RUNTIME_ALLOWED_KEYS
    from sdk.utils.evaluations import engine_runtime_config

    if eval_template.config.get("eval_type_id") not in _RUNTIME_ALLOWED_KEYS:
        return
    run_config = engine_runtime_config(config).get("run_config")
    if not run_config or isinstance(run_config, dict):
        return
    error = {"run_config": ["must be an object"]}
    params = config.get("params")
    if isinstance(params, dict) and "run_config" in params:
        error = {"params": error}
    raise serializers.ValidationError({"config": error})


def _engine_has_judge_model(eval_template, runtime_config):
    """Whether the engine has a model for an LLM-judge item sent without one.

    Mirrors the judge's model choice in evaluations.engine.instance when no
    model is passed (create_eval_instance, then prepare_eval_config): the
    template's config["model"], else a decision or Jev model the item's
    config names (run_config.model, else model), which the engine runs or
    refuses as unknown. No other model in the item's config reaches a judge
    (_RUNTIME_ALLOWED_KEYS), and the template's saved model is not read.
    ``runtime_config`` is the item's config as the engine receives it.
    """
    if (eval_template.config or {}).get("model"):
        return True
    run_config = runtime_config.get("run_config") or {}
    return has_decision_prefix(run_config.get("model") or runtime_config.get("model"))


def resolve_missing_judge_model(
    eval_template: EvalTemplate, data: dict, *, model_field: str = "model_name"
) -> None:
    """Gives an item of a customer's LLM-judge template, sent without
    model_name, that the engine would have no model for, the model
    binding_model resolves for it, or refuses it when there is none.

    The model is the one the item's config names, as the engine reads it
    (engine_runtime_config), else the template's saved model. It is
    written to model_name, so the evaluation, its usage check and its
    charge are those of an item that named it. Every other item is left
    as it is; an item that names a model keeps it, since binding_model
    would rank the item's config above it. ``data`` is one item's
    validated data: a CI/CD item or an SDK evaluation request.
    ``model_field`` is the name the caller's request gives the model; the
    refusal is keyed and worded with it.
    """
    from sdk.utils.evaluations import engine_runtime_config

    if data.get("model_name"):
        return
    if eval_template.owner == OwnerChoices.SYSTEM.value:
        return
    if eval_template.config.get("eval_type_id") != CUSTOM_PROMPT_EVALUATOR_TYPE_ID:
        return
    runtime_config = engine_runtime_config(data["config"])
    if _engine_has_judge_model(eval_template, runtime_config):
        return
    model = binding_model(eval_template, model=None, runtime_config=runtime_config)
    if not model:
        raise serializers.ValidationError(
            {model_field: NO_SAVED_MODEL.format(field=model_field)}
        )
    data["model_name"] = model


class ConfigureEvaluationsSerializer(serializers.Serializer):
    eval_templates = serializers.CharField()
    inputs = serializers.DictField()
    model_name = serializers.CharField(
        required=False, allow_null=True, allow_blank=True
    )
    config = serializers.DictField(required=False, default={})

    def validate(self, data):
        self._check_request(data)
        return data

    def _check_request(self, data):
        """Checks the request, normalizes its config in place and returns
        the template it names."""
        eval_templates = data.get("eval_templates")
        model_name = data.get("model_name")
        inputs = data.get("inputs")

        # Get user and workspace from context
        request = self.context.get("request")
        user = request.user if request else None
        workspace = getattr(request, "workspace", None)
        # The organization the SDK views store and run under.
        organization = getattr(request, "organization", None) or user.organization

        try:
            eval_template = find_sdk_template(eval_templates, organization, workspace)
        except EvalTemplate.DoesNotExist:
            raise serializers.ValidationError(  # noqa: B904
                f"Evaluation template with name :{eval_templates} not found."
            )

        if eval_template.eval_type == EXTERNAL_EVAL_TYPE:
            raise serializers.ValidationError(
                f"Evaluation template {eval_templates} is an external metric: it is "
                "scored by the caller and is never executed by the server."
            )

        if eval_template.owner == OwnerChoices.SYSTEM.value:
            if "models" in eval_template.config:
                if not model_name:
                    raise serializers.ValidationError(
                        {"model_name": "Model is required for system evals"}
                    )

                valid_models = eval_template.config.get("models", [])
                if model_name not in valid_models:
                    raise serializers.ValidationError(
                        {
                            "model_name": f"Model name {model_name} is invalid please use one of the following models: {', '.join(valid_models)}"
                        }
                    )

        required_keys = eval_template.config.get("required_keys", [])
        optional_keys = eval_template.config.get("optional_keys", [])
        compulsory_keys = set(required_keys) - set(optional_keys)
        is_user_custom_eval = bool(eval_template.config.get("custom_eval", False))

        if not isinstance(inputs, dict):
            raise serializers.ValidationError(
                {"inputs": "Inputs must be a dictionary."}
            )

        unexpected_keys = unexpected_input_keys_message(eval_template, inputs)
        if unexpected_keys:
            raise serializers.ValidationError({"inputs": unexpected_keys})

        # System evals: every required key must be present in the SDK
        # payload. User-built custom evals get the partial-input rule —
        # missing keys flow through and the shared validator at execute
        # time decides whether to fail (all-empty) or run with a warning.
        if not is_user_custom_eval:
            missing_keys = [key for key in compulsory_keys if key not in inputs]
            if missing_keys:
                raise serializers.ValidationError(
                    {"inputs": f"Missing required keys: {', '.join(missing_keys)}"}
                )

        if inputs:
            input_values = list(inputs.values())

            def is_list_of_strings(val):
                return isinstance(val, list) and all(isinstance(s, str) for s in val)

            def is_list_of_list_of_strings(val):
                return isinstance(val, list) and all(
                    isinstance(s, list) and all(isinstance(x, str) for x in s)
                    for s in val
                )

            # Check if each individual value is valid
            for val in input_values:
                is_string = isinstance(val, str)
                is_list_of_strings_val = is_list_of_strings(val)
                is_list_of_list_of_strings_val = is_list_of_list_of_strings(val)

                if not (
                    is_string
                    or is_list_of_strings_val
                    or is_list_of_list_of_strings_val
                ):
                    raise serializers.ValidationError(
                        {
                            "inputs": "All input values must be either strings, lists of strings, or lists of lists of strings."
                        }
                    )

            # Check if all list values have the same length (only for lists of strings)
            list_of_strings_values = [v for v in input_values if is_list_of_strings(v)]
            if list_of_strings_values and len(list_of_strings_values) > 1:
                list_len = len(list_of_strings_values[0])

                if not all(len(v) == list_len for v in list_of_strings_values):
                    raise serializers.ValidationError(
                        {"inputs": "All input lists must have the same length."}
                    )

                if list_len > 3:
                    raise serializers.ValidationError(
                        {
                            "inputs": f"Input lists cannot have more than 3 items, but found length {list_len}."
                        }
                    )

        try:
            data["config"] = normalize_eval_runtime_config(
                eval_template.config, data.get("config", {})
            )
            from sdk.utils.evaluations import validate_sdk_choice_score_override

            validate_sdk_choice_score_override(eval_template, data["config"])
        except ValueError as e:
            raise serializers.ValidationError({"config": str(e)})  # noqa: B904

        return eval_template


class StandaloneEvaluationsSerializer(ConfigureEvaluationsSerializer):
    """Select new-eval batches without changing configuration or CI/CD policy."""

    def validate(self, data):
        # Retain the base value types and its existing three-row cap scope.
        eval_template = self._check_request(data)
        validate_run_config(eval_template, data["config"])
        inputs = data["inputs"]
        data["is_batched"] = bool(inputs) and all(
            isinstance(value, list) for value in inputs.values()
        )
        if data["is_batched"] and len({len(value) for value in inputs.values()}) > 1:
            # Include list-valued columns, not just flat string columns. Check
            # the whole shape before either runner can persist or meter a row.
            raise serializers.ValidationError(
                {"inputs": "All input lists must have the same length."}
            )
        # After the config check: the model may come from the request's config.
        # The request names the model "model"; the view passes it as model_name.
        resolve_missing_judge_model(eval_template, data, model_field="model")
        return data
