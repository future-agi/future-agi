"""J2 regression: context capture must not read the leaf-only suite endpoint."""

import json
from uuid import uuid4

import pytest
from django.urls import include, path

from model_hub.models.skills_hub_v11 import HubSkillTestSuite
from model_hub.models.skills_hub_v12 import HubSkillContextCapture
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.v12.trace_source import read_trace_source
from model_hub.tests.skills_hub.r3_l06 import test_capture as capture_tests
from model_hub.tests.skills_hub.r3_l07 import test_workflow_http as fixtures

pytestmark = pytest.mark.django_db
urlpatterns = [path("model-hub/skills/", include("model_hub.urls_skills_hub"))]
admin = fixtures.admin
principal = fixtures.principal
context = fixtures.context
trace = fixtures.trace
other_scope = fixtures.other_scope


@pytest.fixture(autouse=True)
def routes(settings):
    settings.ROOT_URLCONF = __name__
    settings.MIDDLEWARE = [
        "tfc.middleware.workspace_context.WorkspaceContextMiddleware",
        *settings.MIDDLEWARE,
    ]


def test_context_suite_isolated_from_leaf_and_other_scope(
    admin, other_scope, context, hub_skill
):
    url = f"/model-hub/skills/contexts/{context.pk}/tests/"
    response = admin.get(url)
    assert response.status_code == 200, response.data
    assert response["Cache-Control"] == "private, no-store"
    assert response.data["result"]["revision"] == 0
    assert response.data["result"]["cases"] == []
    assert other_scope.get(url).status_code == 404
    assert admin.get(f"/model-hub/skills/{context.pk}/tests/").status_code == 404
    assert (
        admin.get(f"/model-hub/skills/contexts/{hub_skill.pk}/tests/").status_code
        == 404
    )


def test_context_capture_read_edit_cas_and_lineage(admin, other_scope, context, trace):
    root = f"/model-hub/skills/contexts/{context.pk}/"
    source = admin.post(
        root + "captures/source/", {"trace_ref": trace[0]}, format="json"
    )
    assert source.status_code == 200, source.data
    preview = admin.post(
        root + "captures/preview/",
        {
            "trace_ref": source.data["result"]["trace_ref"],
            "selected_segment_ids": [source.data["result"]["segments"][0]["id"]],
        },
        format="json",
    )
    assert preview.status_code == 200, preview.data
    packet = preview.data["result"]
    result = admin.post(
        root + f"captures/{packet['capture_id']}/accept/",
        {
            "source_fingerprint": packet["source_fingerprint"],
            "suite_revision": 0,
            "case": capture_tests.case_input(),
            "acknowledge_workspace_sharing": True,
        },
        format="json",
        HTTP_IDEMPOTENCY_KEY=str(uuid4()),
    )
    assert result.status_code == 200, result.data
    suite = admin.get(root + "tests/").data["result"]
    assert suite == result.data["result"]
    before = HubSkillTestSuite.objects.get(skill=context)
    lineage = before.cases[0]["source_group_id"]
    capture = HubSkillContextCapture.objects.get(pk=packet["capture_id"])
    receipt = dict(capture.accepted_receipt)
    assert capture.selected_text is None
    cases = [{**suite["cases"][0], "title": "Reviewed refund after capture"}]
    # Read-only response annotations are not accepted as authored case fields.
    cases = [
        {
            key: value
            for key, value in item.items()
            if key
            in {
                "id",
                "source_group_id",
                "title",
                "prompt",
                "fixtures",
                "expected_trigger",
                "checks",
                "reviewed",
            }
        }
        for item in cases
    ]
    body = {"revision": suite["revision"], "cases": cases}
    assert other_scope.put(root + "tests/", body, format="json").status_code == 404
    saved = admin.put(root + "tests/", body, format="json")
    assert saved.status_code == 200, saved.data
    assert saved.data["result"]["revision"] == suite["revision"] + 1
    assert admin.put(root + "tests/", body, format="json").status_code == 409
    after = HubSkillTestSuite.objects.get(skill=context)
    assert after.cases[0]["source_group_id"] == lineage
    capture.refresh_from_db()
    assert capture.accepted_receipt == receipt
    assert after.cases[0]["title"] == "Reviewed refund after capture"


@pytest.mark.parametrize("selection", [[3, 2, 1, 0], [2, 0], [3, 1], [3, 0, 2]])
def test_capture_selection_keeps_source_order(admin, context, trace, selection):
    """Checkbox click order must not rewrite the captured conversation/fixtures."""
    ref, row, *_ = trace
    row["input"] = json.dumps(
        {
            "messages": [
                {
                    "role": "user",
                    "content": "First check whether this order is refundable.",
                },
                {"role": "tool", "content": "The order is refundable."},
                {
                    "role": "user",
                    "content": "Correction: do not refund; check shipping instead.",
                },
                {"role": "tool", "content": "The package is in transit."},
            ]
        }
    )
    root = f"/model-hub/skills/contexts/{context.pk}/"
    source = admin.post(root + "captures/source/", {"trace_ref": ref}, format="json")
    assert source.status_code == 200, source.data
    segments = source.data["result"]["segments"]
    response = admin.post(
        root + "captures/preview/",
        {
            "trace_ref": source.data["result"]["trace_ref"],
            "selected_segment_ids": [segments[index]["id"] for index in selection],
        },
        format="json",
    )
    assert response.status_code == 200, response.data
    expected = [segments[index] for index in sorted(selection)]
    assert response.data["result"]["segments"] == expected
    capture = HubSkillContextCapture.objects.get(
        pk=response.data["result"]["capture_id"]
    )
    assert [
        {key: item[key] for key in ("id", "kind", "text")}
        for item in capture.selected_text
    ] == expected
    assert capture.source_fingerprint == source.data["result"]["source_fingerprint"]


def test_capture_reads_real_collector_scalar_identity(principal, context, trace):
    ref, row, _, calls, _ = trace
    expected = {
        "id": str(context.pk),
        "version_id": str(uuid4()),
        "digest": "sha256:" + "a" * 64,
    }
    row["scalar_attributes"] = {
        f"fi.context.{key}": value for key, value in expected.items()
    }
    result = read_trace_source(principal=principal, trace_ref=ref)
    assert result["recorded_context"] == expected
    assert "attrs_string" in calls[0][0]
    # A changed identity must change the independently bound source fingerprint.
    row["scalar_attributes"]["fi.context.version_id"] = str(uuid4())
    assert (
        read_trace_source(principal=principal, trace_ref=ref)["source_fingerprint"]
        != result["source_fingerprint"]
    )


@pytest.mark.parametrize("conflict", ["different", {"invalid": "object"}])
def test_capture_does_not_resolve_conflicting_identity_by_precedence(
    principal, trace, conflict
):
    ref, row, *_ = trace
    row["scalar_attributes"] = {"fi.context.id": "recorded"}
    row["span_attributes"] = {"fi.context.id": conflict}
    with pytest.raises(SkillsHubError) as error:
        read_trace_source(principal=principal, trace_ref=ref)
    assert error.value.code == "capture_unsafe"


def test_typed_source_marker_preserves_langsmith_capture_exclusion(principal, trace):
    ref, row, *_ = trace
    row["scalar_attributes"] = {"gen_ai.trace.source": "langsmith"}
    with pytest.raises(SkillsHubError) as error:
        read_trace_source(principal=principal, trace_ref=ref)
    assert error.value.code == "capture_unsafe"
