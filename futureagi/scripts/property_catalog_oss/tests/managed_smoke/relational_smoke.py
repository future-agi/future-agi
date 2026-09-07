"""Normal-ORM relational source smoke, called inside application_smoke's process.

Parent integration (no launcher or infrastructure operations in this module):
1. After startup and normal original/peer/foreign workspace onboarding, call
   seed(run) as the isolated ``smoke_admin``. It returns/writes a deterministic
   relational-fixture.json; repeat seed only before subsequent mutations.
2. As ``property_catalog_oss_reader`` with managed admission, call
   verify(run, original_source_fixture, original_api_key, phase="seeded").
   This constructs real APIClients; do NOT pass application_smoke's catalog-only
   get wrapper, since configured values intentionally use native adapters.
3. Optionally alternate admin mutate(run, phase="updated"), reader verify(...,
   phase="updated"), admin mutate(run, phase="deleted"), reader verify(...,
   phase="deleted"). Every mutation requires the previous successful API report.

Verification polls only successful qualified catalog reads for convergence; it
never triggers a build, changes activation/settings, writes a catalog row, or
submits ingestion. The parent keeps its ordinary supervisor running. HTTP errors
and malformed/incomplete reads fail immediately, not as convergence retries.
The existing source fixture and all its IDs are untouched. Only the 24 new rows
in this module's deterministic namespace are created; only original-workspace
rows in that namespace are updated/soft-deleted. Dataset coverage is definitions
ONLY: the native ClickHouse cell ingestion/value path is deliberately not used.
Offline tests establish guards/verifier logic, not live relational source parity.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
import uuid

from run import save
from workspace_smoke import active_result, fixture_scopes, request, require_owned

FAMILIES = {
    "template": ("eval_template", "all", "eval_metric", "number", "metric"),
    "config": ("eval_config", "all", "eval_metric", "number", "metric"),
    "simulation": ("eval_config", "simulation", "eval_metric", "number", "metric"),
    "label": ("annotation", "both", "annotation_metric", "categorical", "metric"),
    "column": ("dataset_column", "datasets", "custom_column", "text", "dimension"),
}
PHASES = ("seeded", "updated", "deleted")


def _database_guard(run, database, read_mode, *, write):
    expected_user = "smoke_admin" if write else "property_catalog_oss_reader"
    if (
        database.get("ENGINE") != "django.db.backends.postgresql"
        or database.get("NAME") != "managed_smoke"
        or database.get("HOST") != "127.0.0.1"
        or str(database.get("PORT")) != str(run.manifest["ports"]["postgres"])
        or database.get("USER") != expected_user
        or (not write and read_mode != "managed")
    ):
        raise RuntimeError("relational smoke escaped isolated database/role/admission")


def _guard(run, *, write):
    require_owned(run)  # Refuse unowned runs before importing Django/models.
    from django.conf import settings
    from django.db import connection

    _database_guard(
        run,
        settings.DATABASES["default"],
        getattr(settings, "PROPERTY_CATALOG_READ_MODE", None),
        write=write,
    )
    if connection.in_atomic_block:
        raise RuntimeError("relational action requires its own committed transaction")


def _atomic():
    from django.db import transaction

    return transaction.atomic(using="default")


def _models():
    from accounts.models.user import OrgApiKey
    from model_hub.models.develop_annotations import AnnotationsLabels
    from model_hub.models.develop_dataset import Column, Dataset
    from model_hub.models.evals_metric import EvalTemplate
    from simulate.models import AgentDefinition, RunTest, SimulateEvalConfig
    from tracer.models.custom_eval_config import CustomEvalConfig
    from tracer.models.project import Project

    return {
        "template": EvalTemplate,
        "config": CustomEvalConfig,
        "agent": AgentDefinition,
        "run_test": RunTest,
        "simulation": SimulateEvalConfig,
        "label": AnnotationsLabels,
        "dataset": Dataset,
        "column": Column,
        "project": Project,
        "key": OrgApiKey,
    }


def fixture_plan(run, original, others):
    """Pure plan; require the exact workspace_smoke-owned peer/foreign scopes."""
    if others != fixture_scopes(run, original):
        raise RuntimeError("relational fixtures require exact owned workspace fixtures")
    primary = {
        "label": "original",
        "key_name": "managed-smoke-api",
        **{
            k: str(uuid.UUID(original[k]))
            for k in (
                "organization_id",
                "workspace_id",
                "project_id",
            )
        },
    }
    scopes = []
    run_id = run.manifest["run_id"]  # Validated by fixture_scopes above.
    for source in [primary, *others]:
        scope = {k: source[k] for k in primary}
        scope["ids"] = {
            kind: str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    run_id + ":relational:" + scope["workspace_id"] + ":" + kind,
                )
            )
            for kind in (*FAMILIES, "agent", "run_test", "dataset")
        }
        scopes.append(scope)
    if len({s["workspace_id"] for s in scopes}) != 3:
        raise RuntimeError("relational workspace scopes are not disjoint")
    return {
        "version": 1,
        "run_id": run_id,
        "prefix": "relational-" + run_id,
        "scopes": scopes,
    }


def _plan(run, original=None):
    source = json.loads((run.directory / "source-fixture.json").read_text())
    if original is not None and original != source:
        raise RuntimeError("relational verifier received a different source fixture")
    others = json.loads((run.directory / "workspace-fixtures.json").read_text())[
        "scopes"
    ]
    return fixture_plan(run, source, others)


def _installed(run, original=None):
    plan = _plan(run, original)
    if json.loads((run.directory / "relational-fixture.json").read_text()) != plan:
        raise RuntimeError("relational fixture identity changed")
    return plan


def _digest(plan):
    return hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()


def _settings(options):
    return {
        "options": options,
        "rule_prompt": "",
        "multi_choice": False,
        "auto_annotate": False,
        "strategy": None,
    }


def _specs(plan, scope, phase="seeded"):
    """Model kwargs, with literal canonical model fields and no shadow schema."""
    changed = scope["label"] == "original" and phase != "seeded"
    suffix = "-updated" if changed else ""
    base = plan["prefix"] + "-" + scope["label"]
    tenant = {k: scope[k] for k in ("organization_id", "workspace_id")}
    ids = scope["ids"]
    choices = [
        scope["label"] + "-accepted" + suffix,
        scope["label"] + "-rejected" + suffix,
    ]
    options = [
        {"value": scope["label"] + "-" + code + suffix, "label": label}
        for code, label in zip(("accept_code", "reject_code"), choices, strict=True)
    ]
    return {
        "template": dict(
            name=base + "-template" + suffix,
            **tenant,
            config={"output": "choices"},
            choices=choices,
        ),
        "config": {
            "name": base + "-config",
            "project_id": scope["project_id"],
            "eval_template_id": ids["template"],
        },
        "agent": dict(
            agent_name=base + "-agent",
            agent_type="text",
            inbound=False,
            description="Isolated relational smoke",
            **tenant,
        ),
        "run_test": dict(
            name=base + "-run", agent_definition_id=ids["agent"], **tenant
        ),
        "simulation": {
            "name": base + "-simulation",
            "run_test_id": ids["run_test"],
            "eval_template_id": ids["template"],
        },
        "label": dict(
            name=base + "-label" + suffix,
            **tenant,
            project_id=scope["project_id"],
            type="categorical",
            settings=_settings(options),
        ),
        "dataset": dict(name=base + "-dataset", **tenant),
        "column": {
            "name": base + "-column" + suffix,
            "dataset_id": ids["dataset"],
            "data_type": "text",
            "source": "OTHERS",
        },
    }


def _matches(obj, fields):
    return all(
        str(getattr(obj, field)) == str(value)
        if field.endswith("_id")
        else getattr(obj, field) == value
        for field, value in fields.items()
    )


def _preflight(models, plan):
    for scope in plan["scopes"]:
        models["project"].no_workspace_objects.using("default").get(
            id=scope["project_id"],
            organization_id=scope["organization_id"],
            workspace_id=scope["workspace_id"],
            trace_type="observe",
        )
        models["key"].objects.get(
            name=scope["key_name"],
            organization_id=scope["organization_id"],
            workspace_id=scope["workspace_id"],
        )


def seed(run):
    """Create only this run's 24 canonical rows, atomically; exact retry is safe."""
    _guard(run, write=True)
    plan = _plan(run)
    path = run.directory / "relational-fixture.json"
    if path.exists() and json.loads(path.read_text()) != plan:
        raise RuntimeError("refusing to replace different relational fixture")
    models = _models()
    with _atomic():
        _preflight(models, plan)
        for scope in plan["scopes"]:
            for kind, fields in _specs(plan, scope).items():
                obj, _ = (
                    models[kind]
                    .all_objects.using("default")
                    .get_or_create(
                        id=scope["ids"][kind],
                        defaults=fields,
                    )
                )
                if obj.deleted or not _matches(obj, fields):
                    raise RuntimeError(
                        "existing relational fixture differs; not relabeling"
                    )
    save(path, plan)  # Evidence only, after real ORM commit; never stores secrets.
    return plan


def _previous_report(run, plan, phase):
    previous = PHASES[PHASES.index(phase) - 1]
    report = json.loads(
        (run.directory / f"catalog-relational-{previous}.json").read_text()
    )
    if (report.get("status"), report.get("phase"), report.get("fixture_sha256")) != (
        "passed",
        previous,
        _digest(plan),
    ):
        raise RuntimeError("previous relational API verification not proven")


def mutate(run, *, phase):
    """Admin-only update or dependency soft-delete; repeat performs no new saves."""
    _guard(run, write=True)
    if phase not in ("updated", "deleted"):
        raise ValueError("mutation phase must be updated or deleted")
    plan = _installed(run)
    _previous_report(run, plan, phase)
    models = _models()
    scope = plan["scopes"][0]
    old = _specs(plan, scope, "seeded" if phase == "updated" else "updated")
    target = _specs(plan, scope, "updated")
    touched = []
    with _atomic():
        _preflight(models, plan)
        records = {
            kind: models[kind].all_objects.using("default").get(id=identity)
            for kind, identity in scope["ids"].items()
        }
        parents = ("template", "label", "dataset")
        for kind, obj in records.items():
            if not (_matches(obj, old[kind]) or _matches(obj, target[kind])):
                raise RuntimeError("mutation target escaped exact relational fields")
            if obj.deleted and not (phase == "deleted" and kind in parents):
                raise RuntimeError("unexpected deleted relational fixture")
        children = {kind: records[kind].updated_at for kind in ("config", "simulation")}
        if phase == "updated":
            for kind in ("template", "label", "column"):
                obj = records[kind]
                if not _matches(obj, target[kind]):
                    for field, value in target[kind].items():
                        setattr(obj, field, value)
                    obj.save(using="default")
                    touched.append(kind)
        else:
            for kind in parents:
                if not records[kind].deleted:
                    records[kind].delete(using="default")
                    touched.append(kind)
        for kind, before in children.items():
            records[kind].refresh_from_db(using="default")
            if records[kind].updated_at != before or records[kind].deleted:
                raise RuntimeError("dependency test unexpectedly modified child config")
    evidence = {
        "phase": phase,
        "fixture_sha256": _digest(plan),
        "touched": touched,
        "child_config_rows_unchanged": True,
    }
    save(run.directory / f"relational-mutation-{phase}.json", evidence)
    return evidence


def _budget(deadline):
    if time.monotonic() >= deadline:
        raise RuntimeError("relational verification exceeded its bounded wall")


def _pages(client, endpoint, params, *, catalog, allow_empty=False, deadline=math.inf):
    """Bounded real API pagination. No retry or fallback for errors/corruption."""
    items, seen_cursors, seen_ids = [], set(), set()
    query = dict(params)
    field = "metrics" if catalog else "values"
    for _ in range(8):
        _budget(deadline)
        status, payload = request(client, endpoint, query)
        _budget(deadline)
        if not isinstance(payload, dict) or not isinstance(payload.get("result"), dict):
            raise RuntimeError(f"malformed relational {endpoint} response ({status})")
        result = payload["result"]
        if catalog:
            result = active_result(status, payload, allow_pending=True)
            if result is None:
                return None
        elif status != 200:
            raise RuntimeError(f"relational native API failed ({status})")
        elif allow_empty and not items and result == {"values": []}:
            return []  # Existing authorized-not-found native response contract.
        elif (
            result.get("query_complete") is not True
            or result.get("query_status") != "complete"
        ):
            raise RuntimeError("relational native values were not complete")
        page = result.get(field)
        if not isinstance(page, list) or any(
            not isinstance(item, dict) for item in page
        ):
            raise RuntimeError("malformed relational page")
        for item in page:
            identity = (
                item.get("property_id") if catalog else json.dumps(item, sort_keys=True)
            )
            if not identity or identity in seen_ids:
                raise RuntimeError("duplicate or missing relational page identity")
            seen_ids.add(identity)
        items.extend(page)
        cursor, more = result.get("next_cursor"), result.get("has_more")
        if more is False and cursor is None:
            return items
        if (
            more is not True
            or not isinstance(cursor, str)
            or not cursor
            or cursor in seen_cursors
        ):
            raise RuntimeError("invalid or repeated relational continuation")
        seen_cursors.add(cursor)
        query["cursor"] = cursor
    raise RuntimeError("relational pagination exceeded eight pages")


def _definition_queries(plan, scope):
    base = {"cursor_mode": "true", "page_size": 2, "search": plan["prefix"]}
    project = {
        "source": "traces",
        "project_ids": scope["project_id"],
        "category": "eval_metric",
    }
    return {
        "template": {**base, **project, "per_eval_config": "false"},
        "config": {**base, **project, "per_eval_config": "true"},
        "simulation": {
            **base,
            "source": "simulation",
            "category": "eval_metric",
            "agent_definition_id": scope["ids"]["agent"],
        },
        "label": {
            **base,
            "source": "traces",
            "category": "annotation_metric",
            "project_ids": scope["project_id"],
        },
        "column": {**base, "source": "datasets", "category": "custom_column"},
    }


def _expected(plan, scope, phase):
    if phase == "deleted" and scope["label"] == "original":
        return {}
    specs = _specs(plan, scope, phase)
    result = {}
    for kind, (property_kind, source, category, value_type, role) in FAMILIES.items():
        fields = {
            "property_id": property_kind + ":" + scope["ids"][kind],
            "property_kind": property_kind,
            "name": scope["ids"][kind],
            "display_name": specs[kind]["name"],
            "source": source,
            "category": category,
            "type": value_type,
            "role": role,
        }
        if kind in ("template", "config", "simulation"):
            fields.update(output_type="CHOICES", choices=specs["template"]["choices"])
        elif kind == "label":
            fields.update(
                output_type="categorical",
                choices=[v["label"] for v in specs["label"]["settings"]["options"]],
            )
        else:
            fields["output_type"] = "text"
        result[fields["property_id"]] = fields
    return result


def _definitions(client, plan, scope, phase, *, deadline=math.inf):
    expected = _expected(plan, scope, phase)
    for family, params in _definition_queries(plan, scope).items():
        rows = _pages(client, "metrics", params, catalog=True, deadline=deadline)
        if rows is None:
            return False
        # Simulation also admits this workspace's default template (source=all).
        kinds = ("simulation", "template") if family == "simulation" else (family,)
        wanted = {
            FAMILIES[k][0] + ":" + scope["ids"][k] for k in kinds
        } & expected.keys()
        actual = {row["property_id"]: row for row in rows}
        foreign = {
            identity
            for other in plan["scopes"]
            if other != scope
            for identity in _expected(plan, other, "seeded")
        }
        if actual.keys() & foreign:
            raise RuntimeError("foreign relational definition exposed")
        if actual.keys() != wanted:
            return False  # Valid selected build has not incorporated ORM commit yet.
        if any(
            any(actual[identity].get(k) != v for k, v in expected[identity].items())
            for identity in wanted
        ):
            return False
    return True


def _value_query(scope, kind):
    return {
        "property_id": FAMILIES[kind][0] + ":" + scope["ids"][kind],
        "source": "simulation" if kind == "simulation" else "traces",
        "project_ids": scope["project_id"],
        "page_size": 1,
    }


def _values(client, plan, scope, phase, *, deadline=math.inf):
    deleted = phase == "deleted" and scope["label"] == "original"
    specs = _specs(plan, scope, phase)
    for kind in ("template", "config", "simulation", "label"):
        options = (
            specs["label"]["settings"]["options"]
            if kind == "label"
            else [{"value": v, "label": v} for v in specs["template"]["choices"]]
        )
        if deleted:
            options = []
        params = _value_query(scope, kind)
        actual = _pages(
            client,
            "filter_values",
            params,
            catalog=False,
            allow_empty=deleted,
            deadline=deadline,
        )
        if actual != options:
            raise RuntimeError(
                f"wrong relational configured values: {scope['label']}/{kind}"
            )
        if options:
            found = _pages(
                client,
                "filter_values",
                {**params, "search": options[1]["label"].upper()},
                catalog=False,
                deadline=deadline,
            )
            if found != [options[1]]:
                raise RuntimeError(
                    "relational label search did not return stored value"
                )


def _foreign_checks(clients, plan, *, deadline=math.inf):
    own, *others = plan["scopes"]
    client = clients["original"]
    denied = []
    for other in others:
        for kind in ("template", "config", "simulation", "label"):
            params = {**_value_query(other, kind), "project_ids": own["project_id"]}
            if (
                _pages(
                    client,
                    "filter_values",
                    params,
                    catalog=False,
                    allow_empty=True,
                    deadline=deadline,
                )
                != []
            ):
                raise RuntimeError("foreign relational configured values exposed")
        for projects in (
            other["project_id"],
            own["project_id"] + "," + other["project_id"],
        ):
            _budget(deadline)
            status, _ = request(
                client,
                "metrics",
                {
                    **_definition_queries(plan, own)["template"],
                    "project_ids": projects,
                },
            )
            _budget(deadline)
            if status not in (400, 403):
                raise RuntimeError(
                    "foreign/mixed relational project scope not rejected"
                )
            denied.append(status)
        _budget(deadline)
        status, _ = request(
            client,
            "metrics",
            {
                **_definition_queries(plan, own)["simulation"],
                "agent_definition_id": other["ids"]["agent"],
            },
        )
        _budget(deadline)
        if status not in (400, 403):
            raise RuntimeError("foreign relational agent scope not rejected")
        denied.append(status)
    return denied


def _clients(plan, key):
    from rest_framework.test import APIClient

    models = _models()
    clients = {}
    for scope in plan["scopes"]:
        scoped_key = (
            key
            if scope["label"] == "original"
            else models["key"].objects.get(
                name=scope["key_name"],
                workspace_id=scope["workspace_id"],
                organization_id=scope["organization_id"],
            )
        )
        if any(
            str(getattr(scoped_key, field)) != scope[field]
            for field in ("workspace_id", "organization_id")
        ):
            raise RuntimeError("relational API key belongs to another scope")
        client = APIClient()
        client.credentials(
            HTTP_X_API_KEY=scoped_key.api_key,
            HTTP_X_SECRET_KEY=scoped_key.secret_key,
            HTTP_X_WORKSPACE_ID=scope["workspace_id"],
        )
        clients[scope["label"]] = client
    return clients


def verify(run, fixture, key, *, phase="seeded", timeout=90):
    """Read-only real API verification; timeout is a bounded convergence budget."""
    started = time.monotonic()
    _guard(run, write=False)
    if phase not in PHASES or not math.isfinite(timeout) or not 0 < timeout <= 300:
        raise ValueError(
            "invalid relational phase or timeout (must be 0 < seconds <= 300)"
        )
    plan = _installed(run, fixture)
    if phase != "seeded":
        mutation = json.loads(
            (run.directory / f"relational-mutation-{phase}.json").read_text()
        )
        if mutation.get("phase") != phase or mutation.get("fixture_sha256") != _digest(
            plan
        ):
            raise RuntimeError("relational mutation identity not proven")
    report = {
        "status": "failed",
        "phase": phase,
        "fixture_sha256": _digest(plan),
        "definition_families": list(FAMILIES),
        "workspaces": 3,
        "dataset_values": "definitions_only_not_tested",
    }
    deadline = started + timeout
    try:
        _budget(deadline)
        clients = _clients(plan, key)
        while True:
            _budget(deadline)
            complete = all(
                _definitions(clients[s["label"]], plan, s, phase, deadline=deadline)
                for s in plan["scopes"]
            )
            _budget(deadline)
            if complete:
                break
            time.sleep(min(1, max(0, deadline - time.monotonic())))
        for scope in plan["scopes"]:
            _budget(deadline)
            _values(clients[scope["label"]], plan, scope, phase, deadline=deadline)
            _budget(deadline)
        report["denied_scope_statuses"] = _foreign_checks(
            clients, plan, deadline=deadline
        )
        report["property_ids"] = {
            s["label"]: sorted(_expected(plan, s, phase)) for s in plan["scopes"]
        }
        _budget(deadline)
        report["status"] = "passed"
        return report
    finally:
        report["elapsed_seconds"] = time.monotonic() - started
        save(run.directory / f"catalog-relational-{phase}.json", report)
