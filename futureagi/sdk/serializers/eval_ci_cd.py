from django.db.models import Q
from rest_framework import serializers

from model_hub.models.choices import OwnerChoices
from model_hub.models.evals_metric import EvalTemplate
from model_hub.utils.function_eval_params import normalize_eval_runtime_config
from tracer.models.eval_ci_cd import EvaluationRun
from tracer.models.project import Project


class CICDEvaluationItemSerializer(serializers.Serializer):
    eval_template = serializers.CharField(required=True, allow_blank=False)
    inputs = serializers.DictField(required=True, allow_empty=False)
    model_name = serializers.CharField(
        required=False, allow_null=True, allow_blank=True
    )
    config = serializers.DictField(required=False, default={})

    def _get_eval_template(self, eval_template_name, user):
        """Fetches and returns the evaluation template."""
        try:
            return EvalTemplate.no_workspace_objects.get(
                Q(name=eval_template_name)
                & (Q(organization=user.organization) | Q(organization__isnull=True))
            )
        except EvalTemplate.DoesNotExist:
            raise serializers.ValidationError(  # noqa: B904
                {
                    "eval_template": f"Evaluation template with name '{eval_template_name}' not found."
                }
            )

    def _check_model(self, eval_template, model_name):
        """Validates the model for system-owned evaluation templates."""
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
                            "model_name": f"Model name {model_name} is invalid for this template. Please use one of: {', '.join(valid_models)}"
                        }
                    )

    def _check_input_keys(self, eval_template, inputs):
        """Validates the keys provided in the inputs dictionary."""
        if not isinstance(inputs, dict):
            raise serializers.ValidationError(
                {"inputs": "Inputs must be a dictionary."}
            )

        required_keys = eval_template.config.get("required_keys", [])
        optional_keys = eval_template.config.get("optional_keys", [])
        compulsory_keys = set(required_keys) - set(optional_keys)
        is_user_custom_eval = bool(eval_template.config.get("custom_eval", False))

        extra_keys = set(inputs.keys()) - set(required_keys)
        if extra_keys:
            raise serializers.ValidationError(
                {
                    "inputs": f"Unexpected keys provided: {', '.join(extra_keys)}. Accepted keys are: {', '.join(required_keys)}"
                }
            )

        # System evals stay strict at the SDK boundary. Custom evals let
        # missing keys flow through to the shared validator at run time.
        if not is_user_custom_eval:
            missing_keys = [key for key in compulsory_keys if key not in inputs]
            if missing_keys:
                raise serializers.ValidationError(
                    {"inputs": f"Missing required keys: {', '.join(missing_keys)}"}
                )

    def _check_input_values(self, inputs):
        """Validates the types and lengths of values in the inputs dictionary."""
        if not inputs:
            return

        input_values = list(inputs.values())

        if not all(isinstance(val, list) for val in input_values):
            raise serializers.ValidationError(
                {"inputs": "All input values must be lists for batched requests."}
            )

        if len(input_values) > 1:
            first_list_len = len(input_values[0])
            if not all(len(val) == first_list_len for val in input_values):
                raise serializers.ValidationError(
                    {"inputs": "All input lists must have the same length."}
                )

    def validate(self, data):
        user = self.context.get("request").user if self.context.get("request") else None
        eval_template_name = data.get("eval_template")
        model_name = data.get("model_name")
        inputs = data.get("inputs")

        eval_template = self._get_eval_template(eval_template_name, user)
        self._check_model(eval_template, model_name)
        self._check_input_keys(eval_template, inputs)
        self._check_input_values(inputs)

        try:
            data["config"] = normalize_eval_runtime_config(
                eval_template.config, data.get("config", {})
            )
        except ValueError as e:
            raise serializers.ValidationError({"config": str(e)})  # noqa: B904

        return data


class CICDJobSerializer(serializers.Serializer):
    project_name = serializers.CharField(required=True)
    version = serializers.CharField(required=True)
    eval_data = CICDEvaluationItemSerializer(
        many=True, required=True, allow_empty=False
    )

    def _validate_project(self, project_name, organization):
        """Validates that the project exists and belongs to the user's organization."""
        try:
            return Project.objects.get(
                name=project_name, organization=organization, trace_type="observe"
            )
        except Project.DoesNotExist:
            raise serializers.ValidationError(  # noqa: B904
                {"project_name": f"Project with name '{project_name}' not found."}
            )

    def _validate_version(self, project, version):
        """Validates that the version is unique for the given project."""
        if EvaluationRun.objects.filter(project=project, version=version).exists():
            raise serializers.ValidationError(
                {
                    "version": f"An evaluation run for version '{version}' already exists in this project."
                }
            )

    def validate(self, data):
        project_name = data.get("project_name")
        version = data.get("version")
        request = self.context.get("request")
        organization = request.user.organization

        project = self._validate_project(project_name, organization)
        self._validate_version(project, version)

        data["project"] = project
        return data


class CICDEvaluationRunsQuerySerializer(serializers.Serializer):
    project_name = serializers.CharField(required=True, allow_blank=False)
    versions = serializers.CharField(required=True, allow_blank=False)

    def _parse_versions(self, versions_param):
        """Parse versions from comma-separated string."""
        versions = [v.strip() for v in versions_param.split(",") if v.strip()]
        if not versions:
            raise serializers.ValidationError(
                {
                    "versions": "At least one version must be provided in the 'versions' parameter."
                }
            )
        return versions

    def _validate_evaluation_runs_exist(self, project_name, versions, organization):
        """Validate that evaluation runs exist for all requested versions."""
        evaluation_runs = EvaluationRun.objects.filter(
            project__name=project_name, version__in=versions, organization=organization
        ).select_related("project")

        # Check if all requested versions exist
        found_versions = set(evaluation_runs.values_list("version", flat=True))
        missing_versions = set(versions) - found_versions
        if missing_versions:
            raise serializers.ValidationError(
                {
                    "versions": f"Evaluation runs not found for versions: {', '.join(missing_versions)}"
                }
            )

        return evaluation_runs

    def validate(self, data):
        project_name = data.get("project_name")
        versions_param = data.get("versions")
        request = self.context.get("request")
        organization = request.user.organization

        versions = self._parse_versions(versions_param)

        evaluation_runs = self._validate_evaluation_runs_exist(
            project_name, versions, organization
        )

        data["parsed_versions"] = versions
        data["evaluation_runs"] = evaluation_runs
        return data
