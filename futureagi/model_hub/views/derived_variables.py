"""
API endpoints for managing derived variables from JSON/structured outputs.
"""

import structlog
from drf_yasg.utils import swagger_auto_schema
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated

from model_hub.models.run_prompt import PromptVersion
from model_hub.serializers.contracts import (
    MODEL_HUB_ERROR_RESPONSES,
    DerivedVariableDetailResponseSerializer,
    DerivedVariableExtractRequestSerializer,
    DerivedVariablePreviewRequestSerializer,
    PromptDerivedVariablesResponseSerializer,
)
from model_hub.serializers.develop_dataset_contracts import (
    DatasetDerivedVariablesResponseSerializer,
)
from model_hub.services.derived_variable_service import (
    extract_derived_variables_from_output,
    get_all_derived_variables,
    get_dataset_derived_variables,
    get_derived_variable_schema,
    update_prompt_version_derived_variables,
)
from model_hub.utils.workspace_scope import (
    request_organization,
    request_workspace_filter,
)
from tfc.utils.api_contracts import validated_api_request
from tfc.utils.general_methods import GeneralMethods

logger = structlog.get_logger(__name__)

_gm = GeneralMethods()


def _prompt_version_queryset(request, prompt_id):
    return PromptVersion.no_workspace_objects.filter(
        request_workspace_filter(request, field_name="original_template__workspace"),
        original_template_id=prompt_id,
        original_template__organization=request_organization(request),
        original_template__deleted=False,
    )


@swagger_auto_schema(
    method="get",
    responses={
        200: PromptDerivedVariablesResponseSerializer,
        **MODEL_HUB_ERROR_RESPONSES,
    },
)
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def get_prompt_derived_variables(request, prompt_id):
    """
    Get all derived variables for a prompt template.

    Returns derived variables from JSON outputs across all versions.

    Query params:
        - version: Optional version filter
        - column_name: Optional column name filter

    Returns:
        {
            "result": {
                "version": "v1",
                "derived_variables": {
                    "OutputColumn": {
                        "paths": ["user.name", "user.email"],
                        "schema": {...},
                        "full_variables": ["OutputColumn.user.name", ...]
                    }
                }
            }
        }
    """
    try:
        version = request.query_params.get("version")
        column_name = request.query_params.get("column_name")

        # Get the prompt version
        queryset = _prompt_version_queryset(request, prompt_id)
        if version:
            queryset = queryset.filter(template_version=version)

        prompt_version = queryset.order_by("-created_at").first()

        if not prompt_version:
            return _gm.not_found("Prompt version not found")

        # Get derived variables
        all_derived = get_all_derived_variables(prompt_version)

        if column_name and column_name in all_derived:
            result = {column_name: all_derived[column_name]}
        else:
            result = all_derived

        return _gm.success_response(
            {
                "version": prompt_version.template_version,
                "derived_variables": result,
            }
        )

    except Exception as e:
        logger.exception(f"Error getting derived variables: {e}")
        return _gm.internal_server_error_response(
            "Failed to retrieve derived variables"
        )


@swagger_auto_schema(
    method="get",
    responses={
        200: DerivedVariableDetailResponseSerializer,
        **MODEL_HUB_ERROR_RESPONSES,
    },
)
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def get_derived_variable_schema_view(request, prompt_id, column_name):
    """
    Get the schema for derived variables of a specific column.

    Returns detailed schema information including types and sample values.

    Path params:
        - prompt_id: UUID of the prompt template
        - column_name: Name of the column

    Query params:
        - version: Optional version filter

    Returns:
        {
            "result": {
                "paths": ["user.name", "user.email"],
                "schema": {
                    "user.name": {"type": "string", "sample": "John"},
                    ...
                },
                "is_json": true,
                "raw_sample": {...}
            }
        }
    """
    try:
        version = request.query_params.get("version")

        queryset = _prompt_version_queryset(request, prompt_id)
        if version:
            queryset = queryset.filter(template_version=version)

        prompt_version = queryset.order_by("-created_at").first()

        if not prompt_version:
            return _gm.not_found("Prompt version not found")

        schema = get_derived_variable_schema(prompt_version, column_name)

        return _gm.success_response(schema)

    except Exception as e:
        logger.exception(f"Error getting derived variable schema: {e}")
        return _gm.internal_server_error_response(
            "Failed to retrieve derived variable schema"
        )


@swagger_auto_schema(
    method="post",
    request_body=DerivedVariableExtractRequestSerializer,
    responses={
        200: DerivedVariableDetailResponseSerializer,
        **MODEL_HUB_ERROR_RESPONSES,
    },
    runtime_request_validation=True,
    runtime_response_validation=True,
)
@api_view(["POST"])
@permission_classes([IsAuthenticated])
@validated_api_request(
    request_serializer=DerivedVariableExtractRequestSerializer,
    responses={
        200: DerivedVariableDetailResponseSerializer,
        **MODEL_HUB_ERROR_RESPONSES,
    },
    reject_unknown_fields=True,
    document=False,
)
def extract_derived_variables(request, prompt_id):
    """
    Manually trigger extraction of derived variables from outputs.

    This is useful when you want to re-extract variables or extract from
    existing outputs that weren't processed.

    Request body:
        - version: Version to extract from
        - column_name: Name for the output column
        - output_index: Optional specific output index (default: 0)
        - response_format_type: Optional response format hint

    Returns:
        {
            "result": {
                "paths": ["user.name", ...],
                "schema": {...},
                "full_variables": [...]
            }
        }
    """
    try:
        version = request.validated_data.get("version")
        column_name = request.validated_data.get("column_name", "output")
        output_index = request.validated_data.get("output_index", 0)
        response_format_type = request.validated_data.get("response_format_type")

        if not version:
            return _gm.bad_request("Version is required")

        prompt_version = (
            _prompt_version_queryset(request, prompt_id)
            .filter(
                template_version=version,
            )
            .first()
        )

        if not prompt_version:
            return _gm.not_found("Prompt version not found")

        # Extract and update derived variables
        result = update_prompt_version_derived_variables(
            prompt_version,
            output_index,
            column_name,
            response_format_type,
        )

        # Save the updated metadata
        prompt_version.save(update_fields=["metadata"])

        return _gm.success_response(result)

    except Exception as e:
        logger.exception(f"Error extracting derived variables: {e}")
        return _gm.internal_server_error_response("Failed to extract derived variables")


@swagger_auto_schema(
    method="post",
    request_body=DerivedVariablePreviewRequestSerializer,
    responses={
        200: DerivedVariableDetailResponseSerializer,
        **MODEL_HUB_ERROR_RESPONSES,
    },
    runtime_request_validation=True,
    runtime_response_validation=True,
)
@api_view(["POST"])
@permission_classes([IsAuthenticated])
@validated_api_request(
    request_serializer=DerivedVariablePreviewRequestSerializer,
    responses={
        200: DerivedVariableDetailResponseSerializer,
        **MODEL_HUB_ERROR_RESPONSES,
    },
    reject_unknown_fields=True,
    document=False,
)
def preview_derived_variables(request):
    """
    Preview derived variables from JSON content without saving.

    Useful for showing what variables would be extracted before running.

    Request body:
        - content: JSON string or object to analyze
        - column_name: Name for the variable prefix

    Returns:
        {
            "result": {
                "paths": ["user.name", ...],
                "schema": {...},
                "full_variables": [...]
            }
        }
    """
    try:
        content = request.validated_data.get("content")
        column_name = request.validated_data.get("column_name", "output")

        if not content:
            return _gm.bad_request("Content is required")

        # Extract variables (without saving)
        result = extract_derived_variables_from_output(content, column_name)

        return _gm.success_response(result)

    except Exception as e:
        logger.exception(f"Error previewing derived variables: {e}")
        return _gm.internal_server_error_response("Failed to preview derived variables")


@swagger_auto_schema(
    method="get",
    responses={
        200: DatasetDerivedVariablesResponseSerializer,
        **MODEL_HUB_ERROR_RESPONSES,
    },
)
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def get_dataset_derived_variables_view(request, dataset_id):
    """
    Get all derived variables from all run prompt columns in a dataset.

    This aggregates derived variables from run prompt columns that
    produce JSON outputs, making them available for use in other
    prompts, evals, and experiments.

    Path params:
        - dataset_id: UUID of the dataset

    Returns:
        {
            "result": {
                "derived_variables": {
                    "OutputColumn": {
                        "paths": ["user.name", "user.email"],
                        "full_variables": ["OutputColumn.user.name", ...],
                        "schema": {...}
                    },
                    ...
                }
            }
        }
    """
    try:
        result = get_dataset_derived_variables(
            dataset_id,
            getattr(request, "organization", None) or request.user.organization,
        )

        return _gm.success_response(result)

    except Exception as e:
        logger.exception(f"Error getting dataset derived variables: {e}")
        return _gm.internal_server_error_response(
            "Failed to retrieve dataset derived variables"
        )
