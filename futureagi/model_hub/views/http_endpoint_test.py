"""Real, bounded HTTP source tests for the configuration card.

The caller uses requestModel.buildRequest (also used by row execution) and
applies transformModel.applyTransform to the returned JSON. This view never
re-interpolates row data or manufactures an endpoint response. The transport
function is reusable by a row executor with an explicit scoped secret resolver.
"""

import json
import re
from time import perf_counter
from urllib.parse import urlsplit

from requests.structures import CaseInsensitiveDict
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from model_hub.models.api_key import SecretModel
from tfc.utils.api_contracts import validated_request
from tfc.utils import outbound_destination
from model_hub.services.http_experiment_source import redact_secrets
from tfc.utils.tenant_scope import scope_to_caller

MAX_RESPONSE_BYTES = 2 * 1024 * 1024
TIMEOUT_SECONDS = 15.0


class EndpointConfigurationError(ValueError):
    """An actionable configuration problem, with no credential values."""


def _header_value(value):
    if not isinstance(value, str) or "\r" in value or "\n" in value:
        raise EndpointConfigurationError("Header values must be single-line text.")
    try:
        value.encode("ascii")
    except UnicodeEncodeError as exc:
        raise EndpointConfigurationError("Header values must use ASCII characters.") from exc
    return value


class HttpEndpointHeader(serializers.Serializer):
    name = serializers.RegexField(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$", max_length=256)
    value = serializers.CharField(
        required=False, default="", allow_blank=True, trim_whitespace=False,
        max_length=16384,
    )
    secret_ref = serializers.CharField(required=False, max_length=255)
    scheme = serializers.ChoiceField(
        choices=["bearer"], required=False,
        help_text="bearer sends the secret's value as 'Bearer <value>'. Needs secret_ref.",
    )

    def validate(self, data):
        if data.get("secret_ref") and data.get("value"):
            raise serializers.ValidationError("Choose a secret reference or a literal value.")
        if data.get("scheme") and not data.get("secret_ref"):
            raise serializers.ValidationError("A bearer header takes its token from a workspace secret. Pick the secret.")
        try:
            _header_value(data["value"])
        except EndpointConfigurationError as exc:
            raise serializers.ValidationError(str(exc)) from exc
        return data


class HttpPreparedRequest(serializers.Serializer):
    url = serializers.CharField(max_length=8192)
    method = serializers.ChoiceField(choices=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
    headers = HttpEndpointHeader(many=True, max_length=64)
    body = serializers.CharField(
        allow_blank=True, trim_whitespace=False, max_length=2 * 1024 * 1024,
    )

    def validate_url(self, value):
        try:
            parsed = urlsplit(value)
            valid = (
                parsed.scheme in ("https", "http") and parsed.hostname
                and not parsed.username and not parsed.password and not parsed.fragment
            )
            parsed.port  # Reject malformed ports before attempting a connection.
        except ValueError:
            valid = False
        if not valid:
            raise serializers.ValidationError("Use a public HTTP(S) URL without credentials or fragments.")
        return value

    def validate(self, data):
        if data["method"] in ("GET", "HEAD") and data["body"]:
            raise serializers.ValidationError("GET and HEAD requests must have an empty body.")
        return data


class HttpEndpointTestRequest(serializers.Serializer):
    prepared_request = HttpPreparedRequest(
        help_text="URL, method, header references and exact body bytes from requestModel.buildRequest."
    )
    # Keep the existing card contract for saved settings/preview clients. These
    # fields are context only; prepared_request is the sole outbound request.
    url = serializers.CharField(required=False, max_length=8192)
    method = serializers.CharField(required=False, max_length=16)
    headers = serializers.ListField(child=serializers.JSONField(), required=False, max_length=64)
    body_template = serializers.CharField(required=False, allow_blank=True, trim_whitespace=False)
    output_path = serializers.CharField(required=False, allow_blank=True)
    transform = serializers.JSONField(required=False, allow_null=True)
    mapping = serializers.DictField(required=False)
    dataset_id = serializers.UUIDField(required=False, allow_null=True)
    row_id = serializers.UUIDField(required=False, allow_null=True)
    trace_context = serializers.JSONField(required=False, allow_null=True)


class HttpEndpointDisplayedRequest(serializers.Serializer):
    url = serializers.CharField()
    method = serializers.CharField()
    headers = serializers.DictField(child=serializers.CharField(allow_blank=True))
    body = serializers.CharField(allow_blank=True, trim_whitespace=False)


class HttpEndpointTestResult(serializers.Serializer):
    simulated = serializers.BooleanField()
    provenance = serializers.ChoiceField(choices=["upstream"])
    host = serializers.CharField()
    status = serializers.IntegerField(allow_null=True)
    latency_ms = serializers.FloatField()
    request = HttpEndpointDisplayedRequest()
    traceparent = serializers.CharField(allow_null=True, allow_blank=True)
    echoed = serializers.CharField(allow_null=True)
    response = serializers.JSONField(allow_null=True)
    response_is_json = serializers.BooleanField()
    error = serializers.CharField(allow_null=True)


class HttpEndpointTestResponse(serializers.Serializer):
    status = serializers.BooleanField()
    result = HttpEndpointTestResult()


def execute_endpoint_request(prepared, *, resolve_secret):
    """Send validated builder output once; secret authority is caller-supplied.

    No automatic retries: testing a POST may have user-visible side effects.
    The existing transport pins public addresses, verifies TLS and refuses
    redirects. No ambient server credentials are forwarded to the endpoint.
    """
    headers = CaseInsensitiveDict({"Content-Type": "application/json"})
    displayed = {"Content-Type": "application/json"}
    secrets = []
    for header in prepared["headers"]:
        name = header["name"]
        ref = header.get("secret_ref")
        value = resolve_secret(ref) if ref else header.get("value", "")
        bearer = "Bearer " if ref and header.get("scheme") == "bearer" else ""
        headers[name] = _header_value(bearer + value)
        displayed[name] = f"{bearer}•••• (from secret {ref})" if ref else value
        if ref and value:
            secrets.append(value)
    result = {
        "simulated": False,
        "provenance": "upstream",
        "host": urlsplit(prepared["url"]).hostname,
        "status": None,
        "latency_ms": 0.0,
        "request": {
            "url": prepared["url"], "method": prepared["method"],
            "headers": displayed, "body": prepared["body"],
        },
        "traceparent": headers.get("traceparent"),
        "echoed": None,
        "response": None,
        "response_is_json": False,
        "error": None,
    }
    started = perf_counter()
    try:
        response = outbound_destination.request(
            method=prepared["method"], url=prepared["url"], headers=dict(headers),
            data=prepared["body"] or None,
            timeout=TIMEOUT_SECONDS, max_bytes=MAX_RESPONSE_BYTES,
        )
        result["status"] = response.status_code
        if not 200 <= response.status_code < 300:
            result["error"] = (
                f"Endpoint returned HTTP {response.status_code}. "
                "Check the URL, method and header settings, then retry."
            )
        try:
            result["response"] = json.loads(response.content)
            result["response_is_json"] = True
        except (ValueError, UnicodeError):
            result["response"] = response.content.decode("utf-8", errors="replace")
            result["error"] = result["error"] or "The response is not JSON. Return JSON or check the endpoint URL."
        echoed = response.headers.get("traceparent")
        if not echoed and isinstance(result["response"], dict):
            echoed = result["response"].get("traceparent")
        if isinstance(echoed, str) and re.fullmatch(r"00-[0-9a-f]{32}-[0-9a-f]{16}-[0-9a-f]{2}", echoed):
            result["echoed"] = echoed
    except outbound_destination.DestinationError as exc:
        # Only fixed local reason codes are inspected; exception text is never
        # included in a response or log. The child has a wall-clock deadline
        # covering DNS, TLS and the entire body, not a per-chunk read timeout.
        reason = str(exc)
        if reason in {"deadline", "TimeoutError", "ReadTimeout"}:
            result["error"] = "No answer from the endpoint in 15 s. Check whether it processed the request before retrying."
        elif reason == "redirect":
            result["error"] = "Endpoint returned a redirect. Use its final HTTPS URL and retry."
        elif reason == "body_limit":
            result["error"] = "Endpoint response is too large. Return a smaller JSON response and retry."
        elif reason in {"dns_empty", "gaierror"}:
            result["error"] = "Couldn't resolve the endpoint host. Check the URL and retry."
        elif reason == "address_class" or reason.startswith("url_"):
            result["error"] = "Use a public HTTP(S) endpoint. Private and local hosts cannot be called from Future AGI."
        else:
            result["error"] = "Couldn't reach the endpoint. Check the URL, TLS certificate and server availability, then retry."
    finally:
        result["latency_ms"] = max(0.0, (perf_counter() - started) * 1000)
    # Upstream JSON can echo a header as a number, boolean or null. Apply
    # scalar matching only to that payload, never to our transport metadata.
    if result["response_is_json"]:
        result["response"] = redact_secrets(
            result["response"], secrets, redact_json_scalars=True,
        )
    return redact_secrets(result, secrets)


class HttpEndpointTestView(APIView):
    permission_classes = [IsAuthenticated]

    @validated_request(
        request_serializer=HttpEndpointTestRequest,
        responses={200: HttpEndpointTestResponse},
        reject_unknown_fields=True,
    )
    def post(self, request):
        if getattr(request, "organization", None) is None or getattr(request, "workspace", None) is None:
            return Response({"status": False, "result": "Select a workspace and test again."}, status=400)
        # The same visibility rule as the workspace secret picker, including
        # legacy secrets in the default workspace; never a global name lookup.
        available = scope_to_caller(
            SecretModel.no_workspace_objects.filter(deleted=False), request,
        )

        def resolve_secret(name):
            matches = list(available.filter(name=name)[:2])
            if not matches:
                raise EndpointConfigurationError(f"No workspace secret named {name}. Pick one from the list.")
            if len(matches) != 1:
                raise EndpointConfigurationError(f"More than one workspace secret named {name}. Give it a unique name and retry.")
            try:
                value = matches[0].actual_key
            except ValueError as exc:
                raise EndpointConfigurationError(f"Workspace secret {name} is unavailable. Update it and retry.") from exc
            if not value:
                raise EndpointConfigurationError(f"Workspace secret {name} is empty. Update it and retry.")
            return value

        try:
            result = execute_endpoint_request(
                request.validated_data["prepared_request"], resolve_secret=resolve_secret,
            )
        except EndpointConfigurationError as exc:
            return Response({"status": False, "result": str(exc)}, status=400)
        return Response({"status": True, "result": result})
