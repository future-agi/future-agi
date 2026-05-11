import re

from rest_framework import serializers

from model_hub.models.choices import EvalTemplateType, OwnerChoices


class EvalTemplateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=50)
    owner = serializers.CharField(max_length=50, default=OwnerChoices.SYSTEM.value)
    config = serializers.JSONField()  # JSONField is used for dictionary-like objects
    eval_tags = serializers.ListField(
        child=serializers.CharField(
            max_length=100
        ),  # Each tag is a string with max length of 100
        allow_empty=True,  # Allows empty lists
        required=False,  # Makes eval_tags optional
    )


class EvalUserTemplateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=50)
    template_id = serializers.CharField(max_length=500)
    dataset_id = serializers.CharField(max_length=500)
    config = serializers.JSONField()
    model = serializers.CharField(max_length=100, required=False)


class EvalListSerializer(serializers.Serializer):
    search_text = serializers.CharField(
        max_length=100, required=False, allow_null=True, allow_blank=True
    )
    eval_categories = serializers.CharField(
        max_length=50, required=False, allow_null=True, allow_blank=True
    )
    eval_type = serializers.CharField(
        max_length=50, required=False, allow_null=True, allow_blank=True
    )
    eval_tags = serializers.ListField(
        child=serializers.CharField(max_length=50), required=False, default=[]
    )
    use_cases = serializers.ListField(
        child=serializers.CharField(max_length=100), required=False, default=[]
    )


# core-backend/model_hub/serializers/eval_template.py


class CustomEvalTemplateCreateSerializer(serializers.Serializer):
    template_type = serializers.ChoiceField(
        choices=EvalTemplateType.get_choices(), default=EvalTemplateType.FUTUREAGI.value
    )
    name = serializers.CharField(max_length=255)
    description = serializers.CharField(
        required=False, allow_null=True, allow_blank=True
    )
    tags = serializers.ListField(
        child=serializers.CharField(), required=False, default=[], allow_null=True
    )
    criteria = serializers.CharField(
        required=False, default="", allow_null=True, allow_blank=True, max_length=100000
    )
    output_type = serializers.ChoiceField(
        choices=["Pass/Fail", "score", "choices"], default="Pass/Fail"
    )
    required_keys = serializers.ListField(child=serializers.CharField(), required=True)
    config = serializers.DictField(default={})
    check_internet = serializers.BooleanField(default=False)
    choices = serializers.DictField(
        child=serializers.CharField(), required=False, allow_null=True, default={}
    )
    multi_choice = serializers.BooleanField(default=False)
    template_id = serializers.CharField(
        max_length=500, required=False, allow_null=True, allow_blank=True
    )

    def validate(self, data):
        name = data.get("name")
        if name:
            from model_hub.utils.eval_validators import validate_eval_name

            try:
                cleaned_name = validate_eval_name(name)
            except ValueError as e:
                raise serializers.ValidationError(str(e))
            if name != cleaned_name:
                data["name"] = cleaned_name

        # Validate that criteria contains at least one template variable
        # (skip if data injection is enabled — eval runs on injected data without mapping)
        criteria = data.get("criteria", "")
        config = data.get("config", {})
        data_injection = config.get("data_injection", {})
        has_data_injection = (
            (
                data_injection.get("full_row")
                or data_injection.get("fullRow")
                or not data_injection.get("variables_only", True)
                or not data_injection.get("variablesOnly", True)
            )
            if data_injection
            else False
        )

        if criteria:
            variable_pattern = r"\{\{[a-zA-Z0-9_]+\}\}"
            if not re.search(variable_pattern, criteria) and not has_data_injection:
                raise serializers.ValidationError(
                    "Criteria must contain at least one template variable "
                    "using double curly braces (e.g. {{variable_name}}), or "
                    "enable data injection to evaluate without mapping."
                )
        elif not has_data_injection:
            raise serializers.ValidationError(
                "Criteria is required and must contain at least one template variable "
                "using double curly braces (e.g. {{variable_name}})."
            )

        output_type = data.get("output_type")
        choices = data.get("choices")
        if output_type == "choices":
            if not choices or not isinstance(choices, dict):
                raise serializers.ValidationError(
                    "Choices must be provided as a dict when output_type is 'choices'."
                )
        config = data.get("config")
        if config:
            VALID_KEYS = [
                "model",
                "proxy_agi",
                "visible_ui",
                "reverse_output",
                "config",
            ]
            if not isinstance(config, dict):
                raise serializers.ValidationError("Config must be provided as a dict.")
            if any(key not in VALID_KEYS for key in config.keys()):
                raise serializers.ValidationError(
                    f"Invalid keys in config. Allowed keys are: {VALID_KEYS}"
                )

        # Validate LengthBetween minLength <= maxLength
        from model_hub.utils.eval_validators import validate_length_between_config

        try:
            validate_length_between_config(config)
        except ValueError as e:
            raise serializers.ValidationError(str(e))

        return data

        # Save to model


class UserEvalSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=50)
    template_id = serializers.CharField(max_length=500)
    config = serializers.JSONField()
    kb_id = serializers.UUIDField(required=False)
    error_localizer = serializers.BooleanField(default=False)
    model = serializers.CharField(max_length=100, required=False)
    # Per-binding weight overrides for composite evals. Ignored for
    # single-template metrics. Shape: `{"<child_template_id>": <weight>}`.
    composite_weight_overrides = serializers.JSONField(
        required=False, allow_null=True, default=None
    )

    def validate_name(self, value):
        from model_hub.utils.eval_validators import validate_eval_name

        try:
            return validate_eval_name(value)
        except ValueError as e:
            raise serializers.ValidationError(str(e))

    def validate_composite_weight_overrides(self, value):
        if value is None:
            return None
        if not isinstance(value, dict):
            raise serializers.ValidationError(
                "composite_weight_overrides must be an object mapping "
                "child_template_id -> weight."
            )
        cleaned = {}
        for k, v in value.items():
            try:
                cleaned[str(k)] = float(v)
            except (TypeError, ValueError):
                raise serializers.ValidationError(  # noqa: B904
                    f"Weight for {k!r} must be a number, got {v!r}."
                )
        return cleaned or None


class UpdateColumnConfigSerializer(serializers.Serializer):
    eval_id = serializers.UUIDField()
    column_config = serializers.ListField(child=serializers.DictField(), required=False)
    source = serializers.CharField(max_length=50, required=False)

    def validate_column_config(self, value):
        if not isinstance(value, list):
            raise serializers.ValidationError(
                "Column config must be a list of objects."
            )
        KEYS = [
            "id",
            "name",
            "status",
            "is_visible",
            "is_frozen",
            "source_type",
            "data_type",
            "origin_type",
            "output_type",
        ]
        for val in value:
            if any(key not in KEYS for key in val.keys()):
                raise serializers.ValidationError(
                    f"Invalid keys in column config. Allowed keys are: {KEYS}"
                )
        return value


class EvalPlayGroundSerializer(serializers.Serializer):
    template_id = serializers.UUIDField()  # need this
    model = serializers.CharField(
        max_length=100,
        allow_null=True,
        allow_blank=True,
        required=False,
        default="turing_large",
    )  # need this
    kb_id = serializers.UUIDField(required=False, allow_null=True)  # need this
    error_localizer = serializers.BooleanField(default=False)  # need this
    config = serializers.JSONField(allow_null=True, required=False, default={})
    params = serializers.JSONField(allow_null=True, required=False, default={})
    mapping = serializers.JSONField(allow_null=True, required=False)
    mapping_paths = serializers.JSONField(allow_null=True, required=False)
    input_data_types = serializers.JSONField(allow_null=True, required=False)
    # Auto-context payloads — caller may send any combination. The evaluator
    # detects `{{row.X}}` / `{{span.X}}` / `{{trace.X}}` / `{{session.X}}`
    # (and their bare forms) in the template and binds these dicts into the
    # Jinja render context. All optional — absent kwargs render as
    # "(... data not provided)" placeholders.
    row_context = serializers.JSONField(allow_null=True, required=False)
    span_context = serializers.JSONField(allow_null=True, required=False)
    trace_context = serializers.JSONField(allow_null=True, required=False)
    session_context = serializers.JSONField(allow_null=True, required=False)
    call_context = serializers.JSONField(allow_null=True, required=False)
    # Alternative: caller may pass IDs and the view will fetch the data.
    span_id = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    trace_id = serializers.UUIDField(required=False, allow_null=True)
    session_id = serializers.UUIDField(required=False, allow_null=True)
    call_id = serializers.UUIDField(required=False, allow_null=True)


class UpdateEvalTemplateSerializer(serializers.Serializer):
    eval_template_id = serializers.UUIDField()
    description = serializers.CharField(max_length=255, required=False, default="")
    criteria = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    eval_tags = serializers.ListField(
        child=serializers.CharField(), required=False, default=[]
    )
    multi_choice = serializers.BooleanField(default=False)
    function_eval = serializers.BooleanField(default=False)
    choices_map = serializers.DictField(
        child=serializers.CharField(), required=False, allow_null=True, default={}
    )
    config = serializers.JSONField(default={})
    model = serializers.CharField(max_length=100, required=False)
    check_internet = serializers.BooleanField(required=False, allow_null=True)
    name = serializers.CharField(
        max_length=255, required=False, allow_null=False, allow_blank=False
    )
    required_keys = serializers.ListField(
        child=serializers.CharField(), required=False, default=[]
    )


class DuplicateEvalTemplateSerializer(serializers.Serializer):
    eval_template_id = serializers.UUIDField()
    name = serializers.CharField(
        max_length=255, required=True, allow_null=False, allow_blank=False
    )


class DeleteEvalTemplateSerializer(serializers.Serializer):
    eval_template_id = serializers.UUIDField()


class TestEvalTemplateSerializer(serializers.Serializer):
    config = serializers.JSONField()
    model = serializers.CharField(
        max_length=100,
        allow_null=True,
        allow_blank=True,
        required=False,
        default="turing_large",
    )
    eval_tags = serializers.ListField(
        child=serializers.CharField(), required=False, allow_empty=True, default=[]
    )
    criteria = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    multi_choice = serializers.BooleanField(default=False)
    choices = serializers.DictField(
        child=serializers.CharField(), required=False, allow_null=True, default={}
    )
    input_data_types = serializers.JSONField(required=False, default={})
    name = serializers.CharField(max_length=255, required=True)
    description = serializers.CharField(
        max_length=255, required=False, allow_blank=True, allow_null=True, default=""
    )
    output_type = serializers.CharField(max_length=50, required=True)
    check_internet = serializers.BooleanField(default=False)
    required_keys = serializers.ListField(default=[])
    template_type = serializers.CharField(default="")
    eval_type_id = serializers.CharField(
        max_length=100, required=False, allow_blank=True, allow_null=True
    )
