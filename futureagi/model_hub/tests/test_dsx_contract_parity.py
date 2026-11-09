"""DSX strict-contract parity (lane L2, Capability contract(b)).

Dev answers 400 "Unknown field." to a request that sends a top-level field its
serializer does not declare on every route whose handler is
validated_request(..., reject_unknown_fields=True). Which routes those are is
read from the backend here, never listed by hand:

1. the routes in frontend/src/api/contracts/strictRoutes.generated.js are
   exactly the routes this branch's URL resolver serves with such a handler
   (every swagger path and body method, resolved and introspected). A new or
   removed strict route fails this test until the file is regenerated:
   DSX_WRITE_STRICT_ROUTES=1 pytest model_hub/tests/test_dsx_contract_parity.py
2. every strict route the frontend calls (its path is in utils/axios.js)
   rejects an unknown field before the view runs;
3. each such route's request serializer declares exactly the fields of the
   swagger definition the frontend reads (no stale contract).

The frontend sweep (frontend/src/api/contracts/__tests__/dsxRequestSweep.test.js)
then checks every call site of every route in that file against the contract.

It also pins the two request shapes L2 changed against the real views, with
two organizations: an experiment delete is DELETE (POST is 405), and an
experiment stop takes only an empty body; org B can neither delete nor stop
org A's experiment.
"""

import json
import os
import re
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import Resolver404, resolve
from rest_framework import serializers as drf_serializers
from rest_framework import status

from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from accounts.models.workspace import Workspace, WorkspaceMembership
from model_hub.models.choices import (
    DatasetSourceChoices,
    DataTypeChoices,
    SourceChoices,
    StatusType,
)
from model_hub.models.develop_dataset import Column, Dataset
from model_hub.models.experiments import ExperimentsTable
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles

REPO = Path(__file__).resolve().parents[3]
STRICT_ROUTES_JS = (
    REPO / "frontend" / "src" / "api" / "contracts" / "strictRoutes.generated.js"
)
AXIOS_JS = REPO / "frontend" / "src" / "utils" / "axios.js"
SWAGGER = REPO / "api_contracts" / "openapi" / "swagger.json"
PROBE = "dsx_l2_probe_field"
BODY_METHODS = ("post", "put", "patch", "delete")
PAIR_RE = re.compile(r'\[\s*"(\w+)",\s*"([^"]+)"\s*,?\s*\]')


def _frontend_strict_routes():
    text = STRICT_ROUTES_JS.read_text()
    pairs = PAIR_RE.findall(text)
    assert pairs, "no routes in strictRoutes.generated.js"
    return sorted(set(pairs))


STRICT_ROUTES = _frontend_strict_routes()
SWAGGER_DOC = json.loads(SWAGGER.read_text())
AXIOS_TEXT = AXIOS_JS.read_text()
# The strict routes a frontend call site can reach: utils/axios.js names
# every URL the UI builds.
CALLED_ROUTES = [(m, p) for m, p in STRICT_ROUTES if f'"{p}"' in AXIOS_TEXT]


def _concrete(path, filler=None):
    return re.sub(r"\{[^}]+\}", lambda _m: filler or str(uuid.uuid4()), path)


def _resolve_url(path):
    """(match, concrete url) of a swagger path, or (None, None)."""
    for filler in (None, "1", "a"):
        url = _concrete(path, filler)
        try:
            return resolve(url), url
        except Resolver404:
            continue
    return None, None


def _resolve(path):
    return _resolve_url(path)[0]


def _handler(match, method):
    func = match.func
    view_cls = getattr(func, "view_class", None) or getattr(func, "cls", None)
    if view_cls is None:
        return None
    actions = getattr(func, "actions", None) or {}
    name = actions.get(method, method) if actions else method
    return getattr(view_cls, name, None)


def _cells(fn):
    names = getattr(getattr(fn, "__code__", None), "co_freevars", ()) or ()
    cells = getattr(fn, "__closure__", None) or ()
    out = {}
    for name, cell in zip(names, cells, strict=True):
        try:
            out[name] = cell.cell_contents
        except ValueError:
            continue
    return out


def _rejects_unknown_body(handler, method):
    """True when validated_request answers an unknown body field with 400.

    validated_request keeps its options in the closure of the
    ``prepare_request`` its wrapper calls; decorators above it keep the
    wrapper reachable through ``__wrapped__``.
    """
    fn = handler
    for _ in range(8):
        if fn is None:
            return False
        prep = _cells(fn).get("prepare_request")
        if prep is not None:
            opts = _cells(prep)
            methods = opts.get("request_method_set")
            if opts.get("read_post") and method == "post":
                return True
            return bool(
                opts.get("request_serializer") is not None
                and opts.get("reject_unknown_fields")
                and opts.get("strict_request_validation", True)
                and (methods is None or method.upper() in methods)
            )
        fn = getattr(fn, "__wrapped__", None)
    return False


def _backend_strict_routes():
    found = set()
    for path, ops in SWAGGER_DOC["paths"].items():
        methods = [m for m in BODY_METHODS if m in ops]
        if not methods:
            continue
        match = _resolve(path)
        if match is None:
            continue
        for method in methods:
            if _rejects_unknown_body(_handler(match, method), method):
                found.add((method, path))
    return sorted(found)


def _generated_text(routes):
    lines = [
        "// GENERATED by futureagi/model_hub/tests/test_dsx_contract_parity.py",
        "// (DSX_WRITE_STRICT_ROUTES=1). Do not edit: every [method, path] whose",
        "// handler on this branch is validated_request(..., reject_unknown_fields=",
        "// True), read from the URL resolver over every swagger path. Dev answers an",
        '// unknown top-level field on these with 400 "Unknown field.".',
        "export const STRICT_ROUTES = Object.freeze([",
    ]
    lines += [f'  ["{m}", "{p}"],' for m, p in routes]
    lines.append("]);")
    return "\n".join(lines) + "\n"


def test_frontend_strict_routes_are_the_backend_strict_routes():
    backend = _backend_strict_routes()
    if os.environ.get("DSX_WRITE_STRICT_ROUTES") == "1":
        STRICT_ROUTES_JS.write_text(_generated_text(backend))
    frontend = _frontend_strict_routes()
    missing = sorted(set(backend) - set(frontend))
    stale = sorted(set(frontend) - set(backend))
    assert not missing and not stale, (
        f"strictRoutes.generated.js is stale: missing {missing}, not strict "
        f"{stale}; regenerate with DSX_WRITE_STRICT_ROUTES=1"
    )
    # The derivation finds the routes L2 is known to have changed.
    assert ("post", "/model-hub/experiments/v2/") in backend
    assert ("post", "/model-hub/columns/{column_id}/rerun-operation/") in backend


def test_some_body_routes_are_not_strict():
    # A control for the introspection: dev's loose routes stay out.
    assert (
        "put",
        "/model-hub/develops/{dataset_id}/edit_dataset_behavior/",
    ) not in set(_backend_strict_routes())


def _definition_of(path, method):
    op = SWAGGER_DOC["paths"][path][method]
    body = [p for p in op.get("parameters", []) if p.get("in") == "body"]
    assert body, f"{method} {path} has no body parameter in swagger.json"
    return body[0]["schema"]["$ref"].rsplit("/", 1)[-1]


def _request_serializer(path, method):
    handler = _handler(_resolve(path), method)
    overrides = getattr(handler, "_swagger_auto_schema", {}) or {}
    if method in overrides and isinstance(overrides[method], dict):
        overrides = overrides[method]
    body = overrides.get("request_body")
    assert body is not None, f"{method} {path}: no request serializer declared"
    if isinstance(body, type) and issubclass(body, drf_serializers.BaseSerializer):
        return body()
    return body


@pytest.mark.parametrize("method,path", CALLED_ROUTES)
def test_contract_definition_matches_the_serializer(method, path):
    definition = _definition_of(path, method)
    props = set(SWAGGER_DOC["definitions"][definition].get("properties", {}))
    fields = set(_request_serializer(path, method).fields)
    assert fields == props, (
        f"{method} {path}: serializer fields {sorted(fields)} != "
        f"swagger {definition} {sorted(props)} (regenerate the contract)"
    )


@pytest.mark.django_db
@pytest.mark.parametrize("method,path", CALLED_ROUTES)
def test_route_rejects_an_unknown_top_level_field(auth_client, method, path):
    url = _resolve_url(path)[1]
    if path.endswith("create-dataset-from-local-file/"):
        payload = {
            "file": SimpleUploadedFile("rows.csv", b"q,a\n1,2\n", "text/csv"),
            "new_dataset_name": "dsx-l2-probe",
            PROBE: "1",
        }
        response = getattr(auth_client, method)(url, payload, format="multipart")
    else:
        response = getattr(auth_client, method)(url, {PROBE: 1}, format="json")
        if response.status_code == status.HTTP_415_UNSUPPORTED_MEDIA_TYPE:
            response = getattr(auth_client, method)(
                url, {PROBE: "1"}, format="multipart"
            )
    if response.status_code in (
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
    ):
        pytest.skip(
            f"{method} {path}: refused before the body ({response.status_code})"
        )
    assert response.status_code == status.HTTP_400_BAD_REQUEST, (
        method,
        path,
        response.status_code,
    )
    text = json.dumps(response.data, default=str)
    assert PROBE in text and "Unknown field." in text, text


# ---------------------------------------------------------------- tenants


def _org_user(name):
    org = Organization.objects.create(name=name)
    user = User.objects.create_user(
        email=f"dsx-l2-{uuid.uuid4().hex[:8]}@futureagi.com",
        password="testpassword123",
        name=name,
        organization=org,
        organization_role=OrganizationRoles.OWNER,
    )
    membership, _ = OrganizationMembership.no_workspace_objects.get_or_create(
        user=user,
        organization=org,
        defaults={
            "role": OrganizationRoles.OWNER,
            "level": Level.OWNER,
            "is_active": True,
        },
    )
    ws = Workspace.objects.create(
        name=f"{name} workspace",
        organization=org,
        is_default=True,
        is_active=True,
        created_by=user,
    )
    WorkspaceMembership.no_workspace_objects.get_or_create(
        user=user,
        workspace=ws,
        defaults={
            "role": "Workspace Owner",
            "level": Level.OWNER,
            "is_active": True,
            "organization_membership": membership,
        },
    )
    return user, ws


def _client(user, ws):
    from conftest import WorkspaceAwareAPIClient

    client = WorkspaceAwareAPIClient()
    client.force_authenticate(user=user)
    client.set_workspace(ws)
    return client


def _experiment(user, ws, *, state=StatusType.COMPLETED.value):
    ds = Dataset.objects.create(
        name=f"dsx-l2-{uuid.uuid4().hex[:6]}",
        organization=user.organization,
        workspace=ws,
        source=DatasetSourceChoices.BUILD.value,
    )
    col = Column.objects.create(
        name="out",
        dataset=ds,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.RUN_PROMPT.value,
    )
    return ExperimentsTable.objects.create(
        name=f"dsx-l2-exp-{uuid.uuid4().hex[:6]}",
        dataset=ds,
        column=col,
        status=state,
    )


@pytest.fixture
def two_orgs(db):
    a_user, a_ws = _org_user("DSX L2 org A")
    b_user, b_ws = _org_user("DSX L2 org B")
    a = _client(a_user, a_ws)
    b = _client(b_user, b_ws)
    yield {"a_user": a_user, "a_ws": a_ws, "a": a, "b": b}
    a.stop_workspace_injection()
    b.stop_workspace_injection()


DELETE_URL = "/model-hub/experiments/v2/delete/"


@pytest.mark.django_db
def test_experiment_delete_is_delete_and_org_b_cannot_delete_org_a(two_orgs):
    exp = _experiment(two_orgs["a_user"], two_orgs["a_ws"])
    body = {"experiment_ids": [str(exp.id)]}

    # The DSX UI used POST here: dev answers 405 and nothing is deleted.
    posted = two_orgs["a"].post(DELETE_URL, body, format="json")
    assert posted.status_code == status.HTTP_405_METHOD_NOT_ALLOWED
    exp.refresh_from_db()
    assert exp.deleted is False

    # Org B, with A's id, deletes nothing.
    other = two_orgs["b"].delete(DELETE_URL, body, format="json")
    assert other.status_code == status.HTTP_404_NOT_FOUND
    exp.refresh_from_db()
    assert exp.deleted is False

    # Org A's DELETE (what the UI now sends) deletes it.
    with patch("tfc.temporal.experiments.cancel_experiment_workflow"):
        mine = two_orgs["a"].delete(DELETE_URL, body, format="json")
    assert mine.status_code == status.HTTP_200_OK, mine.data
    exp.refresh_from_db()
    assert exp.deleted is True


@pytest.mark.django_db
def test_experiment_stop_takes_an_empty_body_and_is_org_scoped(two_orgs):
    exp = _experiment(
        two_orgs["a_user"], two_orgs["a_ws"], state=StatusType.RUNNING.value
    )
    url = f"/model-hub/experiments/v2/{exp.id}/stop/"

    # An unknown selected run is refused before anything stops.
    per_run = two_orgs["a"].post(url, {"run_ids": [str(uuid.uuid4())]}, format="json")
    assert per_run.status_code == status.HTTP_404_NOT_FOUND
    exp.refresh_from_db()
    assert exp.status == StatusType.RUNNING.value

    cancel = {"main_cancelled": True, "rerun_cancelled": 0}
    with patch(
        "tfc.temporal.experiments.cancel_all_experiment_workflows",
        return_value=cancel,
    ):
        # Org B cannot stop org A's experiment.
        other = two_orgs["b"].post(url, {}, format="json")
        assert other.status_code == status.HTTP_404_NOT_FOUND
        exp.refresh_from_db()
        assert exp.status == StatusType.RUNNING.value

        # Org A's `{}` (what the UI now sends) stops the whole experiment.
        mine = two_orgs["a"].post(url, {}, format="json")
    assert mine.status_code == status.HTTP_200_OK, mine.data
    exp.refresh_from_db()
    assert exp.status == StatusType.CANCELLED.value
