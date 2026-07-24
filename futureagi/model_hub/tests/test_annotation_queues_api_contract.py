"""Contract tests for the annotation-queue management API.

Mirrors the Simulate contract-debt pattern (see
``simulate/tests/test_simulator_agent_api_contract.py``): assert that the
generated OpenAPI ``swagger.json`` carries request-body and response schemas
for the annotation-queue endpoints, and that the contract-debt report stays
burned down for this feature (no endpoint silently loses its schema).

Pure schema assertions — no DB, no HTTP. They guard the generated contract at
source, so a serializer/view change that drops a schema fails here immediately.
"""

import json
from pathlib import Path

_SWAGGER = None
_DEBT = None


def _repo_root():
    # model_hub/tests/<file>.py -> parents[3] == monorepo root (holds api_contracts/)
    return Path(__file__).resolve().parents[3]


def _swagger():
    global _SWAGGER
    if _SWAGGER is None:
        with (_repo_root() / "api_contracts" / "openapi" / "swagger.json").open() as f:
            _SWAGGER = json.load(f)
    return _SWAGGER


def _debt_report():
    global _DEBT
    if _DEBT is None:
        with (
            _repo_root()
            / "api_contracts"
            / "openapi"
            / "management-api-contract-debt.generated.json"
        ).open() as f:
            _DEBT = json.load(f)
    return _DEBT


def _operation(path, method):
    return _swagger()["paths"][path][method.lower()]


def _body_ref(operation):
    body = next(
        p for p in operation.get("parameters", []) if p.get("in") == "body"
    )
    return body["schema"]["$ref"].rsplit("/", 1)[-1]


def _response_ref(operation, status_code="200"):
    return operation["responses"][status_code]["schema"]["$ref"].rsplit("/", 1)[-1]


# ---------------------------------------------------------------------------
# Expected contracts (a representative, high-value slice across the feature).
# The debt-burndown test below is the catch-all for anything not listed here.
# ---------------------------------------------------------------------------

BODY_CONTRACTS = {
    ("POST", "/model-hub/annotation-queues/"): "AnnotationQueue",
    ("POST", "/model-hub/annotation-queues/get-or-create-default/"): (
        "QueueDefaultRequest"
    ),
    ("PUT", "/model-hub/annotation-queues/{id}/"): "AnnotationQueue",
    ("PATCH", "/model-hub/annotation-queues/{id}/"): "AnnotationQueue",
    ("POST", "/model-hub/annotation-queues/{id}/add-label/"): "QueueLabelRequest",
    ("POST", "/model-hub/annotation-queues/{id}/remove-label/"): "QueueLabelRequest",
    ("POST", "/model-hub/annotation-queues/{id}/update-status/"): "QueueStatusRequest",
    ("POST", "/model-hub/annotation-queues/{id}/hard-delete/"): (
        "QueueHardDeleteRequest"
    ),
    ("POST", "/model-hub/annotation-queues/{id}/export-to-dataset/"): (
        "QueueExportToDatasetRequest"
    ),
    ("POST", "/model-hub/annotation-queues/{queue_id}/automation-rules/"): (
        "AutomationRule"
    ),
    ("POST", "/model-hub/annotation-queues/{queue_id}/items/add-items/"): "AddItems",
    ("POST", "/model-hub/annotation-queues/{queue_id}/items/assign/"): "AssignItems",
    ("POST", "/model-hub/annotation-queues/{queue_id}/items/bulk-remove/"): (
        "BulkRemoveItems"
    ),
    (
        "POST",
        "/model-hub/annotation-queues/{queue_id}/items/{id}/annotations/submit/",
    ): "SubmitAnnotations",
    (
        "POST",
        "/model-hub/annotation-queues/{queue_id}/items/{id}/annotations/import/",
    ): "ImportAnnotations",
    ("POST", "/model-hub/annotation-queues/{queue_id}/items/{id}/review/"): (
        "ReviewItemRequest"
    ),
    ("POST", "/model-hub/annotation-queues/{queue_id}/items/{id}/discussion/"): (
        "DiscussionCommentRequest"
    ),
}

RESPONSE_CONTRACTS = {
    ("POST", "/model-hub/annotation-queues/", "201"): "AnnotationQueue",
    ("GET", "/model-hub/annotation-queues/{id}/", "200"): "AnnotationQueue",
    ("PATCH", "/model-hub/annotation-queues/{id}/", "200"): "AnnotationQueue",
    ("GET", "/model-hub/annotation-queues/for-source/", "200"): (
        "QueueForSourceResponse"
    ),
    ("POST", "/model-hub/annotation-queues/get-or-create-default/", "200"): (
        "QueueDefaultResponse"
    ),
    ("POST", "/model-hub/annotation-queues/{id}/update-status/", "200"): (
        "QueueStatusResponse"
    ),
    ("GET", "/model-hub/annotation-queues/{id}/progress/", "200"): (
        "QueueProgressResponse"
    ),
    ("GET", "/model-hub/annotation-queues/{id}/analytics/", "200"): (
        "QueueAnalyticsResponse"
    ),
    ("GET", "/model-hub/annotation-queues/{id}/agreement/", "200"): (
        "QueueAgreementResponse"
    ),
    ("GET", "/model-hub/annotation-queues/{id}/export/", "200"): (
        "QueueExportAnnotationsResponse"
    ),
    ("POST", "/model-hub/annotation-queues/{id}/export-to-dataset/", "200"): (
        "QueueExportToDatasetResponse"
    ),
    ("POST", "/model-hub/annotation-queues/{queue_id}/items/add-items/", "200"): (
        "QueueAddItemsResponse"
    ),
    ("POST", "/model-hub/annotation-queues/{queue_id}/items/assign/", "200"): (
        "QueueAssignItemsResponse"
    ),
    ("POST", "/model-hub/annotation-queues/{queue_id}/automation-rules/", "201"): (
        "AutomationRule"
    ),
}


def test_annotation_queue_mutations_have_body_contracts():
    """Every listed mutation carries the expected request-body schema."""
    for (method, path), definition in BODY_CONTRACTS.items():
        assert _body_ref(_operation(path, method)) == definition, (method, path)


def test_annotation_queue_endpoints_have_response_contracts():
    """Every listed operation carries the expected response schema."""
    for (method, path, status_code), definition in RESPONSE_CONTRACTS.items():
        assert (
            _response_ref(_operation(path, method), status_code) == definition
        ), (method, path, status_code)


def test_annotation_queue_contract_debt_stays_burned_down():
    """No annotation-queue endpoint may appear in the contract-debt report —
    i.e. none may lose its body/response schema or regress to a broad shape."""
    report = _debt_report()
    buckets = (
        "mutation_endpoints_without_body_schema",
        "operations_without_response_schema",
        "broad_success_response_schemas",
    )

    missing = [bucket for bucket in buckets if bucket not in report]
    assert missing == [], f"debt report is missing bucket keys: {missing}"
    for bucket in buckets:
        offenders = [
            item
            for item in report[bucket]
            if "annotation-queue" in str(
                item.get("path", "") if isinstance(item, dict) else item
            )
        ]
        assert offenders == [], f"{bucket}: {offenders}"
