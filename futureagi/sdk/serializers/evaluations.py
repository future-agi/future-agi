from django.db.models import Q
from rest_framework import serializers

from model_hub.models.choices import OwnerChoices
from model_hub.models.evals_metric import EvalTemplate
from model_hub.utils.function_eval_params import normalize_eval_runtime_config


class ConfigureEvaluationsSerializer(serializers.Serializer):
    eval_templates = serializers.CharField()
    inputs = serializers.DictField()
    model_name = serializers.CharField(
        required=False, allow_null=True, allow_blank=True
    )
    config = serializers.DictField(required=False, default={})

    def validate(self, data):
        eval_templates = data.get("eval_templates")
        model_name = data.get("model_name")
        inputs = data.get("inputs")

        # Get user and workspace from context
        request = self.context.get("request")
        user = request.user if request else None
        workspace = getattr(request, "workspace", None)

        try:
            eval_template = EvalTemplate.no_workspace_objects.get(
                Q(name=eval_templates)
                & (
                    Q(organization=user.organization, workspace=workspace)
                    | Q(organization__isnull=True)
                )
            )
        except EvalTemplate.DoesNotExist:
            raise serializers.ValidationError(  # noqa: B904
                f"Evaluation template with name :{eval_templates} not found."
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

        extra_keys = set(inputs.keys()) - set(required_keys)
        if extra_keys:
            raise serializers.ValidationError(
                {
                    "inputs": f"Unexpected keys provided: {', '.join(extra_keys)}. Accepted keys are: {', '.join(required_keys)}"
                }
            )

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
        except ValueError as e:
            raise serializers.ValidationError({"config": str(e)})  # noqa: B904

        return data
