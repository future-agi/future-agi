import traceback

import structlog
from django.http import Http404
from drf_yasg.utils import swagger_auto_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from model_hub.schema.prompt.prompt_metrics import FetchPromptMetricsRequest
from model_hub.serializers.contracts import (
    MODEL_HUB_ERROR_RESPONSES,
    PromptMetricsEmptyScreenResponseSerializer,
    PromptMetricsQuerySerializer,
    PromptMetricsResponseSerializer,
)
from model_hub.services.prompt_metrics import (
    fetch_prompt_metrics,
    fetch_prompt_metrics_span_view,
)
from tfc.utils.api_contracts import validated_request
from tfc.utils.general_methods import GeneralMethods

logger = structlog.get_logger(__name__)


class FetchPromptObserveMetricsView(APIView):
    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]

    @validated_request(
        query_serializer=PromptMetricsQuerySerializer,
        responses={200: PromptMetricsResponseSerializer, **MODEL_HUB_ERROR_RESPONSES},
        reject_unknown_fields=True,
    )
    def get(self, request):
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

            response = fetch_prompt_metrics(request_data)

            return self._gm.success_response(response)

        except Http404:
            return self._gm.not_found("Prompt template not found")
        except Exception as e:
            logger.error(f"Error while fetching the prompt-observe metrics: {str(e)}")
            return self._gm.bad_request("Failed to fetch the prompt-observe metrics.")


class FetchPromptMetricsSpanView(APIView):
    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]

    @validated_request(
        query_serializer=PromptMetricsQuerySerializer,
        responses={200: PromptMetricsResponseSerializer, **MODEL_HUB_ERROR_RESPONSES},
        reject_unknown_fields=True,
    )
    def get(self, request):
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
                search_term=query["search_term"],
                page_number=query["page_number"],
                page_size=query["page_size"],
            )

            response = fetch_prompt_metrics_span_view(request_data)

            return self._gm.success_response(response)

        except Http404:
            return self._gm.not_found("Prompt template not found")
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
