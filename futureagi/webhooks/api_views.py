"""Delivery-log, events, catalog, egress, preview and policy routes of the webhooks API (DESIGN.md section 10)."""

from __future__ import annotations

import functools
import json
import os
from pathlib import Path

from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from tfc.utils.api_serializers import EmptyRequestSerializer
from webhooks import api_activity, api_policy, config
from webhooks import api_deliveries as dl
from webhooks import api_events as ev
from webhooks import api_responses as r
from webhooks import serializers as s
from webhooks.api_test import preview
from webhooks.api_validation import FILTER_KEYS
from webhooks.core.fanout import CATALOG, GROUPS
from webhooks.permissions import VIEW, VIEW_LOG
from webhooks.views import contract, handled, ok

SCHEMAS = (
    Path(__file__).resolve().parent / "core" / "conformance" / "schemas" / "events"
)
DESCRIPTIONS = {
    "prompt.version.created": "A new prompt version is committed (drafts never fire).",
    "prompt.label.changed": "A label is assigned, moved or removed on a prompt's versions.",
    "deployment.run.completed": "A deployment run completes.",
    "deployment.run.failed": "A deployment run fails, is cancelled or is interrupted.",
}


@functools.lru_cache(maxsize=1)
def catalog() -> dict:
    out = []
    for t in CATALOG:
        out.append(
            {
                "type": t,
                "group": t.rsplit(".", 1)[0] + ".*"
                if t.startswith("deployment.")
                else "prompt.*",
                "description": DESCRIPTIONS[t],
                "filters": sorted(FILTER_KEYS[t]),
                "schema": json.loads((SCHEMAS / f"{t}.v1.schema.json").read_text()),
            }
        )
    # The per-type schemas refer to these by $id (urn:futureagi:webhooks:v1:...): a client resolves them from here.
    shared = {}
    for name in ("envelope", "deployment.run"):
        doc = json.loads((SCHEMAS / f"{name}.v1.schema.json").read_text())
        shared[doc["$id"]] = doc
    return {"results": out, "groups": list(GROUPS), "shared_schemas": shared}


class DeliveryListView(APIView):
    permission_classes = [IsAuthenticated]

    @contract(query=s.DeliveryListQuery, responses={200: r.DeliveryPageResponse})
    @handled
    def get(self, request, caller, endpoint_id=None):
        params = dict(request.validated_query_data)
        endpoint = None
        if endpoint_id or params.get("endpoint"):
            endpoint = caller.require_endpoint(
                endpoint_id or params["endpoint"], VIEW_LOG
            )
        return ok(dl.list_deliveries(caller, params, endpoint))


class ActivityView(APIView):
    """This endpoint's audit entries, newest first: who changed what, and when (the endpoint page's Activity card)."""

    permission_classes = [IsAuthenticated]

    @contract(
        query=s.ActivityQuery,
        responses={200: r.ActivityPageResponse},
        operation_id="webhooks_endpoints_activity_list",
    )
    @handled
    def get(self, request, caller, endpoint_id):
        ep = caller.require_endpoint(endpoint_id, VIEW)
        params = dict(request.validated_query_data)
        if params.get("cursor"):
            params["cursor"] = str(params["cursor"])
        return ok(api_activity.list_activity(caller, ep, params))


class DeliveryDetailView(APIView):
    permission_classes = [IsAuthenticated]

    @contract(responses={200: r.DeliveryDetailResponse})
    @handled
    def get(self, request, caller, delivery_id):
        return ok(dl.delivery_detail(caller, dl.get_delivery(caller, delivery_id)))


class RedeliverView(APIView):
    permission_classes = [IsAuthenticated]

    @contract(
        EmptyRequestSerializer,
        responses={202: r.RedeliverResponse},
        operation_id="webhooks_deliveries_redeliver_one",
    )
    @handled
    def post(self, request, caller, delivery_id):
        return ok(dl.redeliver_one(caller, delivery_id), 202)


class BulkRedeliverView(APIView):
    permission_classes = [IsAuthenticated]

    @contract(
        s.BulkRedeliverRequest,
        responses={200: r.BulkRedeliverResponse},
        operation_id="webhooks_deliveries_redeliver_bulk",
    )
    @handled
    def post(self, request, caller):
        return ok(dl.redeliver_bulk(caller, dict(request.validated_data)))


class EventListView(APIView):
    permission_classes = [IsAuthenticated]

    @contract(query=s.EventListQuery, responses={200: r.EventPageResponse})
    @handled
    def get(self, request, caller):
        return ok(ev.list_events(caller, dict(request.validated_query_data)))


class EventTypesView(APIView):
    permission_classes = [IsAuthenticated]

    @contract(responses={200: r.EventTypesResponse})
    @handled
    def get(self, request, caller):
        return ok(catalog())


class EgressIpsView(APIView):
    """The webhook NAT's published addresses per region (section 6.4): ``WEBHOOKS_EGRESS_IPS`` as
    ``region=ip,ip;region=ip``, set by the hosted deployment (WH-1c). Empty where none are published."""

    permission_classes = [IsAuthenticated]

    @contract(responses={200: r.EgressResponse})
    @handled
    def get(self, request, caller):
        regions = []
        for part in os.environ.get("WEBHOOKS_EGRESS_IPS", "").split(";"):
            region, _, ips = part.partition("=")
            addrs = [a.strip() for a in ips.split(",") if a.strip()]
            if region.strip() and addrs:
                regions.append({"region": region.strip(), "addresses": addrs})
        return ok({"hosted": config.is_hosted(), "regions": regions})


class PreviewView(APIView):
    permission_classes = [IsAuthenticated]

    @contract(s.PreviewRequest, responses={200: r.PreviewResponse})
    @handled
    def post(self, request, caller):
        return ok(preview(caller, dict(request.validated_data)))


class PolicyView(APIView):
    permission_classes = [IsAuthenticated]

    @contract(responses={200: r.PolicyResponse})
    @handled
    def get(self, request, caller, workspace_id=None):
        return ok(
            api_policy.get_policy(
                caller, None if workspace_id is None else str(workspace_id)
            )
        )

    @contract(s.PolicyRequest, responses={200: r.PolicyResponse})
    @handled
    def put(self, request, caller, workspace_id=None):
        return ok(api_policy.put_policy(caller, dict(request.validated_data)))


class WorkspacePolicyView(PolicyView):
    @contract(s.WorkspacePolicyRequest, responses={200: r.PolicyResponse})
    @handled
    def put(self, request, caller, workspace_id=None):
        caller.require(VIEW, str(workspace_id))
        return ok(
            api_policy.put_policy(
                caller, dict(request.validated_data), str(workspace_id)
            )
        )
