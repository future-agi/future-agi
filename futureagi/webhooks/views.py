"""Endpoint routes of the webhooks API (DESIGN.md section 10). Thin: every rule lives in ``webhooks.api_*``.

Each method is declared with ``validated_request``, so the serializer that documents it in OpenAPI is also its runtime
validator, unknown fields are refused, and a refusal is FA's common error envelope with a ``WEBHOOK_*`` code.
"""

from __future__ import annotations

import functools

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from tfc.utils.api_contracts import validated_request
from tfc.utils.api_serializers import EmptyRequestSerializer
from webhooks import api_endpoint_ops as ops
from webhooks import api_endpoints as eps
from webhooks import api_represent as rep
from webhooks import api_responses as r
from webhooks import serializers as s
from webhooks.api_errors import WebhookApiError, error_response, validation_response
from webhooks.api_test import send_test
from webhooks.core.fanout import type_matches
from webhooks.models import Endpoint
from webhooks.permissions import VIEW, caller_of

TAG = ["webhooks"]


def ok(result, code: int = status.HTTP_200_OK) -> Response:
    return Response({"status": True, "result": result}, status=code)


def handled(fn):
    """``WebhookApiError`` becomes its envelope; the caller is resolved once per request."""

    @functools.wraps(fn)
    def wrapper(self, request, *args, **kwargs):
        try:
            return fn(self, request, caller_of(request), *args, **kwargs)
        except WebhookApiError as err:
            return error_response(err)

    return wrapper


def contract(request_serializer=None, *, query=None, responses, **swagger):
    return validated_request(
        request_serializer,
        query_serializer=query,
        responses=responses,
        reject_unknown_fields=True,
        validation_error_response=validation_response,
        tags=TAG,
        **swagger,
    )


class EndpointListView(APIView):
    permission_classes = [IsAuthenticated]

    @contract(query=s.EndpointListQuery, responses={200: r.EndpointListResponse})
    @handled
    def get(self, request, caller):
        q = request.validated_query_data
        qs = Endpoint.objects.filter(
            organization_id=caller.organization_id, deleted=False
        ).exclude(kind="cli_listen")
        if q.get("workspace"):
            caller.require(VIEW, str(q["workspace"]))
            qs = qs.filter(workspace_id=q["workspace"]) | qs.filter(
                workspace__isnull=True
            )
        for k in ("status", "kind"):
            if q.get(k):
                qs = qs.filter(**{k: q[k]})
        rows = [
            ep for ep in qs.order_by("name", "id") if caller.can(VIEW, ep.workspace_id)
        ]
        if q.get("event_type"):
            rows = [
                ep
                for ep in rows
                if any(
                    type_matches(sub.event_type, q["event_type"])
                    or type_matches(q["event_type"], sub.event_type)
                    for sub in ep.subscriptions.all()
                )
            ]
        return ok({"results": [rep.represent(caller, ep) for ep in rows]})

    @contract(s.EndpointCreateRequest, responses={201: r.EndpointCreatedResponse})
    @handled
    def post(self, request, caller):
        ep, secret, warn = eps.create_endpoint(caller, dict(request.validated_data))
        endpoint = rep.represent(caller, ep, detail=True)
        warnings = list(endpoint.get("warnings", []))
        if warn:
            warnings.append(
                {
                    "code": "WEBHOOK_URL_QUERY_SECRET",
                    "message": "Don't put secrets in the URL. Every request is signed; use the signing secret or a "
                    "header.",
                    "params": warn,
                }
            )
        return ok(
            {
                "endpoint": endpoint,
                "secret": secret,
                "warnings": warnings,
            },
            status.HTTP_201_CREATED,
        )


class EndpointDetailView(APIView):
    permission_classes = [IsAuthenticated]

    @contract(responses={200: r.EndpointResponse})
    @handled
    def get(self, request, caller, endpoint_id):
        ep = caller.require_endpoint(endpoint_id, VIEW)
        return ok(rep.represent(caller, ep, detail=True))

    @contract(s.EndpointUpdateRequest, responses={200: r.EndpointResponse})
    @handled
    def patch(self, request, caller, endpoint_id):
        ep = caller.require_endpoint(endpoint_id, VIEW)
        ep = eps.update_endpoint(caller, ep, dict(request.validated_data))
        return ok(rep.represent(caller, ep, detail=True))

    @contract(responses={200: r.DeletedResponse})
    @handled
    def delete(self, request, caller, endpoint_id):
        ep = caller.require_endpoint(endpoint_id, VIEW)
        cancelled = eps.delete_endpoint(caller, ep)
        return ok({"deleted": True, "id": ep.id, "cancelled_deliveries": cancelled})


class EndpointEnableView(APIView):
    permission_classes = [IsAuthenticated]
    turn_on = True

    @contract(s.ToggleRequest, responses={200: r.EndpointResponse})
    @handled
    def post(self, request, caller, endpoint_id):
        ep = caller.require_endpoint(endpoint_id, VIEW)
        ep = ops.set_enabled(
            caller, ep, self.turn_on, request.validated_data.get("reason", "")
        )
        return ok(rep.represent(caller, ep, detail=True))


class EndpointDisableView(EndpointEnableView):
    turn_on = False


class EndpointResumeView(APIView):
    permission_classes = [IsAuthenticated]

    @contract(s.ResumeRequest, responses={200: r.ResumeResponse})
    @handled
    def post(self, request, caller, endpoint_id):
        ep = caller.require_endpoint(endpoint_id, VIEW)
        return ok(ops.resume(caller, ep, request.validated_data["mode"]))


class EndpointRotateView(APIView):
    permission_classes = [IsAuthenticated]

    @contract(s.RotateRequest, responses={200: r.RotateResponse})
    @handled
    def post(self, request, caller, endpoint_id):
        ep = caller.require_endpoint(endpoint_id, VIEW)
        d = request.validated_data
        return ok(
            ops.rotate_secret(caller, ep, d.get("overlap_seconds"), d.get("secret"))
        )


class EndpointRevokePreviousView(APIView):
    permission_classes = [IsAuthenticated]

    @contract(EmptyRequestSerializer, responses={200: r.RevokeResponse})
    @handled
    def post(self, request, caller, endpoint_id):
        ep = caller.require_endpoint(endpoint_id, VIEW)
        return ok(ops.revoke_previous(caller, ep))


class EndpointTestView(APIView):
    permission_classes = [IsAuthenticated]

    @contract(s.TestRequest, responses={200: r.TestResponse, 202: r.TestResponse})
    @handled
    def post(self, request, caller, endpoint_id):
        ep = caller.require_endpoint(endpoint_id, VIEW)
        code, result = send_test(caller, ep, request.validated_data["event_type"])
        return ok(result, code)
