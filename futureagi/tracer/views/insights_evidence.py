"""
Insights evidence broker: the Insights agent reads a run's units here.

POST /tracer/internal/insights/evidence/manifest   {unit}                    -> {manifest}
POST /tracer/internal/insights/evidence/fields     {unit, items, max_bytes}  -> {fields}
POST /tracer/internal/insights/evidence/media      {unit, media}             -> bytes

Internal endpoints for the agent service only (PROTOCOL_V2 section 3), served
by the web pods, never by the Temporal worker waiting on the agent. They take
no session or API key: the bearer token is a capability Django signed for one
agent call of one run (``ee.agenthub.insights.broker``), checked again on
every call with the run itself. They serve the snapshots the worker stored
before the agent was asked (``ee.agenthub.insights.evidence_store``). Errors
are ``{"error": {"code": ...}}`` and nothing else: no request content is ever
echoed back.
"""

import json

import structlog
from django.core.exceptions import RequestDataTooBig
from django.http import HttpResponse, JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

logger = structlog.get_logger(__name__)

# A request names a unit and at most 2,000 field pairs.
MAX_REQUEST_BYTES = 512 * 1024


def _error(status: int, code: str) -> JsonResponse:
    response = JsonResponse({"error": {"code": code}}, status=status)
    if code == "broker_busy":
        # The agent asks again after this many seconds (PROTOCOL_V2 change 24).
        response["Retry-After"] = "1"
    return response


@method_decorator(csrf_exempt, name="dispatch")
class InsightEvidenceView(View):
    http_method_names = ["post"]
    op = ""

    def post(self, request):
        try:
            from ee.agenthub.insights import broker
        except ImportError:
            return _error(404, "not_found")
        # Expired and ended runs' snapshots go even when no write follows.
        broker.sweep()
        token = broker.bearer(request.headers.get("Authorization"))
        try:
            claims = broker.verify(token)
            body = self._body(request)
            run, row = broker.authorize(claims, self.op, body.get("unit"))
            remaining = broker.take_call(token, claims)
            cached = broker.snapshot(run, row)
            if self.op == "media":
                return self._media(broker, token, claims, run, cached, body, remaining)
            if self.op == "fields":
                content = broker.fields_body(
                    cached, body.get("items"), body.get("max_bytes"), remaining
                )
            else:
                content = broker.manifest_body(cached, body["unit"])
        except broker.BrokerError as refused:
            return _error(refused.status, refused.code)
        except Exception:
            logger.exception("insight_evidence_failed", op=self.op)
            return _error(500, "internal_error")
        broker.spend_bytes(token, claims, len(content))
        return HttpResponse(content, content_type="application/json")

    @staticmethod
    def _body(request) -> dict:
        from ee.agenthub.insights.broker import BrokerError

        try:
            raw = request.body
        except RequestDataTooBig:
            raise BrokerError(413, "request_too_large") from None
        if len(raw) > MAX_REQUEST_BYTES:
            raise BrokerError(413, "request_too_large")
        try:
            body = json.loads(raw)
        except ValueError:
            body = None
        if not isinstance(body, dict):
            raise BrokerError(400, "invalid_request")
        return body

    @staticmethod
    def _media(broker, token, claims, run, cached, body, remaining) -> HttpResponse:
        item = broker.media(cached, body.get("media"), project_id=str(run.project_id))
        if len(item.content) > remaining:
            raise broker.BrokerError(429, "capability_budget_spent")
        broker.spend_bytes(token, claims, len(item.content))
        response = HttpResponse(item.content, content_type=item.mime)
        if item.original:
            response["x-insights-media-original"] = "{}x{}".format(*item.original)
        response["Cache-Control"] = "no-store"
        return response
