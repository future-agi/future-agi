"""Real late workspace onboarding and tenant-bound property/value APIs.

Only the disposable run may create ORM fixtures or submit OTLP. There are no
catalog, activation, identity, lease, or acknowledgement writes in this stage.
"""

from __future__ import annotations

import json
import time
import uuid

from completion_timing import COMPLETION_TIMEOUT_SECONDS, completion_timing
from run import save


def require_owned(run):
    if not run.manifest.get("application") or not run.owned():
        raise RuntimeError("workspace stage requires the owned disposable application")


def fixture_scopes(run, original):
    run_id = run.manifest["run_id"]
    if len(run_id) != 16 or any(c not in "0123456789abcdef" for c in run_id):
        raise RuntimeError("workspace fixture run identity is invalid")

    def identity(name):
        return str(uuid.uuid5(uuid.NAMESPACE_URL, run_id + ":workspace:" + name))

    return [
        {
            "label": label,
            "organization_id": str(uuid.UUID(original["organization_id"]))
            if label == "peer"
            else identity(label + ":org"),
            "workspace_id": identity(label + ":workspace"),
            "project_id": identity(label + ":project"),
            "project_name": "Managed workspace " + label,
            "key_name": "managed-smoke-" + label,
            "values": [label + "-a", label + "-b"],
        }
        for label in ("peer", "foreign")
    ]


def create(run, original):
    require_owned(run)
    from django.db import transaction

    from accounts.models.organization import Organization
    from accounts.models.organization_membership import OrganizationMembership
    from accounts.models.user import OrgApiKey, User
    from accounts.models.workspace import Workspace, WorkspaceMembership
    from tfc.constants.roles import OrganizationRoles
    from tracer.models.project import Project

    scopes = fixture_scopes(run, original)
    with transaction.atomic():
        for scope in scopes:
            organization = (
                Organization.objects.get(id=scope["organization_id"])
                if scope["label"] == "peer"
                else Organization.objects.create(
                    id=scope["organization_id"], name="Managed isolated foreign"
                )
            )
            user = User.objects.create_user(
                email=f"{run.manifest['run_id']}-{scope['label']}@managed-smoke.invalid",
                name="Synthetic workspace owner",
                organization=organization,
            )
            workspace = Workspace.no_workspace_objects.create(
                id=scope["workspace_id"],
                organization=organization,
                name=scope["label"],
                is_default=scope["label"] == "foreign",
                created_by=user,
            )
            membership, _ = OrganizationMembership.no_workspace_objects.get_or_create(
                organization=organization,
                user=user,
                defaults={"role": OrganizationRoles.OWNER},
            )
            WorkspaceMembership.no_workspace_objects.get_or_create(
                workspace=workspace,
                user=user,
                defaults={
                    "role": OrganizationRoles.WORKSPACE_ADMIN,
                    "organization_membership": membership,
                },
            )
            OrgApiKey.objects.create(
                name=scope["key_name"],
                type="user",
                user=user,
                organization=organization,
                workspace=workspace,
            )
            Project.no_workspace_objects.create(
                id=scope["project_id"],
                organization=organization,
                workspace=workspace,
                name=scope["project_name"],
                model_type="GenerativeLLM",
                trace_type="observe",
                user=user,
            )
    save(run.directory / "workspace-fixtures.json", {"scopes": scopes})


def request(client, endpoint, params, **headers):
    # APIClient reapplies credentials after per-request extras. Override them
    # explicitly so a forged workspace header actually reaches authentication.
    original = dict(client._credentials)
    try:
        client.credentials(**{**original, **headers})
        response = client.get(
            "/tracer/dashboard/" + endpoint + "/", params, HTTP_HOST="localhost"
        )
        if any(response.wsgi_request.META.get(k) != v for k, v in headers.items()):
            raise RuntimeError("test header did not reach the actual request")
        return response.status_code, response.json()
    finally:
        client.credentials(**original)


def active_result(status, payload, *, allow_pending=False):
    if status != 200:
        raise RuntimeError(f"workspace catalog API failed: {status} {payload}")
    result = payload["result"]
    if allow_pending and result.get("query_provenance") == "property_catalog_bootstrap":
        if not (
            result.get("query_status") == "pending"
            and result.get("query_complete") is False
            and result.get("query_exact") is False
            and result.get("metrics") == []
        ):
            raise RuntimeError("workspace bootstrap pretended to be an exact catalog")
        return None
    if not (
        result.get("query_provenance") == "activated_property_catalog"
        and result.get("query_complete") is True
    ):
        raise RuntimeError(f"workspace API did not use selected catalog: {result}")
    return result


def verify(run, original, _key, original_get):
    require_owned(run)
    import requests
    from ingestion_smoke import collector_state
    from rest_framework.test import APIClient

    from accounts.models.user import OrgApiKey

    scopes = json.loads((run.directory / "workspace-fixtures.json").read_text())[
        "scopes"
    ]
    if scopes != fixture_scopes(run, original):
        raise RuntimeError("workspace fixtures escaped the exact owned scopes")
    collector_state(run)
    identity_path = run.directory / "application-runtime/runtime-identity-v1.json"
    identity_before = identity_path.read_bytes()
    clients = {}
    report = {
        "status": "failed",
        "observations": [],
        "denied_scopes": [],
        "denied_cursors": [],
        "header_checks": [],
    }
    started = time.monotonic()
    session = requests.Session()
    session.trust_env = False
    try:
        for scope in scopes:
            key = OrgApiKey.objects.get(
                name=scope["key_name"],
                organization_id=scope["organization_id"],
                workspace_id=scope["workspace_id"],
            )
            client = APIClient()
            client.credentials(
                HTTP_X_API_KEY=key.api_key,
                HTTP_X_SECRET_KEY=key.secret_key,
                HTTP_X_WORKSPACE_ID=scope["workspace_id"],
            )
            clients[scope["label"]] = client
            current = time.time_ns()
            trace = uuid.uuid5(
                uuid.NAMESPACE_URL, run.manifest["run_id"] + scope["label"]
            ).hex
            body = {
                "resourceSpans": [
                    {
                        "resource": {
                            "attributes": [
                                {
                                    "key": "project_name",
                                    "value": {"stringValue": scope["project_name"]},
                                }
                            ]
                        },
                        "scopeSpans": [
                            {
                                "scope": {"name": "managed-workspace-smoke"},
                                "spans": [
                                    {
                                        "traceId": trace,
                                        "spanId": trace[:16],
                                        "name": "tenant_marker",
                                        "kind": 1,
                                        "startTimeUnixNano": str(current),
                                        "endTimeUnixNano": str(current + 1_000_000),
                                        "attributes": [
                                            {
                                                "key": "tenant_marker",
                                                "value": {
                                                    "arrayValue": {
                                                        "values": [
                                                            {"stringValue": v}
                                                            for v in scope["values"]
                                                        ]
                                                    }
                                                },
                                            }
                                        ],
                                    }
                                ],
                            }
                        ],
                    }
                ]
            }
            # Exclusive attempt marker; never retry an uncertain ingestion POST.
            with (run.directory / ("workspace-otlp-" + scope["label"] + ".json")).open(
                "x"
            ) as claim:
                json.dump({"trace_id": trace, "project_id": scope["project_id"]}, claim)
            response = session.post(
                f"http://127.0.0.1:{run.manifest['ports']['otlp']}/v1/traces",
                headers={"X-Api-Key": key.api_key, "X-Secret-Key": key.secret_key},
                json=body,
                timeout=8,
                allow_redirects=False,
            )
            if response.status_code != 200:
                raise RuntimeError(f"workspace OTLP failed: {response.status_code}")

        remaining = {s["label"] for s in scopes}
        deadline = started + COMPLETION_TIMEOUT_SECONDS
        while remaining and time.monotonic() < deadline:
            # A new workspace must not interrupt an already selected catalog.
            history, _ = original_get(
                "filter_values",
                {
                    "property_id": "custom_attribute:plan",
                    "source": "traces",
                    "project_ids": original["project_id"],
                    "page_size": 50,
                },
            )
            if [v["value"] for v in history["values"]] != ["Pro"]:
                raise RuntimeError(
                    "new workspace onboarding lost existing workspace history"
                )
            for scope in scopes:
                if scope["label"] not in remaining:
                    continue
                client = clients[scope["label"]]
                props = active_result(
                    *request(
                        client,
                        "metrics",
                        {
                            "source": "traces",
                            "search": "tenant_marker",
                            "cursor_mode": "true",
                            "page_size": 50,
                        },
                    ),
                    allow_pending=True,
                )
                report["observations"].append(
                    {
                        "workspace": scope["label"],
                        "seconds": time.monotonic() - started,
                        "state": "pending" if props is None else "active",
                    }
                )
                if props is None or not props["metrics"]:
                    continue
                if [m["name"] for m in props["metrics"]] != ["tenant_marker"]:
                    raise RuntimeError(
                        "workspace property discovery escaped expected definition"
                    )
                for project_scope in ({}, {"project_ids": scope["project_id"]}):
                    values = active_result(
                        *request(
                            client,
                            "filter_values",
                            {
                                "source": "traces",
                                "property_id": "custom_attribute:tenant_marker",
                                "page_size": 50,
                                **project_scope,
                            },
                        )
                    )
                    if sorted(v["value"] for v in values["values"]) != scope["values"]:
                        raise RuntimeError(
                            "workspace property values crossed tenant boundaries"
                        )
                remaining.remove(scope["label"])
            if remaining:
                time.sleep(0.5)
        if remaining:
            raise RuntimeError(
                f"new workspaces did not onboard in 180s: {sorted(remaining)}"
            )

        report["timing"] = completion_timing(
            time.monotonic() - started, latency_target_seconds=60
        )

        for scope in scopes:
            client = clients[scope["label"]]
            other = next(s for s in scopes if s["label"] != scope["label"])
            for endpoint in ("metrics", "filter_values"):
                for project_ids in (
                    other["project_id"],
                    original["project_id"],
                    scope["project_id"] + "," + other["project_id"],
                ):
                    params = {
                        "source": "traces",
                        "page_size": 50,
                        "project_ids": project_ids,
                    }
                    params.update(
                        {"cursor_mode": "true"}
                        if endpoint == "metrics"
                        else {"property_id": "custom_attribute:tenant_marker"}
                    )
                    status, _payload = request(client, endpoint, params)
                    if status not in (400, 403):
                        raise RuntimeError(
                            f"foreign project scope was not rejected: {endpoint} {status}"
                        )
                    report["denied_scopes"].append(
                        {
                            "workspace": scope["label"],
                            "endpoint": endpoint,
                            "project_ids": project_ids,
                            "status": status,
                        }
                    )
            page = active_result(
                *request(
                    client,
                    "filter_values",
                    {
                        "source": "traces",
                        "property_id": "custom_attribute:tenant_marker",
                        "page_size": 1,
                    },
                )
            )
            if page.get("has_more") is not True or not page.get("next_cursor"):
                raise RuntimeError(
                    "workspace cursor isolation lacks a real continuation"
                )
            status, _payload = request(
                clients[other["label"]],
                "filter_values",
                {
                    "source": "traces",
                    "property_id": "custom_attribute:tenant_marker",
                    "page_size": 1,
                    "cursor": page["next_cursor"],
                },
            )
            if status not in (400, 403):
                raise RuntimeError(f"cross-workspace cursor was not rejected: {status}")
            report["denied_cursors"].append(
                {"from": scope["label"], "to": other["label"], "status": status}
            )
            # API-key workspace is authoritative. A foreign header must either
            # be rejected or still return exactly the key's own scoped values.
            status, payload = request(
                client,
                "filter_values",
                {
                    "source": "traces",
                    "property_id": "custom_attribute:tenant_marker",
                    "page_size": 50,
                },
                HTTP_X_WORKSPACE_ID=other["workspace_id"],
            )
            if status not in (400, 403):
                result = active_result(status, payload)
                if sorted(v["value"] for v in result["values"]) != scope["values"]:
                    raise RuntimeError("foreign header changed API-key tenant scope")
            report["header_checks"].append(
                {
                    "workspace": scope["label"],
                    "foreign_workspace_header": other["workspace_id"],
                    "status": status,
                    "actual_request_header_verified": True,
                }
            )
        original_props, _ = original_get(
            "metrics",
            {
                "source": "traces",
                "search": "tenant_marker",
                "cursor_mode": "true",
                "page_size": 50,
            },
        )
        if original_props["metrics"]:
            raise RuntimeError(
                "new workspace properties leaked into original workspace"
            )
        if identity_path.read_bytes() != identity_before:
            raise RuntimeError("new workspace onboarding changed installation identity")
        report.update(
            status="passed",
            seconds=time.monotonic() - started,
            same_identity_bytes=True,
            new_workspaces=2,
            organizations=2,
            source="real_authenticated_otlp",
            cursor_and_header_isolation=True,
        )
        return report
    finally:
        session.close()
        save(run.directory / "catalog-workspaces.json", report)
