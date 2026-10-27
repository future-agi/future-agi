"""
Quick Analysis — lightweight single-shot LLM endpoint for Imagine views.

Used by dynamicAnalysis widgets to re-generate trace analysis
when a saved view is opened on a new trace. No agent loop,
no tool calls — just prompt + context → text response.
"""

import asyncio

import structlog
from drf_yasg.utils import swagger_auto_schema
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from ee.falcon_ai.llm_client import FalconLLMClient
from ee.falcon_ai.serializers_contracts import (
    FalconErrorResponseSerializer,
    QuickAnalysisResponseSerializer,
)
from tfc.utils.general_methods import GeneralMethods

logger = structlog.get_logger(__name__)
_gm = GeneralMethods()


class QuickAnalysisSerializer(serializers.Serializer):
    prompt = serializers.CharField(max_length=8000)


async def _run_analysis(prompt):
    """Run a single-shot LLM completion and collect the streamed response.

    The LLM client yields OpenAI-format chunks:
    {"choices": [{"delta": {"content": "..."}, "finish_reason": None}]}
    """
    client = FalconLLMClient()
    messages = [{"role": "user", "content": prompt}]

    content = ""
    async for chunk in client.stream_completion(messages, tools=None):
        choices = chunk.get("choices", [])
        if not choices:
            continue
        delta = choices[0].get("delta", {})
        text = delta.get("content", "")
        if text:
            content += text
        finish = choices[0].get("finish_reason")
        if finish == "stop":
            break

    return content


class QuickAnalysisView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        request_body=QuickAnalysisSerializer,
        responses={
            200: QuickAnalysisResponseSerializer,
            429: FalconErrorResponseSerializer,
            502: FalconErrorResponseSerializer,
            504: FalconErrorResponseSerializer,
        },
    )
    def post(self, request):
        serializer = QuickAnalysisSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        prompt = serializer.validated_data["prompt"]

        try:
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor() as pool:
                content = pool.submit(asyncio.run, _run_analysis(prompt)).result(
                    timeout=45
                )

            return Response({"status": True, "result": content})

        except concurrent.futures.TimeoutError:
            logger.error("quick_analysis_timeout")
            return _gm.custom_error_response(
                status.HTTP_504_GATEWAY_TIMEOUT,
                "Analysis timed out. Please retry.",
            )
        except Exception as e:
            error_msg = str(e)
            logger.error("quick_analysis_error", error=error_msg)
            is_rate_limit = "Too many" in error_msg or "ServiceUnavailable" in error_msg
            return _gm.custom_error_response(
                (
                    status.HTTP_429_TOO_MANY_REQUESTS
                    if is_rate_limit
                    else status.HTTP_502_BAD_GATEWAY
                ),
                error_msg,
            )
