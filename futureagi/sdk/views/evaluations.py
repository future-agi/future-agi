import uuid

import structlog
from django.core.exceptions import ValidationError
from django.db.models import Q
from rest_framework.parsers import JSONParser
from rest_framework.renderers import JSONRenderer
from rest_framework.views import APIView

from accounts.authentication import APIKeyAuthentication

logger = structlog.get_logger(__name__)
from agentic_eval.core_evals.fi_evals import *  # noqa: F403
from analytics.utils import (
    MixpanelEvents,
    MixpanelSources,
    MixpanelTypes,
    get_mixpanel_properties,
    track_mixpanel_event,
)
from model_hub.models.error_localizer_model import ErrorLocalizerStatus
from model_hub.models.evals_metric import EvalTemplate
from model_hub.models.evaluation import Evaluation
from sdk.serializers.evaluations import ConfigureEvaluationsSerializer
from sdk.utils.async_evaluations import _handle_async_eval
from sdk.utils.evaluations import (
    StandaloneEvaluationError,
    _run_batched_standalone_eval,
    _run_protect,
    _run_standalone_eval,
)
from tfc.utils.error_codes import get_error_message
from tfc.utils.general_methods import GeneralMethods
from tracer.models.external_eval_config import (
    ExternalEvalConfig,
    StatusChoices,
)


# Define a Choices class
class RequiredKeys:
    TEXT = "text"
    RESPONSE = "response"
    QUERY = "query"
    CONTEXT = "context"
    EXPECTED_RESPONSE = "expected_response"
    DOCUMENT = "document"
    INPUT = "input"
    OUTPUT = "output"
    PROMPT = "prompt"
    CRITERIA = "criteria"
    IMAGE_URL = "image_url"
    INPUT_IMAGE_URL = "input_image_url"
    OUTPUT_IMAGE_URL = "output_image_url"
    ACTUAL_JSON = "actual_json"
    EXPECTED_JSON = "expected_json"


class ModelConfigDescription:
    TEXT = "text"
    RESPONSE = "response"
    QUERY = "query"
    CONTEXT = "context"
    MODEL = "model"
    EVAL_PROMPT = "eval_prompt"
    SYSTEM_PROMPT = "system_prompt"
    EXPECTED_RESPONSE = "expected_response"
    DOCUMENT = "document"
    GRADING_CRITERIA = "grading_criteria"
    SCHEMA = "schema"
    PATTERN = "pattern"
    SUBSTRING = "substring"
    CASE_SENSITIVE = "case_sensitive"
    KEYWORDS = "keywords"
    MAX_LENGTH = "max_length"
    MIN_LENGTH = "min_length"
    URL = "url"
    PAYLOAD = "payload"
    HEADERS = "headers"
    CODE = "code"
    CHOICES = "choices"
    RULE_PROMPT = "rule_prompt"
    INPUT = "input"
    MULTI_CHOICE = "multi_choice"
    VALIDATIONS = "validations"
    COMPARATOR = "comparator"
    FAILURE_THRESHOLD = "failure_threshold"
    OWNER = "owner"  # Added owner key
    ORGANIZATION = "organization"


class GetEvalStructureEvalIdView(APIView):
    _gm = GeneralMethods()
    authentication_classes = [APIKeyAuthentication]
    parser_classes = (JSONParser,)
    renderer_classes = (JSONRenderer,)

    def get_renderer_context(self):
        context = super().get_renderer_context()
        context["json_underscoreize"] = False
        return context

    def get(self, request, eval_id, *args, **kwargs):
        try:
            template = EvalTemplate.no_workspace_objects.get(eval_id=eval_id)
            return self._gm.success_response(
                {
                    "id": template.id,
                    "name": template.name,
                    "description": template.description,
                    "organization": template.organization_id,
                    "owner": template.owner,
                    "eval_tags": template.eval_tags,
                    "config": template.config,
                    "eval_id": template.eval_id,
                    "criteria": template.criteria,
                    "choices": template.choices,
                    "multi_choice": template.multi_choice,
                }
            )
        except EvalTemplate.DoesNotExist:
            return self._gm.bad_request(get_error_message("MISSING_EVAL_TEMPLATE"))


class StandaloneEvalView(APIView):
    _gm = GeneralMethods()
    authentication_classes = [APIKeyAuthentication]
    parser_classes = (JSONParser,)

    def post(self, request, *args, **kwargs):
        try:
            sdk_uuid = str(uuid.uuid4())
            unprocessed_inputs = request.data.get("inputs", [])
            if not unprocessed_inputs:
                return self._gm.bad_request("inputs is required and cannot be empty.")

            first_input = unprocessed_inputs[0]
            input_text = first_input.get("input", "")

            inputs = {"input": input_text}
            # Forward max_tokens from guardrail config if present
            if "max_tokens" in first_input:
                inputs["max_tokens"] = first_input["max_tokens"]
            config = request.data.get("config", {})
            protect_flash = request.data.get("protect_flash", False)

            if not config:
                return self._gm.bad_request("config is required.")

            eval_id = None
            new_config = None
            for key, value in config.items():
                try:
                    eval_id = int(key)
                except ValueError:
                    return self._gm.bad_request(
                        f"Invalid eval_id: {key}. Must be a numeric value."
                    )
                new_config = value

            output_entries = [{"evaluations": []}]
            properties = get_mixpanel_properties(
                user=request.user,
                count=1,
                eval_id=eval_id,
                source=MixpanelSources.SDK.value,
            )
            track_mixpanel_event(MixpanelEvents.EVAL_RUN_STARTED.value, properties)
            response = _run_protect(
                inputs,
                new_config,
                protect_flash,
                eval_id,
                request.user,
                sdk_uuid,
                workspace=request.workspace,
            )
            output_entries[0]["evaluations"].append(response)
            properties = get_mixpanel_properties(
                user=request.user,
                count=len(output_entries[0].get("evaluations", [])),
                eval_id=eval_id,
                source=MixpanelSources.SDK.value,
            )
            track_mixpanel_event(MixpanelEvents.EVAL_RUN_COMPLETED.value, properties)

            logger.info("Successfully completed all evaluations")
            return self._gm.success_response(output_entries)

        except Exception as e:
            logger.exception(f"Error in running standalone eval: {str(e)}")
            return self._gm.internal_server_error_response(
                get_error_message("FAILED_TO_RUN_STANDALONE_EVAL")
            )


class StandaloneEvalView_v2(APIView):
    _gm = GeneralMethods()
    authentication_classes = [APIKeyAuthentication]
    parser_classes = (JSONParser,)

    def get(self, request, *args, **kwargs):
        try:
            eval_id = request.query_params.get("eval_id")
            if not eval_id:
                return self._gm.bad_request("eval_id is required.")

            evaluation = Evaluation.objects.select_related(
                "eval_template", "error_localizer"
            ).get(
                id=eval_id,
                organization=getattr(request, "organization", None)
                or request.user.organization,
            )

            if evaluation.status in [
                StatusChoices.PENDING,
                StatusChoices.PROCESSING,
            ]:
                return self._gm.success_response(
                    {
                        "eval_status": evaluation.status,
                        "result": "Evaluation is being processed.",
                    }
                )

            response_data = {
                "eval_status": evaluation.status,
                "result": {
                    "eval_id": evaluation.id,
                    "name": evaluation.eval_template.name,
                    "input_data": evaluation.input_data,
                    "reason": evaluation.reason,
                    "runtime": evaluation.runtime,
                    "model": evaluation.model_name,
                    "output_type": evaluation.output_type,
                    "value": evaluation.value,
                    "error_localizer_enabled": evaluation.error_localizer_enabled,
                },
            }

            if evaluation.error_localizer_enabled and evaluation.error_localizer:
                response_data["result"]["error_localizer"] = {
                    "error_analysis": evaluation.error_localizer.error_analysis,
                    "selected_input_key": evaluation.error_localizer.selected_input_key,
                }

                if evaluation.error_localizer.status == ErrorLocalizerStatus.FAILED:
                    response_data["result"]["error_localizer"][
                        "error_message"
                    ] = evaluation.error_localizer.error_message

            if evaluation.status == StatusChoices.FAILED:
                response_data["result"]["error_message"] = evaluation.error_message

            return self._gm.success_response(response_data)

        except Evaluation.DoesNotExist:
            return self._gm.bad_request(get_error_message("EVALUATION_NOT_FOUND"))

        except Exception as e:
            logger.exception(f"Error in getting eval results: {str(e)}")
            return self._gm.internal_server_error_response(
                get_error_message("FAILED_TO_GET_EVAL_RESULTS")
            )

    def post(self, request, *args, **kwargs):
        try:
            eval_name = request.data.get("eval_name")
            inputs = request.data.get(
                "inputs", {}
            )  # Ensure inputs is a dict, default to empty
            model = request.data.get("model", None)
            span_id = request.data.get("span_id", None)
            custom_eval_name = request.data.get("custom_eval_name", None)
            trace_eval = request.data.get("trace_eval", False)
            is_async = request.data.get("is_async", False)
            error_localizer_enabled = request.data.get("error_localizer", False)
            organization = (
                getattr(request, "organization", None) or request.user.organization
            )

            eval_config = {
                "eval_templates": eval_name,
                "inputs": inputs,
                "model_name": model,
                "config": request.data.get("config", {}),
            }

            serializer = ConfigureEvaluationsSerializer(
                data=eval_config, context={"request": request}
            )
            if not serializer.is_valid():
                return self._gm.bad_request(serializer.errors)

            eval_template = EvalTemplate.no_workspace_objects.get(
                Q(name=eval_name)
                & (
                    Q(organization=organization, workspace=request.workspace)
                    | Q(organization__isnull=True)
                )
            )

            is_batched = False
            if inputs and isinstance(list(inputs.values())[0], list):
                is_batched = True
                properties = get_mixpanel_properties(
                    user=request.user,
                    type=MixpanelTypes.OBSERVE.value,
                    model=model,
                    span_id=span_id,
                    eval=eval_template,
                    count=len(list(inputs.values())[0]),
                )
                track_mixpanel_event(MixpanelEvents.EVAL_RUN_STARTED.value, properties)

            if is_async:
                result = _handle_async_eval(
                    eval_template=eval_template,
                    inputs=inputs,
                    model=model,
                    user=request.user,
                    workspace=request.workspace,
                    span_id=span_id,
                    custom_eval_name=custom_eval_name,
                    trace_eval=trace_eval,
                    is_batched=is_batched,
                    eval_config=serializer.validated_data.get("config", {}),
                    error_localizer_enabled=error_localizer_enabled,
                )
                return self._gm.success_response(result)

            if is_batched:
                results = _run_batched_standalone_eval(
                    eval_template=eval_template,
                    inputs=inputs,
                    model=model,
                    user=request.user,
                    workspace=request.workspace,
                    eval_config=serializer.validated_data.get("config", {}),
                    error_localizer_enabled=error_localizer_enabled,
                    trace_eval=trace_eval,
                    custom_eval_name=custom_eval_name,
                    span_id=span_id,
                )
                output_entries = [{"evaluations": results}]

            else:
                properties = get_mixpanel_properties(
                    user=request.user,
                    type=MixpanelTypes.OBSERVE.value,
                    model=model,
                    span_id=span_id,
                    eval=eval_template,
                    count=1,
                )
                track_mixpanel_event(MixpanelEvents.EVAL_RUN_STARTED.value, properties)
                result = _run_standalone_eval(
                    eval_template=eval_template,
                    inputs=inputs,
                    model=model,
                    user=request.user,
                    workspace=request.workspace,
                    eval_config=serializer.validated_data.get("config", {}),
                    error_localizer_enabled=error_localizer_enabled,
                    trace_eval=trace_eval,
                    custom_eval_name=custom_eval_name,
                    span_id=span_id,
                )
                output_entries = [{"evaluations": [result]}]

            properties = get_mixpanel_properties(
                user=request.user,
                type=MixpanelTypes.OBSERVE.value,
                model=model,
                span_id=span_id,
                eval=eval_template,
                count=len(output_entries[0].get("evaluations", [])),
            )
            track_mixpanel_event(MixpanelEvents.EVAL_RUN_COMPLETED.value, properties)

            return self._gm.success_response(output_entries)

        except StandaloneEvaluationError as e:
            return self._gm.bad_request(str(e))

        except Exception as e:
            logger.exception(f"Error in running standalone eval: {str(e)}")
            return self._gm.internal_server_error_response(
                get_error_message("FAILED_TO_RUN_STANDALONE_EVAL")
            )


class GetEvalsView(APIView):
    _gm = GeneralMethods()
    authentication_classes = [APIKeyAuthentication]
    parser_classes = (JSONParser,)

    def get(self, request, *args, **kwargs):
        try:
            # Filter to show only system evals and user's organization evals
            organization = (
                getattr(request, "organization", None) or request.user.organization
            )
            evals = EvalTemplate.no_workspace_objects.filter(
                Q(organization=organization, workspace=request.workspace)
                | Q(organization__isnull=True)
            ).values(
                "id",
                "name",
                "description",
                "eval_id",
                "eval_tags",
                "config",
                "criteria",
                "choices",
                "multi_choice",
                "owner",
            )

            eval_list = [
                {
                    "id": str(eval["id"]),
                    "name": eval["name"],
                    "description": eval["description"],
                    "eval_id": eval["eval_id"],
                    "eval_tags": eval["eval_tags"],
                    "config": eval["config"],
                    "criteria": eval["criteria"],
                    "choices": eval["choices"],
                    "multi_choice": eval["multi_choice"],
                    "owner": eval["owner"],
                }
                for eval in evals
            ]

            return self._gm.success_response(eval_list)
        except Exception as e:
            logger.exception(f"Error in getting evals: {str(e)}")
            return self._gm.internal_server_error_response(
                get_error_message("FAILED_TO_GET_EVALS")
            )


class ConfigureEvaluationsView(APIView):
    _gm = GeneralMethods()
    authentication_classes = [APIKeyAuthentication]
    parser_classes = (JSONParser,)

    def post(self, request, *args, **kwargs):
        try:
            user_organization = (
                getattr(request, "organization", None) or request.user.organization
            )
            eval_config = request.data.get("eval_config")
            platform = request.data.get("platform")
            custom_eval_name = request.data.get("custom_eval_name")

            credentials = request.data.copy()
            credentials.pop("eval_config", None)
            credentials.pop("platform", None)
            credentials.pop("custom_eval_name", None)

            if not eval_config or not platform:
                return self._gm.bad_request("eval_config and platform are required.")

            serializer = ConfigureEvaluationsSerializer(
                data=eval_config, context={"request": request}
            )
            if not serializer.is_valid():
                return self._gm.bad_request(serializer.errors)

            eval_template = EvalTemplate.objects.get(
                name=serializer.validated_data.get("eval_templates")
            )

            ExternalEvalConfig.objects.create(
                organization=user_organization,
                eval_template=eval_template,
                name=custom_eval_name,
                config=serializer.validated_data.get("config", {}),
                mapping=serializer.validated_data.get("inputs", {}),
                model=serializer.validated_data.get("model_name"),
                platform=platform,
                credentials=credentials,
                status=StatusChoices.PENDING,
            )

            return self._gm.success_response({"message": "Configuration is valid."})

        except ValidationError as e:
            return self._gm.bad_request(e.message_dict)

        except Exception as e:
            logger.exception(f"Error in configuring evaluations: {str(e)}")
            return self._gm.internal_server_error_response(
                "Failed to configure evaluations."
            )
