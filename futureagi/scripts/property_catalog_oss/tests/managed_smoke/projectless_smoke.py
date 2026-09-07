"""Real projectless -> Observe -> projectless application smoke.

Parent-owned integration, inside the isolated Django application process:
  admin: seed(run)                       # Workspace + membership/key + six ORM rows
  reader: verify(run, phase="seeded")    # Qualified relational-only build
  admin: update(run)                     # Add one real Observe project, no spans
  reader: verify(run, phase="project")   # Qualified project scope, still zero source
  reader: ingest(run)                    # ONE actual authenticated OTLP POST
  reader: verify(run, phase="observed")  # Actual catalog property/value APIs
  admin: delete(run)                     # Soft-delete only our last Observe project
  reader: verify(run, phase="deleted")   # Relational discovery; old scope denied

Each action can run in a separate application process. After the parent performs
an actual runtime restart, call verify_restart(run, phase=<last verified phase>).
That action checks durable fixture/identity/API continuity; the parent must report
the actual service restart separately. This module never restarts infrastructure.
ingest() persists an exclusive attempt before POST and never retries uncertain
writes. After an interrupted POST, verify(observed) can establish the effect by
real API readback without submitting again.

Only normal ORM definition/onboarding/project writes and actual OTLP are used.
No catalog, control, fence, schema, span-table, or repair writes. Dataset values
are explicitly out of scope. Selected-build checkpoint SELECTs distinguish zero
span source from nonzero required deliveries; they are not synthesized evidence.
All reports are local evidence, not product state. Offline tests are not live QA.
verify's maximum 90-second budget includes guards, ORM scope checks and all reads.
The parent's 100-second process cap also covers Django startup before this call;
pass a smaller timeout if startup has consumed that headroom. Synchronous calls
are checked before/after, not forcibly interrupted by this helper.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
import uuid

from relational_smoke import _atomic, _matches
from relational_smoke import _guard as _application_guard
from relational_smoke import _models as _relational_models
from run import save
from workspace_smoke import active_result, request

PHASES = ("seeded", "project", "observed", "deleted")


def fixture_plan(run, original):
    run_id = run.manifest["run_id"]
    if not isinstance(run_id, str) or not re.fullmatch(r"[0-9a-f]{16}", run_id):
        raise RuntimeError("invalid projectless run identity")

    def identity(name):
        return str(uuid.uuid5(uuid.NAMESPACE_URL, run_id + ":projectless:" + name))

    return {
        "version": 1,
        "run_id": run_id,
        "organization_id": str(uuid.UUID(original["organization_id"])),
        "original_workspace_id": str(uuid.UUID(original["workspace_id"])),
        "original_project_id": str(uuid.UUID(original["project_id"])),
        "workspace_id": identity("workspace"),
        "key_id": identity("key"),
        "membership_id": identity("membership"),
        "key_name": "managed-projectless-" + run_id,
        "prefix": "projectless-" + run_id,
        "ids": {
            kind: identity(kind)
            for kind in (
                "template",
                "agent",
                "run_test",
                "simulation",
                "dataset",
                "column",
                "project",
            )
        },
        "marker": "projectless_marker_" + run_id,
        "values": ["first-" + run_id, "second-" + run_id],
        "trace_id": uuid.UUID(identity("trace")).hex,
    }


def _digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _guard(run, *, write):
    _application_guard(run, write=write)
    if not run.directory.is_absolute() or run.directory.is_symlink():
        raise RuntimeError(
            "projectless evidence directory is not an owned absolute directory"
        )
    original = json.loads((run.directory / "source-fixture.json").read_text())
    fixture_plan(run, original)
    if not write:
        _read_config(run)  # Validate catalog endpoint before any APIClient query.


def _read_config(run):
    from tracer.services.clickhouse.v2.property_catalog.connection import (
        PropertyCatalogConnectionConfig,
    )

    config = PropertyCatalogConnectionConfig.from_settings()
    _check_read_config(run, config)
    return config


def _check_read_config(run, config):
    if (config.host, config.port, config.database, config.user) != (
        "127.0.0.1",
        run.manifest["ports"]["native"],
        "property_catalog_dev_app_" + run.manifest["run_id"],
        "property_catalog_oss_api",
    ):
        raise RuntimeError(
            "projectless catalog endpoint escaped disposable readonly scope"
        )


def _plan(run, *, installed=True):
    plan = fixture_plan(
        run, json.loads((run.directory / "source-fixture.json").read_text())
    )
    if (
        installed
        and json.loads((run.directory / "projectless-fixture.json").read_text()) != plan
    ):
        raise RuntimeError("projectless fixture identity changed")
    return plan


def _models():
    from accounts.models.organization_membership import OrganizationMembership
    from accounts.models.workspace import Workspace, WorkspaceMembership
    from tfc.constants.roles import OrganizationRoles

    return {
        **_relational_models(),
        "workspace": Workspace,
        "membership": WorkspaceMembership,
        "org_membership": OrganizationMembership,
        "admin_role": OrganizationRoles.WORKSPACE_ADMIN,
    }


def _origin(models, plan):
    key = models["key"].no_workspace_objects.get(
        name="managed-smoke-api",
        organization_id=plan["organization_id"],
        workspace_id=plan["original_workspace_id"],
        enabled=True,
        type="user",
    )
    if key.user_id is None:
        raise RuntimeError("projectless onboarding requires the original real owner")
    membership = models["org_membership"].no_workspace_objects.get(
        organization_id=plan["organization_id"],
        user_id=key.user_id,
    )
    return key, membership


def _specs(plan):
    ids, prefix = plan["ids"], plan["prefix"]
    tenant = {k: plan[k] for k in ("organization_id", "workspace_id")}
    return {
        "template": dict(
            name=prefix + "-template",
            **tenant,
            config={"output": "choices"},
            choices=plan["values"],
        ),
        "agent": dict(
            agent_name=prefix + "-agent",
            description="Projectless isolated smoke",
            agent_type="text",
            inbound=False,
            **tenant,
        ),
        "run_test": dict(
            name=prefix + "-run", agent_definition_id=ids["agent"], **tenant
        ),
        "simulation": {
            "name": prefix + "-simulation",
            "run_test_id": ids["run_test"],
            "eval_template_id": ids["template"],
        },
        "dataset": dict(name=prefix + "-dataset", **tenant),
        "column": {
            "name": prefix + "-column",
            "dataset_id": ids["dataset"],
            "data_type": "text",
            "source": "OTHERS",
        },
    }


def _row(model, identity, fields, *, create=False):
    manager = model.all_objects.using("default")
    obj = (
        manager.get_or_create(id=identity, defaults=fields)[0]
        if create
        else manager.get(id=identity)
    )
    if obj.deleted or not _matches(obj, fields):
        raise RuntimeError(
            "projectless exact ORM identity/fields changed; refusing relabel"
        )
    return obj


def _scope_rows(models, plan):
    # All project types and tombstones, not just Observe. This proves genuinely
    # projectless onboarding and prevents deleting one of several live projects.
    return list(
        models["project"]
        .all_objects.using("default")
        .filter(
            workspace_id=plan["workspace_id"],
            organization_id=plan["organization_id"],
        )
        .order_by("id")
    )


def _check_projects(rows, plan, phase):
    if phase == "seeded":
        valid = not rows
    else:
        valid = (
            len(rows) == 1
            and str(rows[0].id) == plan["ids"]["project"]
            and (
                rows[0].trace_type == "observe"
                and rows[0].deleted == (phase == "deleted")
            )
        )
    if not valid:
        raise RuntimeError(
            "projectless workspace project inventory is not the exact phase scope"
        )


def _assert_state(models, plan, phase, *, deadline=math.inf):
    origin, membership = _bounded(deadline, _origin, models, plan)
    _bounded(
        deadline,
        _row,
        models["workspace"],
        plan["workspace_id"],
        {
            "organization_id": plan["organization_id"],
            "name": plan["prefix"],
            "created_by_id": origin.user_id,
            "is_default": False,
            "is_active": True,
        },
    )
    _bounded(
        deadline,
        _row,
        models["membership"],
        plan["membership_id"],
        {
            "workspace_id": plan["workspace_id"],
            "user_id": origin.user_id,
            "organization_membership_id": membership.id,
            "role": models["admin_role"],
        },
    )
    key = _bounded(
        deadline,
        _row,
        models["key"],
        plan["key_id"],
        {
            "name": plan["key_name"],
            "organization_id": plan["organization_id"],
            "workspace_id": plan["workspace_id"],
            "user_id": origin.user_id,
            "type": "user",
            "enabled": True,
        },
    )
    for kind, fields in _specs(plan).items():
        _bounded(deadline, _row, models[kind], plan["ids"][kind], fields)
    projects = _bounded(deadline, _scope_rows, models, plan)
    _check_projects(projects, plan, phase)
    if projects and not _matches(
        projects[0],
        {
            "organization_id": plan["organization_id"],
            "workspace_id": plan["workspace_id"],
            "user_id": origin.user_id,
            "name": plan["prefix"] + "-observe",
            "model_type": "GenerativeLLM",
        },
    ):
        raise RuntimeError("projectless Observe project fields changed")
    return key


def seed(run):
    _guard(run, write=True)
    plan = _plan(run, installed=False)
    path = run.directory / "projectless-fixture.json"
    if path.exists() and json.loads(path.read_text()) != plan:
        raise RuntimeError("refusing to replace a different projectless fixture")
    models = _models()
    with _atomic():
        origin, membership = _origin(models, plan)
        _check_projects(_scope_rows(models, plan), plan, "seeded")
        _row(
            models["workspace"],
            plan["workspace_id"],
            {
                "organization_id": plan["organization_id"],
                "name": plan["prefix"],
                "created_by_id": origin.user_id,
                "is_default": False,
                "is_active": True,
            },
            create=True,
        )
        _row(
            models["membership"],
            plan["membership_id"],
            {
                "workspace_id": plan["workspace_id"],
                "user_id": origin.user_id,
                "organization_membership_id": membership.id,
                "role": models["admin_role"],
            },
            create=True,
        )
        _row(
            models["key"],
            plan["key_id"],
            {
                "name": plan["key_name"],
                "organization_id": plan["organization_id"],
                "workspace_id": plan["workspace_id"],
                "user_id": origin.user_id,
                "type": "user",
                "enabled": True,
            },
            create=True,
        )
        for kind, fields in _specs(plan).items():
            _row(models[kind], plan["ids"][kind], fields, create=True)
    save(path, plan)
    return plan


def _passed(run, plan, phase):
    report = json.loads(
        (run.directory / f"catalog-projectless-{phase}.json").read_text()
    )
    if (report.get("status"), report.get("phase"), report.get("fixture_sha256")) != (
        "passed",
        phase,
        _digest(plan),
    ):
        raise RuntimeError("previous projectless live verification not proven")
    return report


def update(run):
    """Add the first Observe project only after relational-only API success."""
    _guard(run, write=True)
    plan, models = _plan(run), _models()
    _passed(run, plan, "seeded")
    with _atomic():
        rows = _scope_rows(models, plan)
        key = _assert_state(models, plan, "project" if rows else "seeded")
        _row(
            models["project"],
            plan["ids"]["project"],
            {
                "organization_id": plan["organization_id"],
                "workspace_id": plan["workspace_id"],
                "user_id": key.user_id,
                "name": plan["prefix"] + "-observe",
                "trace_type": "observe",
                "model_type": "GenerativeLLM",
            },
            create=True,
        )
    return {"project_id": plan["ids"]["project"]}


def delete(run):
    """Soft-delete ONLY this run's last Observe project; never alter its spans."""
    _guard(run, write=True)
    plan, models = _plan(run), _models()
    _passed(run, plan, "observed")
    with _atomic():
        rows = _scope_rows(models, plan)
        phase = "deleted" if len(rows) == 1 and rows[0].deleted else "observed"
        _assert_state(models, plan, phase)
        project = rows[0]
        if not project.deleted:
            project.delete(using="default")
    return {"project_id": plan["ids"]["project"], "soft_deleted": True}


def _client(key, plan):
    from rest_framework.test import APIClient

    if any(
        str(getattr(key, name)) != plan[name]
        for name in ("organization_id", "workspace_id")
    ):
        raise RuntimeError("projectless API credentials escaped tenant")
    client = APIClient()
    client.credentials(
        HTTP_X_API_KEY=key.api_key,
        HTTP_X_SECRET_KEY=key.secret_key,
        HTTP_X_WORKSPACE_ID=plan["workspace_id"],
    )
    return client


def _budget(deadline):
    if time.monotonic() >= deadline:
        raise RuntimeError("projectless action exceeded its bounded wall")


def _bounded(deadline, action, *args, **kwargs):
    _budget(deadline)
    result = action(*args, **kwargs)
    _budget(deadline)
    return result


def _page(client, endpoint, params, deadline, *, catalog=True, pending=False):
    _budget(deadline)
    status, payload = request(client, endpoint, params)
    _budget(deadline)
    if not isinstance(payload, dict) or not isinstance(payload.get("result"), dict):
        raise RuntimeError(f"malformed projectless API response ({status})")
    if catalog:
        return active_result(status, payload, allow_pending=pending)
    result = payload["result"]
    if (
        status != 200
        or result.get("query_complete") is not True
        or result.get("query_status") != "complete"
    ):
        raise RuntimeError("projectless configured value API was not complete")
    return result


def _metrics(client, params, deadline):
    items, cursors, metadata = {}, set(), None
    for _ in range(8):
        page = _page(client, "metrics", params, deadline, pending=True)
        if page is None:
            return None
        current = {
            k: page.get(k)
            for k in ("catalog_epoch", "catalog_revision", "activation_fingerprint")
        }
        if metadata is not None and metadata != current:
            raise RuntimeError("projectless continuation changed selected activation")
        metadata = current
        if any(
            type(metadata[k]) is not int or metadata[k] < 1
            for k in ("catalog_epoch", "catalog_revision")
        ) or not re.fullmatch(r"[a-f0-9]{64}", str(metadata["activation_fingerprint"])):
            raise RuntimeError("projectless API has no actual selected identity")
        if not isinstance(page.get("metrics"), list):
            raise RuntimeError("malformed projectless metric page")
        for item in page["metrics"]:
            if (
                not isinstance(item, dict)
                or not item.get("property_id")
                or item["property_id"] in items
            ):
                raise RuntimeError("duplicate/invalid projectless property identity")
            items[item["property_id"]] = item
        more, cursor = page.get("has_more"), page.get("next_cursor")
        if more is False and cursor is None:
            return items, metadata
        if (
            more is not True
            or not isinstance(cursor, str)
            or not cursor
            or cursor in cursors
        ):
            raise RuntimeError("invalid projectless continuation")
        cursors.add(cursor)
        params = {**params, "cursor": cursor}
    raise RuntimeError("projectless metrics exceeded eight pages")


def _definitions(plan):
    return {
        "eval_template:" + plan["ids"]["template"]: ("all", "eval_metric", "template"),
        "eval_config:" + plan["ids"]["simulation"]: (
            "simulation",
            "eval_metric",
            "simulation",
        ),
        "dataset_column:" + plan["ids"]["column"]: (
            "datasets",
            "custom_column",
            "column",
        ),
    }


def _summary(plan, phase, metadata, activation, build_plan, checkpoints):
    """Independently expose actual selected scope and per-role checkpoint counts."""
    expected_projects = (
        [] if phase in ("seeded", "deleted") else [plan["ids"]["project"]]
    )
    if list(activation.source_scope.project_ids) != expected_projects:
        return None
    if activation.activation_sha256 != metadata["activation_fingerprint"]:
        raise RuntimeError("selected API identity differs from activation proof")
    expected = {
        (str(s.source_adapter), s.producer_stream_id): str(s.role)
        for s in build_plan.streams
    }
    actual = {
        (row["source_adapter"], row["producer_stream_id"]): row for row in checkpoints
    }
    if (
        len(expected) != 10
        or len(actual) != len(checkpoints)
        or set(actual) != set(expected)
    ):
        raise RuntimeError(
            "projectless selected build lacks exact ten-stream checkpoint inventory"
        )
    spans = {}
    for key, row in actual.items():
        if row["status"] != "complete" or row["terminal"] not in (1, True):
            raise RuntimeError("projectless selected stream is not terminal/complete")
        for field in ("source_count", "value_count", "delivery_count"):
            if type(row[field]) is not int or row[field] < 0:
                raise RuntimeError("projectless stream count is malformed")
        if row["delivery_count"] < 1:
            raise RuntimeError("zero source was mistaken for zero deliveries")
        if key[0] == "span_attribute":
            spans[expected[key]] = {
                field: row[field]
                for field in ("source_count", "value_count", "delivery_count")
            }
            if phase != "observed" and (row["source_count"] or row["value_count"]):
                raise RuntimeError(
                    "projectless/uningested scope contains span source or values"
                )
    if set(spans) != {"definitions", "values", "hot_values", "source_audit"}:
        raise RuntimeError("projectless span role inventory is incomplete")
    return {
        **metadata,
        "build_token": activation.build_token,
        "project_ids": expected_projects,
        "span_streams": spans,
        "required_stream_deliveries": sum(row["delivery_count"] for row in checkpoints),
    }


def _evidence(run, plan, phase, metadata, deadline):
    from tracer.services.clickhouse.v2.property_catalog.activation import (
        RevisionBuildPlan,
    )
    from tracer.services.clickhouse.v2.property_catalog.connection import (
        PropertyCatalogReadExecutor,
    )
    from tracer.services.clickhouse.v2.property_catalog.reader import (
        property_catalog_activation_sql,
        verify_property_catalog_activation,
    )

    _budget(deadline)
    config = _read_config(run)
    executor = PropertyCatalogReadExecutor(config=config)
    params = {
        "catalog_organization_id": plan["organization_id"],
        "catalog_workspace_id": plan["workspace_id"],
        "catalog_epoch": metadata["catalog_epoch"],
        "catalog_revision": metadata["catalog_revision"],
        "catalog_exact_activation": 1,
    }
    settings = {
        "max_threads": 1,
        "max_result_rows": 11,
        "max_result_bytes": 262144,
        "result_overflow_mode": "throw",
    }
    rows = executor.execute(
        property_catalog_activation_sql(config.database),
        params,
        timeout_ms=2000,
        settings=settings,
    ).data
    _budget(deadline)
    activation = verify_property_catalog_activation(
        rows,
        scope={
            "organization_id": plan["organization_id"],
            "workspace_id": plan["workspace_id"],
        },
        cursor_present=False,
    )
    build_plan = RevisionBuildPlan.from_json(rows[0]["build_plan_json"])
    _budget(deadline)
    checkpoints = executor.execute(
        _checkpoint_sql(config.database),
        {**params, "build_token": activation.build_token},
        timeout_ms=2000,
        settings=settings,
    ).data
    _budget(deadline)
    return _summary(plan, phase, metadata, activation, build_plan, checkpoints)


def _checkpoint_sql(database):
    if not re.fullmatch(r"property_catalog_dev_app_[a-f0-9]{16}", database):
        raise RuntimeError(
            "projectless checkpoint database is outside disposable scope"
        )
    # Compare every distinct latest count state instead of arbitrarily choosing
    # one conflicting row. These are the real checkpoint schema column names.
    return f"""
SELECT DISTINCT source_adapter, toString(producer_stream_id) AS producer_stream_id,
    status, terminal, source_rows AS source_count, value_rows AS value_count,
    delivery_count
FROM (
    SELECT *, max(_version) OVER (PARTITION BY source_adapter, producer_stream_id) AS latest_version
    FROM `{database}`.property_catalog_checkpoints
    PREWHERE organization_id=%(catalog_organization_id)s AND workspace_id=%(catalog_workspace_id)s
      AND catalog_epoch=%(catalog_epoch)s AND catalog_revision=%(catalog_revision)s
      AND build_token=%(build_token)s
)
WHERE _version=latest_version
LIMIT 11
"""


def _otlp(plan, timestamp):
    if type(timestamp) is not int or timestamp <= 0:
        raise RuntimeError("invalid projectless OTLP timestamp")
    return {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [
                        {
                            "key": "project_name",
                            "value": {"stringValue": plan["prefix"] + "-observe"},
                        }
                    ]
                },
                "scopeSpans": [
                    {
                        "scope": {"name": "managed-projectless-smoke"},
                        "spans": [
                            {
                                "traceId": plan["trace_id"],
                                "spanId": plan["trace_id"][:16],
                                "name": plan["marker"],
                                "kind": 1,
                                "startTimeUnixNano": str(timestamp),
                                "endTimeUnixNano": str(timestamp + 1_000_000),
                                "attributes": [
                                    {
                                        "key": plan["marker"],
                                        "value": {
                                            "arrayValue": {
                                                "values": [
                                                    {"stringValue": v}
                                                    for v in plan["values"]
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


def _claim(run, plan):
    claim = json.loads((run.directory / "projectless-otlp-attempt.json").read_text())
    expected = {
        "fixture_sha256": _digest(plan),
        "timestamp_ns": claim.get("timestamp_ns"),
        "payload_sha256": _digest(_otlp(plan, claim.get("timestamp_ns"))),
    }
    if claim != expected:
        raise RuntimeError("projectless OTLP attempt identity changed")
    return claim


def ingest(run):
    _guard(run, write=False)
    plan = _plan(run)
    _passed(run, plan, "project")
    key = _assert_state(_models(), plan, "project")
    attempt = run.directory / "projectless-otlp-attempt.json"
    accepted = run.directory / "projectless-otlp-accepted.json"
    if attempt.exists():
        claim = _claim(run, plan)
        if accepted.exists() and json.loads(accepted.read_text()) == claim:
            return {"already_accepted": True, **claim}
        raise RuntimeError(
            "uncertain projectless OTLP attempt: use observed API readback; never repost"
        )
    if accepted.exists():
        raise RuntimeError("projectless accepted receipt lacks original attempt")
    import os

    import requests
    from ingestion_smoke import collector_state

    collector_state(run)  # Actual owned collector + loopback publication check.
    timestamp = time.time_ns()
    body = _otlp(plan, timestamp)
    claim = {
        "fixture_sha256": _digest(plan),
        "timestamp_ns": timestamp,
        "payload_sha256": _digest(body),
    }
    with attempt.open("x") as output:
        json.dump(claim, output, sort_keys=True)
        output.flush()
        os.fsync(output.fileno())
    directory_fd = os.open(run.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    with requests.Session() as session:
        session.trust_env = False
        result = session.post(
            f"http://127.0.0.1:{run.manifest['ports']['otlp']}/v1/traces",
            json=body,
            headers={"X-Api-Key": key.api_key, "X-Secret-Key": key.secret_key},
            timeout=8,
            allow_redirects=False,
        )
        if result.status_code != 200 or result.json().get("partialSuccess"):
            raise RuntimeError("projectless OTLP was not fully accepted; do not retry")
    save(accepted, claim)
    return claim


def _checks(client, plan, phase, deadline):
    # Simulation choices are native ORM-backed values; no catalog provenance is
    # required here. Template choices need an Observe config, intentionally absent.
    query = {
        "property_id": "eval_config:" + plan["ids"]["simulation"],
        "source": "simulation",
        "page_size": 1,
    }
    first = _page(client, "filter_values", query, deadline, catalog=False)
    if (
        first.get("values")
        != [{"value": plan["values"][0], "label": plan["values"][0]}]
        or first.get("has_more") is not True
        or not first.get("next_cursor")
    ):
        raise RuntimeError("projectless simulation choices lack exact continuation")
    second = _page(
        client,
        "filter_values",
        {**query, "cursor": first["next_cursor"]},
        deadline,
        catalog=False,
    )
    if (
        second.get("values")
        != [{"value": plan["values"][1], "label": plan["values"][1]}]
        or second.get("has_more") is not False
        or second.get("next_cursor") is not None
    ):
        raise RuntimeError("projectless simulation choices are incomplete")
    values = _page(
        client,
        "filter_values",
        {
            "property_id": "custom_attribute:" + plan["marker"],
            "source": "traces",
            "page_size": 10,
        },
        deadline,
    )
    actual = sorted(v["value"] for v in values.get("values", []))
    if (
        actual != (sorted(plan["values"]) if phase == "observed" else [])
        or values.get("has_more") is not False
        or values.get("next_cursor") is not None
    ):
        raise RuntimeError("projectless span values differ from actual phase")
    denied = []
    for project in [
        plan["original_project_id"],
        *([plan["ids"]["project"]] if phase == "deleted" else []),
    ]:
        for endpoint in ("metrics", "filter_values"):
            _budget(deadline)
            params = {"source": "traces", "project_ids": project, "page_size": 10}
            params.update(
                {"cursor_mode": "true"}
                if endpoint == "metrics"
                else {"property_id": "custom_attribute:" + plan["marker"]}
            )
            status, _ = request(client, endpoint, params)
            _budget(deadline)
            if status not in (400, 403):
                raise RuntimeError("obsolete/foreign project scope was not rejected")
            denied.append(
                {"endpoint": endpoint, "project_id": project, "status": status}
            )
    return denied


def verify(run, *, phase="seeded", timeout=90, _restart=False):
    started = time.monotonic()
    if phase not in PHASES or not math.isfinite(timeout) or not 0 < timeout <= 90:
        raise ValueError("invalid projectless phase or wall budget")
    deadline = started + timeout
    _bounded(deadline, _guard, run, write=False)
    plan = _bounded(deadline, _plan, run)
    previous = (
        _bounded(deadline, _passed, run, plan, PHASES[PHASES.index(phase) - 1])
        if phase != "seeded"
        else None
    )
    baseline = _bounded(deadline, _passed, run, plan, phase) if _restart else None
    if phase == "observed":
        # Readback is safe even when the POST outcome was uncertain.
        _bounded(deadline, _claim, run, plan)
    key = _assert_state(_bounded(deadline, _models), plan, phase, deadline=deadline)
    identity_sha256 = _bounded(deadline, _identity_digest, run)
    report = {
        "status": "failed",
        "phase": phase,
        "fixture_sha256": _digest(plan),
        "runtime_identity_sha256": identity_sha256,
        "dataset_values": "definitions_only_not_tested",
        "restart_probe_only": _restart,
    }
    try:
        client = _bounded(deadline, _client, key, plan)
        while True:
            _budget(deadline)
            found = _metrics(
                client,
                {
                    "cursor_mode": "true",
                    "search": plan["prefix"],
                    "agent_definition_id": plan["ids"]["agent"],
                    "page_size": 2,
                },
                deadline,
            )
            if found is not None:
                metrics, metadata = found
                expected = _definitions(plan)
                if set(metrics) != set(expected):
                    unexpected = set(metrics) - set(expected)
                    if unexpected:
                        raise RuntimeError(
                            "unexpected property in projectless fixture namespace"
                        )
                else:
                    for identity, (source, category, kind) in expected.items():
                        if (
                            metrics[identity].get("source"),
                            metrics[identity].get("category"),
                            metrics[identity].get("display_name"),
                        ) != (source, category, _specs(plan)[kind]["name"]):
                            raise RuntimeError(
                                "projectless canonical property binding changed"
                            )
                    evidence = _evidence(run, plan, phase, metadata, deadline)
                    if evidence is not None:
                        if (
                            previous
                            and phase in ("project", "deleted")
                            and evidence["catalog_revision"]
                            <= previous["evidence"]["catalog_revision"]
                        ):
                            raise RuntimeError(
                                "projectless scope transition did not advance revision"
                            )
                        break
            time.sleep(min(0.5, max(0, deadline - time.monotonic())))
        # Ingestion effects may lag the current revision. Poll their catalog
        # vocabulary only while reads remain complete/qualified.
        if phase == "observed":
            while True:
                values = _page(
                    client,
                    "filter_values",
                    {
                        "property_id": "custom_attribute:" + plan["marker"],
                        "source": "traces",
                        "page_size": 10,
                    },
                    deadline,
                )
                if sorted(v["value"] for v in values.get("values", [])) == sorted(
                    plan["values"]
                ):
                    break
                time.sleep(min(0.5, max(0, deadline - time.monotonic())))
            # Bind the report to the activation that actually served the values,
            # not the earlier relational-only revision observed before polling.
            metadata = {
                k: values[k]
                for k in ("catalog_epoch", "catalog_revision", "activation_fingerprint")
            }
            evidence = _evidence(run, plan, phase, metadata, deadline)
            if evidence is None:
                raise RuntimeError(
                    "observed values activation has another project scope"
                )
        marker = _metrics(
            client,
            {
                "cursor_mode": "true",
                "category": "custom_attribute",
                "source": "traces",
                "search": plan["marker"],
                "page_size": 10,
            },
            deadline,
        )
        expected_marker = (
            {"custom_attribute:" + plan["marker"]} if phase == "observed" else set()
        )
        if marker is None or set(marker[0]) != expected_marker:
            raise RuntimeError("projectless span property discovery differs from phase")
        report["denied_scopes"] = _checks(client, plan, phase, deadline)
        _assert_state(_bounded(deadline, _models), plan, phase, deadline=deadline)
        if _bounded(deadline, _identity_digest, run) != identity_sha256:
            raise RuntimeError(
                "runtime identity changed during projectless verification"
            )
        if baseline and (
            identity_sha256 != baseline.get("runtime_identity_sha256")
            or evidence["catalog_epoch"] != baseline["evidence"]["catalog_epoch"]
            or evidence["catalog_revision"] < baseline["evidence"]["catalog_revision"]
        ):
            raise RuntimeError(
                "projectless restart changed epoch or regressed revision"
            )
        _budget(deadline)
        report.update(status="passed", evidence=evidence, property_ids=sorted(expected))
        return report
    finally:
        report["elapsed_seconds"] = time.monotonic() - started
        label = phase + ("-restart" if _restart else "")
        save(run.directory / f"catalog-projectless-{label}.json", report)


def verify_restart(run, *, phase, timeout=90):
    """Readback after parent-owned restart; no restart is claimed or performed here."""
    return verify(run, phase=phase, timeout=timeout, _restart=True)


def _identity_digest(run):
    """Observe persisted identity bytes, never edit or use them as auth authority."""
    import os
    import stat

    path = run.directory / "application-runtime/runtime-identity-v1.json"
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise RuntimeError("projectless runtime identity must be a regular file")
        data = os.read(fd, 65537)
        if not data or len(data) > 65536:
            raise RuntimeError("projectless runtime identity size is invalid")
        return hashlib.sha256(data).hexdigest()
    finally:
        os.close(fd)
