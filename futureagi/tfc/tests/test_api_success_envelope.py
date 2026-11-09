"""OpenAPI must document the success envelope the platform really returns.

``GeneralMethods.success_response(result)`` answers ``{"status": true,
"result": <result>}`` with HTTP 200 (``create_response`` does the same with
201). Actions that return through those helpers without an explicit
``responses=`` contract used to be generated with drf-yasg's bare serializer
schema and its guessed status (201 for POST), so every client generated from
the spec decoded the envelope as the inner object.

These tests pin that generation wraps the inferred schema in the envelope at
the runtime status, that explicitly annotated actions and the default error
contract are untouched, and that no view can start returning the envelope
while the spec still describes something else.
"""

import inspect
import json
import re
from functools import lru_cache
from pathlib import Path

from django.urls import include, path
from drf_yasg import openapi
from drf_yasg.app_settings import swagger_settings
from drf_yasg.codecs import OpenAPICodecJson
from drf_yasg.generators import OpenAPISchemaGenerator
from drf_yasg.management.commands.generate_swagger import Command
from drf_yasg.utils import swagger_auto_schema
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.generics import GenericAPIView
from rest_framework.response import Response
from rest_framework.routers import SimpleRouter
from rest_framework.settings import api_settings
from rest_framework.test import APIRequestFactory

from tfc.utils.api_contracts import validated_request
from tfc.utils.general_methods import GeneralMethods

# Same mock request URL as scripts/generate-openapi-schema.sh.
_API_URL = "http://localhost:8000"
_ITEM = {"name": "Future AGI"}


def _envelope(result_schema):
    """The success envelope as generated, mirroring hand-written *ApiResponse."""

    return {
        "required": ["result"],
        "type": "object",
        "properties": {
            "status": {"title": "Status", "type": "boolean", "default": True},
            "result": result_schema,
        },
    }


# ---------------------------------------------------------------------------
# Synthetic views: one of each shape the generator must tell apart.
# ---------------------------------------------------------------------------


class _EnvelopeItemSerializer(serializers.Serializer):
    name = serializers.CharField()


class _EnvelopeItemApiResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = _EnvelopeItemSerializer()


class _ListView(GenericAPIView):
    # drf-yasg infers an unpaginated array of the serializer for these GETs.
    serializer_class = _EnvelopeItemSerializer
    pagination_class = None


class _UnannotatedEnvelopeView(_ListView):
    _gm = GeneralMethods()

    def get(self, request):
        return self._gm.success_response([_ITEM])


class _AnnotatedEnvelopeView(_ListView):
    @validated_request(responses={200: _EnvelopeItemApiResponseSerializer})
    def get(self, request):
        return GeneralMethods().success_response(_ITEM)


class _BareView(_ListView):
    def get(self, request):
        return Response([_ITEM])


def _module_level_success(result):
    return GeneralMethods().success_response(result)


class _ModuleHelperEnvelopeView(_ListView):
    # Not routed: only the regression guard's reachability check reads it.
    def get(self, request):
        return _module_level_success([_ITEM])


class _OptedOutEnvelopeView(_ListView):
    @swagger_auto_schema(success_envelope=False)
    def get(self, request):
        return GeneralMethods().success_response([_ITEM])


class _OptedOutValidatedEnvelopeView(_ListView):
    @validated_request(query_serializer=_EnvelopeItemSerializer, success_envelope=False)
    def get(self, request):
        return GeneralMethods().success_response([_ITEM])


class _MixedEnvelopeView(_ListView):
    def get(self, request):
        if request.query_params.get("bare"):
            return Response([_ITEM])
        return GeneralMethods().success_response([_ITEM])


class _ReassignedParameterView(_ListView):
    _gm = GeneralMethods()

    def _respond(self, response=None):
        if response is None:
            response = self._gm.success_response([_ITEM])
        return response

    def get(self, request):
        # The caller's bare Response is what answers; the helper's own
        # success_response() branch never runs for this call.
        return self._respond(Response([_ITEM]))


class _EnvelopeItemViewSet(viewsets.GenericViewSet):
    serializer_class = _EnvelopeItemSerializer
    pagination_class = None
    _gm = GeneralMethods()

    def list(self, request):
        return self._gm.success_response([_ITEM])

    def create(self, request):
        return self._gm.create_response(_ITEM)

    def retrieve(self, request, pk=None):
        response = self._gm.success_response(_ITEM)
        return response

    def update(self, request, pk=None, partial=False):
        if not request.data:
            return self._gm.bad_request("A body is required.")
        return self._gm.success_response(_ITEM)

    def partial_update(self, request, pk=None):
        return self.update(request, pk=pk, partial=True)

    def destroy(self, request, pk=None):
        return self._gm.success_response("Item deleted.")

    @action(detail=False, methods=["post"])
    def touch(self, request):
        return self._gm.success_response(_ITEM)

    @action(detail=False, methods=["post"])
    def accept(self, request):
        return self._gm.success_response(_ITEM, status=status.HTTP_202_ACCEPTED)


class _ForcedEnvelopeViewSet(viewsets.GenericViewSet):
    """The tuple-returning helper hides ``error`` from the static analysis."""

    serializer_class = _EnvelopeItemSerializer
    pagination_class = None
    _gm = GeneralMethods()

    def _checked(self, request):
        if not request.data:
            return None, self._gm.bad_request("A body is required.")
        return dict(request.data), None

    @swagger_auto_schema(success_envelope=True)
    def create(self, request):
        item, error = self._checked(request)
        if error is not None:
            return error
        return self._gm.success_response(item)

    @action(detail=False, methods=["post"])
    def unproven(self, request):
        item, error = self._checked(request)
        if error is not None:
            return error
        return self._gm.success_response(item)


_router = SimpleRouter()
_router.register("items", _EnvelopeItemViewSet, basename="envelope-item")
_router.register("forced", _ForcedEnvelopeViewSet, basename="envelope-forced")

_SYNTHETIC_PATTERNS = [
    path("envelope/unannotated/", _UnannotatedEnvelopeView.as_view()),
    path("envelope/annotated/", _AnnotatedEnvelopeView.as_view()),
    path("envelope/bare/", _BareView.as_view()),
    path("envelope/opted-out/", _OptedOutEnvelopeView.as_view()),
    path("envelope/opted-out-validated/", _OptedOutValidatedEnvelopeView.as_view()),
    path("envelope/mixed/", _MixedEnvelopeView.as_view()),
    path("envelope/reassigned-parameter/", _ReassignedParameterView.as_view()),
    path("envelope/", include(_router.urls)),
]


def _as_json(schema):
    return json.loads(OpenAPICodecJson(validators=[]).encode(schema))


@lru_cache(maxsize=1)
def _synthetic_schema():
    generator = OpenAPISchemaGenerator(
        info=openapi.Info(title="Envelope", default_version="v1"),
        patterns=_SYNTHETIC_PATTERNS,
    )
    return _as_json(generator.get_schema(request=None, public=True))


def _synthetic_responses(path_suffix, method):
    paths = _synthetic_schema()["paths"]
    (match,) = [key for key in paths if key.endswith(path_suffix)]
    return paths[match][method]["responses"]


def test_unannotated_success_response_view_documents_the_envelope():
    responses = _synthetic_responses("/unannotated/", "get")

    assert responses["200"]["schema"] == _envelope(
        {"type": "array", "items": {"$ref": "#/definitions/_EnvelopeItem"}}
    )

    # The documented envelope is what the view really answers.
    response = _UnannotatedEnvelopeView.as_view()(APIRequestFactory().get("/"))
    assert response.status_code == 200
    assert response.data == {"status": True, "result": [_ITEM]}
    assert set(response.data) == set(responses["200"]["schema"]["properties"])


def test_annotated_success_response_view_keeps_its_declared_contract():
    responses = _synthetic_responses("/annotated/", "get")

    assert responses["200"]["schema"] == {
        "$ref": "#/definitions/_EnvelopeItemApiResponse"
    }


def test_views_that_do_not_prove_the_envelope_keep_the_inferred_schema():
    bare = {"type": "array", "items": {"$ref": "#/definitions/_EnvelopeItem"}}

    assert _synthetic_responses("/bare/", "get")["200"]["schema"] == bare
    assert _synthetic_responses("/opted-out/", "get")["200"]["schema"] == bare
    assert _synthetic_responses("/opted-out-validated/", "get")["200"]["schema"] == bare
    # A bare 2xx branch next to an enveloped one cannot be expressed as one
    # Swagger 2.0 schema; generation leaves it for an explicit responses=.
    assert _synthetic_responses("/mixed/", "get")["200"]["schema"] == bare


def test_a_reassigned_parameter_still_carries_the_callers_response():
    # The helper assigns success_response() to its parameter only when the
    # caller passed none; this caller passes a bare Response, so the action is
    # not proven to answer the envelope and keeps the inferred schema.
    bare = {"type": "array", "items": {"$ref": "#/definitions/_EnvelopeItem"}}

    assert (
        _synthetic_responses("/reassigned-parameter/", "get")["200"]["schema"] == bare
    )

    response = _ReassignedParameterView.as_view()(APIRequestFactory().get("/"))
    assert response.status_code == 200
    assert response.data == [_ITEM]


def test_success_envelope_override_documents_what_the_analysis_cannot_prove():
    item = {"$ref": "#/definitions/_EnvelopeItem"}

    # Without the override an unprovable action keeps drf-yasg's inference.
    unproven = _synthetic_responses("/forced/unproven/", "post")
    assert unproven["201"]["schema"] == item
    assert "200" not in unproven

    # success_envelope=True wraps it at success_response()'s 200.
    forced = _synthetic_responses("/forced/", "post")
    assert forced["200"]["schema"] == _envelope(item)
    assert "201" not in forced
    assert forced["default"] == {
        "description": "Default error response",
        "schema": {"$ref": "#/definitions/ManagementAPIErrorResponse"},
    }

    # The override documents what the action really answers.
    create = _ForcedEnvelopeViewSet.as_view({"post": "create"})
    response = create(APIRequestFactory().post("/", _ITEM, format="json"))
    assert response.status_code == 200
    assert response.data == {"status": True, "result": _ITEM}


def test_viewset_actions_document_the_envelope_at_the_runtime_status():
    item = {"$ref": "#/definitions/_EnvelopeItem"}

    list_responses = _synthetic_responses("/items/", "get")
    assert list_responses["200"]["schema"] == _envelope(
        {"type": "array", "items": item}
    )
    # create_response() answers 201.
    assert _synthetic_responses("/items/", "post")["201"]["schema"] == _envelope(item)
    # A local variable holding success_response() is still the envelope.
    assert _synthetic_responses("/items/{id}/", "get")["200"]["schema"] == _envelope(
        item
    )
    assert _synthetic_responses("/items/{id}/", "put")["200"]["schema"] == _envelope(
        item
    )
    # partial_update delegates to update(); the helper call is followed.
    assert _synthetic_responses("/items/{id}/", "patch")["200"]["schema"] == _envelope(
        item
    )

    # success_response() answers 200 for a POST action, not drf-yasg's guess.
    touch = _synthetic_responses("/items/touch/", "post")
    assert touch["200"]["schema"] == _envelope(item)
    assert "201" not in touch

    accept = _synthetic_responses("/items/accept/", "post")
    assert accept["202"]["schema"] == _envelope(item)
    assert "201" not in accept


def test_success_envelope_needs_an_inferred_payload_to_wrap():
    # drf-yasg infers no body for DELETE; the generator does not invent one.
    responses = _synthetic_responses("/items/{id}/", "delete")

    assert responses["204"] == {"description": ""}
    assert "200" not in responses


def test_success_envelope_keeps_the_default_error_contract():
    for suffix, method in [
        ("/unannotated/", "get"),
        ("/items/", "post"),
        ("/items/touch/", "post"),
        ("/items/{id}/", "patch"),
    ]:
        assert _synthetic_responses(suffix, method)["default"] == {
            "description": "Default error response",
            "schema": {"$ref": "#/definitions/ManagementAPIErrorResponse"},
        }


# ---------------------------------------------------------------------------
# The generated Management API contract (same generator and mock request as
# scripts/generate-openapi-schema.sh, in process).
# ---------------------------------------------------------------------------


def _operation_path(path_, prefix):
    suffix = path_[len(prefix) :]
    return suffix if suffix.startswith("/") else "/" + suffix


@lru_cache(maxsize=1)
def _generated_contract():
    operations = {}

    class _RecordingGenerator(OpenAPISchemaGenerator):
        def get_operation(self, view, path_, prefix, method, components, request):
            operation = super().get_operation(
                view, path_, prefix, method, components, request
            )
            if operation is not None:
                operations[(_operation_path(path_, prefix), method.lower())] = (
                    view,
                    self.get_overrides(view, method),
                )
            return operation

    api_version = api_settings.DEFAULT_VERSION
    request = Command().get_mock_request(_API_URL, "json")
    if api_version:
        request.version = api_version
    generator = _RecordingGenerator(
        info=swagger_settings.DEFAULT_INFO, version=api_version, url=_API_URL
    )
    schema = _as_json(generator.get_schema(request=request, public=True))
    return schema, operations


def _checked_in_swagger():
    repo_root = Path(__file__).resolve().parents[3]
    with (repo_root / "api_contracts" / "openapi" / "swagger.json").open() as f:
        return json.load(f)


def _generated_responses(path_, method):
    return _generated_contract()[0]["paths"][path_][method]["responses"]


def test_tracer_dashboard_crud_documents_the_success_envelope():
    dashboard = "/tracer/dashboard/"
    detail = "/tracer/dashboard/{id}/"

    assert _generated_responses(dashboard, "get")["200"]["schema"] == _envelope(
        {"type": "array", "items": {"$ref": "#/definitions/Dashboard"}}
    )
    assert _generated_responses(detail, "get")["200"]["schema"] == _envelope(
        {"$ref": "#/definitions/DashboardDetail"}
    )

    # create answers success_response(...) with HTTP 200, not drf-yasg's 201.
    create = _generated_responses(dashboard, "post")
    assert create["200"]["schema"] == _envelope(
        {"$ref": "#/definitions/DashboardDetail"}
    )
    assert "201" not in create

    assert _generated_responses(detail, "patch")["200"]["schema"] == _envelope(
        {"$ref": "#/definitions/DashboardDetail"}
    )


def test_unprovable_success_response_actions_document_the_envelope():
    # Both answer success_response(...) with HTTP 200; the static analysis
    # cannot prove it (a tuple-unpacked error response; a nested finish()
    # closure), so they declare success_envelope=True. The envelope wraps the
    # schema drf-yasg already infers; no payload shape is invented.
    feedback = _generated_responses("/model-hub/feedback/", "post")
    assert feedback["200"]["schema"] == _envelope({"$ref": "#/definitions/Feedback"})
    assert "201" not in feedback

    metrics = _generated_responses("/tracer/project/get_user_metrics/", "post")
    assert metrics["200"]["schema"] == _envelope(
        {"$ref": "#/definitions/ProjectUserMetricsRequest"}
    )
    assert "201" not in metrics


def test_annotated_dashboard_query_is_byte_identical():
    path_ = "/tracer/dashboard/query/"
    generated = _generated_contract()[0]
    checked_in = _checked_in_swagger()

    assert json.dumps(generated["paths"][path_]["post"]) == json.dumps(
        checked_in["paths"][path_]["post"]
    )
    assert json.dumps(
        generated["definitions"]["DashboardQueryApiResponse"]
    ) == json.dumps(checked_in["definitions"]["DashboardQueryApiResponse"])


def test_default_error_response_is_unchanged():
    generated = _generated_contract()[0]
    checked_in = _checked_in_swagger()

    for path_, method in [
        ("/tracer/dashboard/", "get"),
        ("/tracer/dashboard/", "post"),
        ("/tracer/dashboard/{id}/", "get"),
        ("/tracer/dashboard/{id}/", "patch"),
        ("/tracer/dashboard/query/", "post"),
        ("/model-hub/feedback/", "post"),
        ("/tracer/project/get_user_metrics/", "post"),
    ]:
        assert json.dumps(
            generated["paths"][path_][method]["responses"]["default"]
        ) == json.dumps(checked_in["paths"][path_][method]["responses"]["default"])
    assert json.dumps(
        generated["definitions"]["ManagementAPIErrorResponse"]
    ) == json.dumps(checked_in["definitions"]["ManagementAPIErrorResponse"])


# ---------------------------------------------------------------------------
# Regression guard: every operation whose view can return the success
# envelope must document it, carry an explicit 2xx responses= contract, opt
# out explicitly with success_envelope=False, or be listed below with the
# reason generation cannot model it.
# ---------------------------------------------------------------------------

_NO_INFERRED_PAYLOAD = (
    "drf-yasg infers no 2xx body (DELETE 204 or no serializer); the envelope "
    "has nothing to wrap, so this needs an explicit responses= contract. "
    "Until then the spec is wrong at the status level: the runtime answers "
    "200 {status, result}, and a Swagger 2.0 client that routes undeclared "
    "statuses to 'default' decodes that 200 as ManagementAPIErrorResponse"
)
_MIXED = (
    "one branch answers a bare DRF page, another the envelope; one Swagger "
    "2.0 schema cannot describe both, so this needs an explicit responses="
)
_UNPROVEN = (
    "the generator cannot prove the success response (a tuple-unpacked error "
    "response), and drf-yasg infers no 2xx body for this DELETE, so neither "
    "the analysis nor success_envelope=True has anything to wrap; needs an "
    "explicit responses= contract (status-level impact as for no payload)"
)
_DISCARDED_HOOK = (
    "perform_*() returns success_response() but DRF's mixin discards it and "
    "answers bare serializer data; the generated bare schema is correct"
)

KNOWN_UNMODELLED_SUCCESS_ENVELOPES = {
    "DELETE /agent-playground/graphs/{graph_id}/dataset/rows/delete/": (
        _NO_INFERRED_PAYLOAD
    ),
    "DELETE /agent-playground/graphs/{id}/": _NO_INFERRED_PAYLOAD,
    "DELETE /agent-playground/graphs/{id}/versions/{version_id}/": (
        _NO_INFERRED_PAYLOAD
    ),
    "DELETE /agent-playground/graphs/{id}/versions/{version_id}/node-connections/{nc_id}/": (
        _NO_INFERRED_PAYLOAD
    ),
    "DELETE /agent-playground/graphs/{id}/versions/{version_id}/nodes/{node_id}/": (
        _NO_INFERRED_PAYLOAD
    ),
    "DELETE /agentcc/custom-properties/{id}/": _NO_INFERRED_PAYLOAD,
    "DELETE /agentcc/email-alerts/{id}/": _NO_INFERRED_PAYLOAD,
    "DELETE /agentcc/guardrail-feedback/{id}/": _NO_INFERRED_PAYLOAD,
    "DELETE /agentcc/guardrail-policies/{id}/": _NO_INFERRED_PAYLOAD,
    "DELETE /agentcc/org-configs/{id}/": _NO_INFERRED_PAYLOAD,
    "DELETE /agentcc/provider-credentials/{id}/": _NO_INFERRED_PAYLOAD,
    "DELETE /agentcc/routing-policies/{id}/": _NO_INFERRED_PAYLOAD,
    "DELETE /agentcc/sessions/{id}/": _NO_INFERRED_PAYLOAD,
    "DELETE /agentcc/shadow-experiments/{id}/": _NO_INFERRED_PAYLOAD,
    "DELETE /agentcc/webhooks/{id}/": _NO_INFERRED_PAYLOAD,
    "DELETE /model-hub/annotation-queues/{id}/": _NO_INFERRED_PAYLOAD,
    "DELETE /model-hub/annotations/{id}/": _NO_INFERRED_PAYLOAD,
    "DELETE /model-hub/api-keys/{id}/": _NO_INFERRED_PAYLOAD,
    "DELETE /model-hub/develops/{dataset_id}/delete_column/{column_id}/": (
        _NO_INFERRED_PAYLOAD
    ),
    "DELETE /model-hub/develops/{dataset_id}/delete_row/": _NO_INFERRED_PAYLOAD,
    "DELETE /model-hub/develops/{dataset_id}/delete_template_eval/{eval_id}/": (
        _NO_INFERRED_PAYLOAD
    ),
    "DELETE /model-hub/develops/{dataset_id}/delete_user_eval/{eval_id}/": (
        _NO_INFERRED_PAYLOAD
    ),
    "DELETE /model-hub/eval-groups/{id}/": _NO_INFERRED_PAYLOAD,
    "DELETE /model-hub/experiments/delete/": _NO_INFERRED_PAYLOAD,
    "DELETE /model-hub/experiments/v2/delete/": _NO_INFERRED_PAYLOAD,
    "DELETE /model-hub/get-eval-logs": _NO_INFERRED_PAYLOAD,
    "DELETE /model-hub/prompt-base-templates/{id}/": _NO_INFERRED_PAYLOAD,
    "DELETE /model-hub/prompt-folders/{id}/": _NO_INFERRED_PAYLOAD,
    "DELETE /model-hub/prompt-templates/{id}/delete-evaluation-config/": (
        _NO_INFERRED_PAYLOAD
    ),
    "DELETE /simulate/api/personas/{id}/": _NO_INFERRED_PAYLOAD,
    "DELETE /simulate/prompt-templates/{prompt_template_id}/simulations/{run_test_id}/": (
        _NO_INFERRED_PAYLOAD
    ),
    "DELETE /tracer/dashboard/{dashboard_pk}/widgets/{id}/": _NO_INFERRED_PAYLOAD,
    "DELETE /tracer/dashboard/{id}/": _NO_INFERRED_PAYLOAD,
    "DELETE /tracer/observability-provider/{id}/": _NO_INFERRED_PAYLOAD,
    "DELETE /tracer/observation-span/delete_annotation_label/": _NO_INFERRED_PAYLOAD,
    "DELETE /tracer/project/{id}/": _NO_INFERRED_PAYLOAD,
    "DELETE /tracer/shared-links/{id}/": _NO_INFERRED_PAYLOAD,
    "DELETE /tracer/shared-links/{id}/access/{access_id}/": _NO_INFERRED_PAYLOAD,
    "GET /agentcc/mcp/tool-calls/{id}/": _NO_INFERRED_PAYLOAD,
    "GET /agentcc/request-logs/": _MIXED,
    "GET /agentcc/request-logs/search/": _MIXED,
    "GET /agentcc/request-logs/sessions/": _MIXED,
    "GET /agentcc/request-logs/sessions/{session_id}/": _MIXED,
    "GET /agentcc/sessions/": _MIXED,
    "GET /agentcc/shadow-results/": _MIXED,
    "GET /model-hub/experiments/data/": _MIXED,
    "GET /model-hub/optimisation/": _MIXED,
    "GET /model-hub/prompt-executions/": _MIXED,
    "GET /model-hub/prompt-history-executions/": _MIXED,
    "GET /tracer/eval-task/": _MIXED,
    "DELETE /model-hub/annotation-queues/{queue_id}/items/{id}/discussion/comments/{comment_id}/": (
        _UNPROVEN
    ),
    "PUT /model-hub/annotations-labels/{id}/": _DISCARDED_HOOK,
    "PATCH /model-hub/annotations-labels/{id}/": _DISCARDED_HOOK,
    "DELETE /model-hub/annotations-labels/{id}/": _DISCARDED_HOOK,
}

# Deliberately broad and textual, independent of the generator's own analysis:
# any ``.success_response(`` / ``.create_response(`` call reachable through
# ``self``/``super()`` methods or module-level functions counts.
_ENVELOPE_HELPER_CALL = re.compile(r"\.(?:success_response|create_response)\(")
_SELF_CALL = re.compile(r"\b(?:self|super\(\))\.(\w+)\(")
# A bare call such as ``helper(...)``; ``x.helper(`` is not one.
_BARE_CALL = re.compile(r"(?<![.\w])([A-Za-z_]\w*)\(")
_FRAMEWORK_MODULES = ("django.", "drf_yasg.", "rest_framework.")


def _user_function(func):
    seen = set()
    while id(func) not in seen:
        seen.add(id(func))
        func = inspect.unwrap(getattr(func, "__func__", func))
        if (
            getattr(func, "__module__", "") == "rest_framework.decorators"
            and func.__name__ == "handler"
        ):
            func = inspect.getclosurevars(func).nonlocals["func"]
    return func


def _reaches_success_helper(view_cls, func, depth=0, seen=None):
    seen = set() if seen is None else seen
    func = _user_function(func)
    if func in seen or depth > 8:
        return False
    seen.add(func)
    try:
        source = inspect.getsource(func)
    except (OSError, TypeError):
        return False
    if _ENVELOPE_HELPER_CALL.search(source):
        return True
    return any(
        _reaches_success_helper(view_cls, helper, depth + 1, seen)
        for helper in _called_helpers(view_cls, func, source)
    )


def _called_helpers(view_cls, func, source):
    """Yield the functions ``source`` calls that can build its response.

    ``self.x()``/``super().x()`` resolve through the view's MRO only; a bare
    ``x()`` resolves only to a module-level function of ``func``'s module (so
    ``list(queryset)`` is never mistaken for the view's ``list`` action).
    """

    for name in _SELF_CALL.findall(source):
        for klass in view_cls.__mro__:
            attr = klass.__dict__.get(name)
            attr = getattr(attr, "__func__", attr)
            if callable(attr):
                yield attr
    module_globals = getattr(func, "__globals__", {})
    for name in _BARE_CALL.findall(source):
        target = module_globals.get(name)
        if inspect.isfunction(target) and not (
            getattr(target, "__module__", "") or ""
        ).startswith(_FRAMEWORK_MODULES):
            yield target


def _documents_envelope(response, definitions):
    schema = response.get("schema") or {}
    if "$ref" in schema:
        schema = definitions[schema["$ref"].rsplit("/", 1)[-1]]
    properties = schema.get("properties") or {}
    return (properties.get("status") or {}).get(
        "type"
    ) == "boolean" and "result" in properties


def _has_explicit_success_contract(overrides):
    return any(
        str(code) != "default" and 200 <= int(code) < 300
        for code in overrides.get("responses") or {}
    )


def test_regression_guard_follows_self_and_module_level_helpers():
    # The guard must see the envelope wherever the action can reach it, so an
    # action the analysis cannot prove still has to be modelled or listed.
    assert _reaches_success_helper(
        _EnvelopeItemViewSet, _EnvelopeItemViewSet.partial_update
    )
    assert _reaches_success_helper(
        _ModuleHelperEnvelopeView, _ModuleHelperEnvelopeView.get
    )
    assert not _reaches_success_helper(_BareView, _BareView.get)


def test_every_success_envelope_operation_is_modelled():
    schema, operations = _generated_contract()
    definitions = schema["definitions"]

    unmodelled = set()
    for (path_, method), (view, overrides) in operations.items():
        action_name = getattr(view, "action", None) or method
        handler = getattr(view, action_name, None)
        if handler is None or not _reaches_success_helper(type(view), handler):
            continue
        if _has_explicit_success_contract(overrides):
            continue
        if overrides.get("success_envelope") is False:
            continue
        responses = schema["paths"][path_][method]["responses"]
        if any(
            code.startswith("2") and _documents_envelope(response, definitions)
            for code, response in responses.items()
        ):
            continue
        unmodelled.add(f"{method.upper()} {path_}")

    generated = {f"{method.upper()} {path_}" for path_, method in operations}
    new = sorted(unmodelled - set(KNOWN_UNMODELLED_SUCCESS_ENVELOPES))
    # Only entries whose operation exists in this generation can go stale, so
    # the OSS lane (no ee/cloud routes) checks the same list.
    stale = sorted(
        key
        for key in KNOWN_UNMODELLED_SUCCESS_ENVELOPES
        if key in generated and key not in unmodelled
    )
    assert not new, (
        f"{len(new)} operations return GeneralMethods.success_response() but "
        "OpenAPI does not document the envelope. Declare responses=, "
        "success_envelope=True where the analysis cannot prove the envelope, "
        "or success_envelope=False with a reason:\n" + "\n".join(new)
    )
    assert not stale, (
        "These operations are modelled now; remove them from "
        "KNOWN_UNMODELLED_SUCCESS_ENVELOPES:\n" + "\n".join(stale)
    )
