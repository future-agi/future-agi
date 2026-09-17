import traceback

import structlog
from django.db import DatabaseError
from django.http import Http404
from drf_yasg.utils import swagger_auto_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from model_hub.schema.prompt.prompt_metrics import (
    FetchPromptMetricsRequest,
    FetchPromptSpanMetricsRequest,
)
from model_hub.serializers.contracts import (
    MODEL_HUB_ERROR_RESPONSES,
    ModelHubErrorResponseSerializer,
    PromptAggregateMetricsQuerySerializer,
    PromptMetricsEmptyScreenResponseSerializer,
    PromptMetricsResponseSerializer,
    PromptSpanMetricsQuerySerializer,
)
from model_hub.services.prompt_metrics import (
    PROMPT_METRICS_REQUEST_WALL_MS,
    PromptMetricsReadLimitExceeded,
    bounded_prompt_metrics_read,
    fetch_prompt_metrics,
    fetch_prompt_metrics_span_view,
)
from tfc.utils.api_contracts import validated_request
from tfc.utils.general_methods import GeneralMethods
from tracer.services.clickhouse.read_budget import ReadDeadline, ReadDeadlineExceeded

logger = structlog.get_logger(__name__)


class FetchPromptObserveMetricsView(APIView):
    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]

    @validated_request(
        query_serializer=PromptAggregateMetricsQuerySerializer,
        responses={
            200: PromptMetricsResponseSerializer,
            **MODEL_HUB_ERROR_RESPONSES,
            422: ModelHubErrorResponseSerializer,
            503: ModelHubErrorResponseSerializer,
        },
        reject_unknown_fields=True,
    )
    def get(self, request):
        deadline = ReadDeadline.start(PROMPT_METRICS_REQUEST_WALL_MS)
        try:
            query = request.validated_query_data

            request_data = FetchPromptMetricsRequest(
                prompt_template_id=str(query["prompt_template_id"]),
                organization_id=str(
                    (
                        getattr(request, "organization", None)
                        or request.user.organization
                    ).id
                ),
                filters=query["filters"],
                page_number=query["page_number"],
                page_size=query["page_size"],
            )

            with bounded_prompt_metrics_read(deadline):
                response = fetch_prompt_metrics(request_data, deadline=deadline)

            return self._gm.success_response(response)

        except Http404:
            return self._gm.not_found("Prompt template not found")
        except PromptMetricsReadLimitExceeded as exc:
            return self._gm.custom_error_response(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                str(exc),
                code="prompt_metrics_scope_too_wide",
            )
        except (ReadDeadlineExceeded, DatabaseError):
            return self._gm.custom_error_response(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "Prompt metrics are temporarily unavailable. Please retry.",
                code="prompt_metrics_read_unavailable",
            )
        except Exception as e:
            logger.error(f"Error while fetching the prompt-observe metrics: {str(e)}")
            return self._gm.bad_request("Failed to fetch the prompt-observe metrics.")


class FetchPromptMetricsSpanView(APIView):
    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]

    @validated_request(
        query_serializer=PromptSpanMetricsQuerySerializer,
        responses={
            200: PromptMetricsResponseSerializer,
            **MODEL_HUB_ERROR_RESPONSES,
            422: ModelHubErrorResponseSerializer,
            503: ModelHubErrorResponseSerializer,
        },
        reject_unknown_fields=True,
    )
    def get(self, request):
        deadline = ReadDeadline.start(PROMPT_METRICS_REQUEST_WALL_MS)
        try:
            query = request.validated_query_data

            request_data = FetchPromptSpanMetricsRequest(
                prompt_template_id=str(query["prompt_template_id"]),
                organization_id=str(
                    (
                        getattr(request, "organization", None)
                        or request.user.organization
                    ).id
                ),
                filters=query["filters"],
                search_term=query["search_term"],
                page_number=query["page_number"],
                page_size=query["page_size"],
            )

            with bounded_prompt_metrics_read(deadline):
                response = fetch_prompt_metrics_span_view(
                    request_data, deadline=deadline
                )

            return self._gm.success_response(response)

        except Http404:
            return self._gm.not_found("Prompt template not found")
        except PromptMetricsReadLimitExceeded as exc:
            return self._gm.custom_error_response(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                str(exc),
                code="prompt_metrics_scope_too_wide",
            )
        except (ReadDeadlineExceeded, DatabaseError):
            return self._gm.custom_error_response(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "Prompt metrics are temporarily unavailable. Please retry.",
                code="prompt_metrics_read_unavailable",
            )
        except Exception as e:
            traceback.print_exc()
            logger.error(f"Error while fetching the prompt-observe metrics: {str(e)}")
            return self._gm.bad_request("Failed to fetch the prompt-observe metrics.")


class FetchPromptMetricsNullView(APIView):
    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        responses={
            200: PromptMetricsEmptyScreenResponseSerializer,
            **MODEL_HUB_ERROR_RESPONSES,
        }
    )
    def get(self, request):
        try:
            response = {
                "python": """import os
import openai
import opentelemetry
from fi_instrumentation import register, using_prompt_template
from openai import OpenAI
from traceai_openai import OpenAIInstrumentor

# Set up Environment Variables
os.environ["OPENAI_API_KEY"] = "your-openai-api-key"  # pragma: allowlist secret
os.environ["FI_API_KEY"] = "your-futureagi-api-key"  # pragma: allowlist secret
os.environ["FI_SECRET_KEY"] = "your-futureagi-secret-key"  # pragma: allowlist secret

my_first_model = "my first model"

# Setup OTel via our register function
trace_provider = register(
    project_type=ProjectType.EXPERIMENT,
    project_name="Project_name",
    project_version_name="project_version_name",
)
OpenAIInstrumentor().instrument(tracer_provider=trace_provider)

# Setup OpenAI
client = OpenAI()

# Define the prompt template and its variables
prompt_template = "Please describe the weather forecast for {city} on {date}"
prompt_template_variables = {"city": "San Francisco", "date":"March 27"}

# Use the context manager to add template information
with using_prompt_template(
    template=prompt_template,
    variables=prompt_template_variables,
    version="v1.0",
):
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "user",
                "content": prompt_template.format(**prompt_template_variables)
            },
        ]
    )""",
                "typescript": """import { context } from "@opentelemetry/api";
import { register, ProjectType, setPromptTemplate } from "@traceai/fi-core";
import { OpenAIInstrumentation } from "@traceai/fi-openai";
import OpenAI from "openai";


// Use OpenTelemetry context to add template information
const updatedContext = setPromptTemplate(context.active(), {
  template: promptTemplate,
  variables: promptTemplateVariables,
  version: "v1.0",
});

// Execute the OpenAI call within the context
const response = await context.with(updatedContext, async () => {
  return await client.chat.completions.create({
    model: "gpt-4o-mini",
    messages: [
      {
        role: "user",
        content: promptTemplate.replace("{city}", promptTemplateVariables.city)
                              .replace("{date}", promptTemplateVariables.date)
      },
    ],
  });
});

console.log(response);""",
            }
            return self._gm.success_response(response)

        except Exception as e:
            traceback.print_exc()
            logger.error(f"failed to fetch null screen details: {str(e)}")
            return self._gm.bad_request("failed to fetch null screen details.")
