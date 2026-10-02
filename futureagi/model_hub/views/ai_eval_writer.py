"""
AI Eval Prompt Writer endpoint.

POST /model-hub/ai-eval-writer/

Takes a user's brief description of what they want to evaluate and generates
an eval artifact (an instruction prompt, an LLM-as-a-Judge message array, or
test data). The generation logic lives in model_hub.services.ai_eval_writer_service.
"""

import traceback

import structlog
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from model_hub.serializers.contracts import (
    MODEL_HUB_ERROR_RESPONSES,
    AIEvalWriterRequestSerializer,
    AIEvalWriterResponseSerializer,
)
from model_hub.services.ai_eval_writer_service import generate_eval_prompt
from tfc.utils.api_contracts import validated_request
from tfc.utils.general_methods import GeneralMethods

logger = structlog.get_logger(__name__)


class AIEvalWriterView(APIView):
    """
    POST /model-hub/ai-eval-writer/

    Request:
    {
        "description": "check if response matches ground truth",
        "output_format": "prompt" | "messages" | "test_data"  # optional, defaults to "prompt"
    }

    Response: { "status": true, "result": { ... } } where result carries the
    field matching output_format, already parsed/validated backend-side:
      - "prompt"    -> { "prompt": "<instruction text>" }
      - "messages"  -> { "messages": [ { "role", "content" }, ... ] }
      - "test_data" -> { "test_data": { "<var>": "<value>", ... } }
    """

    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]

    @validated_request(
        request_serializer=AIEvalWriterRequestSerializer,
        responses={200: AIEvalWriterResponseSerializer, **MODEL_HUB_ERROR_RESPONSES},
        reject_unknown_fields=True,
    )
    def post(self, request, *args, **kwargs):
        try:
            result = generate_eval_prompt(
                description=request.validated_data.get("description", ""),
                output_format=request.validated_data.get("output_format", "prompt"),
            )
            return self._gm.success_response(result)

        except ValueError as e:
            # Bad input (blank description / unknown output_format) — 400.
            return self._gm.bad_request(str(e))
        except Exception as e:
            # Upstream/LLM/gateway failure — not the client's fault, so 5xx so
            # monitoring and callers can tell "bad input" from "model is down".
            logger.error(
                f"Error in AIEvalWriterView: {str(e)}\n{traceback.format_exc()}"
            )
            return self._gm.internal_server_error_response(
                f"AI eval writer error: {str(e)}"
            )
