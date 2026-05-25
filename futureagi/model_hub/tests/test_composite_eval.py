"""
Tests for Phase 7: Composite Evals.
"""

import pytest

from model_hub.models.choices import OwnerChoices
from model_hub.models.evals_metric import CompositeEvalChild, EvalTemplate
from model_hub.types import CompositeChildResult
from model_hub.utils.composite_aggregation import (
    aggregate_error_localizers,
    aggregate_scores,
    aggregate_summaries,
)


@pytest.fixture
def child_eval_1(organization, workspace):
    return EvalTemplate.no_workspace_objects.create(
        name="child-eval-one",
        organization=organization,
        workspace=workspace,
        owner=OwnerChoices.USER.value,
        config={"output": "Pass/Fail"},
        eval_tags=["llm"],
        visible_ui=True,
    )


@pytest.fixture
def child_eval_2(organization, workspace):
    return EvalTemplate.no_workspace_objects.create(
        name="child-eval-two",
        organization=organization,
        workspace=workspace,
        owner=OwnerChoices.USER.value,
        config={"output": "score"},
        eval_tags=["code", "function"],
        visible_ui=True,
    )


@pytest.mark.e2e
@pytest.mark.django_db
class TestCompositeEvalCreateAPI:
    url = "/model-hub/eval-templates/create-composite/"

    def test_create_composite(self, auth_client, child_eval_1, child_eval_2):
        response = auth_client.post(
            self.url,
            {
                "name": "my-composite",
                "description": "A composite eval",
                "child_template_ids": [str(child_eval_1.id), str(child_eval_2.id)],
            },
            format="json",
        )
        assert response.status_code == 200
        result = response.data["result"]
        assert result["name"] == "my-composite"
        assert result["template_type"] == "composite"
        assert len(result["children"]) == 2
        assert result["children"][0]["order"] == 0
        assert result["children"][1]["order"] == 1

        # Verify DB
        parent = EvalTemplate.objects.get(id=result["id"])
        assert parent.template_type == "composite"
        assert CompositeEvalChild.objects.filter(parent=parent).count() == 2

    def test_create_composite_stores_child_configs(
        self, auth_client, child_eval_1, child_eval_2
    ):
        response = auth_client.post(
            self.url,
            {
                "name": "param-composite",
                "child_template_ids": [str(child_eval_1.id), str(child_eval_2.id)],
                "child_configs": {
                    str(child_eval_2.id): {"params": {"min_words": 5}},
                },
            },
            format="json",
        )

        assert response.status_code == 200
        parent = EvalTemplate.objects.get(id=response.data["result"]["id"])
        link = CompositeEvalChild.objects.get(parent=parent, child=child_eval_2)
        assert link.config == {"params": {"min_words": 5}}

    def test_create_composite_invalid_child(self, auth_client, child_eval_1):
        response = auth_client.post(
            self.url,
            {
                "name": "bad-composite",
                "child_template_ids": [
                    str(child_eval_1.id),
                    "00000000-0000-0000-0000-000000000000",
                ],
            },
            format="json",
        )
        assert response.status_code == 400

    def test_create_composite_empty_children_rejected(self, auth_client):
        response = auth_client.post(
            self.url,
            {"name": "empty-composite", "child_template_ids": []},
            format="json",
        )
        assert response.status_code == 400

    def test_create_composite_duplicate_name_rejected(self, auth_client, child_eval_1):
        auth_client.post(
            self.url,
            {"name": "dup-composite", "child_template_ids": [str(child_eval_1.id)]},
            format="json",
        )
        response = auth_client.post(
            self.url,
            {"name": "dup-composite", "child_template_ids": [str(child_eval_1.id)]},
            format="json",
        )
        assert response.status_code == 400


@pytest.mark.e2e
@pytest.mark.django_db
class TestCompositeEvalDetailAPI:
    def _create_composite(self, auth_client, child_ids):
        r = auth_client.post(
            "/model-hub/eval-templates/create-composite/",
            {"name": "detail-composite", "child_template_ids": child_ids},
            format="json",
        )
        return r.data["result"]["id"]

    def test_get_composite_detail(self, auth_client, child_eval_1, child_eval_2):
        composite_id = self._create_composite(
            auth_client, [str(child_eval_1.id), str(child_eval_2.id)]
        )
        response = auth_client.get(
            f"/model-hub/eval-templates/{composite_id}/composite/"
        )
        assert response.status_code == 200
        result = response.data["result"]
        assert result["template_type"] == "composite"
        assert len(result["children"]) == 2
        assert result["children"][0]["child_name"] == "child-eval-one"
        assert result["children"][1]["child_name"] == "child-eval-two"

    def test_get_composite_detail_includes_child_configs(
        self, auth_client, child_eval_1, child_eval_2
    ):
        response = auth_client.post(
            "/model-hub/eval-templates/create-composite/",
            {
                "name": "detail-config-composite",
                "child_template_ids": [str(child_eval_1.id), str(child_eval_2.id)],
                "child_configs": {
                    str(child_eval_2.id): {"params": {"max_words": 12}},
                },
            },
            format="json",
        )
        composite_id = response.data["result"]["id"]

        response = auth_client.get(
            f"/model-hub/eval-templates/{composite_id}/composite/"
        )

        assert response.status_code == 200
        children = response.data["result"]["children"]
        child = next(c for c in children if c["child_id"] == str(child_eval_2.id))
        assert child["config"] == {"params": {"max_words": 12}}

    def test_get_nonexistent_composite(self, auth_client):
        response = auth_client.get(
            "/model-hub/eval-templates/00000000-0000-0000-0000-000000000000/composite/"
        )
        assert response.status_code == 404

    def test_get_single_as_composite_404(self, auth_client, child_eval_1):
        """Getting a single eval via composite detail should 404."""
        response = auth_client.get(
            f"/model-hub/eval-templates/{child_eval_1.id}/composite/"
        )
        assert response.status_code == 404


# =============================================================================
# Aggregation Logic (Unit Tests — no DB required)
# =============================================================================


def _make_child(
    name: str,
    score: float | None = None,
    weight: float = 1.0,
    reason: str | None = None,
    status: str = "completed",
    error: str | None = None,
    error_localizer_result: dict | None = None,
) -> CompositeChildResult:
    return CompositeChildResult(
        child_id="fake-id",
        child_name=name,
        order=0,
        score=score,
        output=score,
        reason=reason,
        output_type="percentage",
        status=status,
        error=error,
        weight=weight,
        error_localizer_result=error_localizer_result,
    )


class TestAggregateScores:
    def test_weighted_avg(self):
        # (0.8 * 2.0 + 0.6 * 1.0) / (2.0 + 1.0) = 2.2 / 3.0 ≈ 0.7333
        result = aggregate_scores([(0.8, 2.0), (0.6, 1.0)], "weighted_avg")
        assert result is not None
        assert abs(result - 0.7333) < 0.001

    def test_avg(self):
        result = aggregate_scores([(0.8, 1.0), (0.6, 1.0), (0.4, 1.0)], "avg")
        assert result is not None
        assert abs(result - 0.6) < 0.001

    def test_min(self):
        result = aggregate_scores([(0.8, 1.0), (0.3, 1.0), (0.9, 1.0)], "min")
        assert result == 0.3

    def test_max(self):
        result = aggregate_scores([(0.8, 1.0), (0.3, 1.0), (0.9, 1.0)], "max")
        assert result == 0.9

    def test_pass_rate_default_threshold(self):
        # Default threshold 0.5: scores 0.8 and 0.6 pass, 0.3 fails → 2/3
        result = aggregate_scores([(0.8, 1.0), (0.6, 1.0), (0.3, 1.0)], "pass_rate")
        assert result is not None
        assert abs(result - 2 / 3) < 0.001

    def test_pass_rate_custom_thresholds(self):
        # Child thresholds: [0.7, 0.5, 0.9]
        # 0.8 >= 0.7 ✓, 0.6 >= 0.5 ✓, 0.85 >= 0.9 ✗ → 2/3
        result = aggregate_scores(
            [(0.8, 1.0), (0.6, 1.0), (0.85, 1.0)],
            "pass_rate",
            child_thresholds=[0.7, 0.5, 0.9],
        )
        assert result is not None
        assert abs(result - 2 / 3) < 0.001

    def test_empty_scores_returns_none(self):
        assert aggregate_scores([], "weighted_avg") is None

    def test_zero_weight_returns_none(self):
        assert aggregate_scores([(0.5, 0.0), (0.8, 0.0)], "weighted_avg") is None

    def test_single_score(self):
        result = aggregate_scores([(0.75, 1.0)], "weighted_avg")
        assert result == 0.75

    def test_unknown_function_falls_back_to_weighted_avg(self):
        result = aggregate_scores([(0.8, 2.0), (0.6, 1.0)], "unknown_func")
        assert result is not None
        assert abs(result - 0.7333) < 0.001


class TestAggregateSummaries:
    def test_summaries_include_scores_and_reasons(self):
        children = [
            _make_child("hallucination", score=0.85, reason="No hallucinations found."),
            _make_child("toxicity", score=0.95, reason="Clean and professional tone."),
        ]
        summary = aggregate_summaries(children)
        assert "[hallucination]" in summary
        assert "0.85" in summary
        assert "No hallucinations found." in summary
        assert "[toxicity]" in summary
        assert "0.95" in summary

    def test_failed_child_shows_error(self):
        children = [
            _make_child("relevance", status="failed", error="API timeout"),
        ]
        summary = aggregate_summaries(children)
        assert "[relevance]" in summary
        assert "FAILED" in summary
        assert "API timeout" in summary

    def test_none_score_shows_na(self):
        children = [
            _make_child("legacy-eval", score=None, reason="Some reason"),
        ]
        summary = aggregate_summaries(children)
        assert "N/A" in summary


class TestAggregateErrorLocalizers:
    def test_groups_by_child_name(self):
        children = [
            _make_child(
                "hallucination",
                error_localizer_result={
                    "error_analysis": [{"unit_key": "sentence_1", "rank": 1}],
                    "selected_input_key": "output",
                },
            ),
            _make_child("toxicity", error_localizer_result=None),
            _make_child(
                "relevance",
                error_localizer_result={
                    "error_analysis": [{"unit_key": "sentence_3", "rank": 1}],
                    "selected_input_key": "context",
                },
            ),
        ]
        result = aggregate_error_localizers(children)
        assert "hallucination" in result
        assert "relevance" in result
        assert "toxicity" not in result  # None excluded

    def test_empty_children(self):
        assert aggregate_error_localizers([]) == {}


# =============================================================================
# CRUD with Aggregation Fields
# =============================================================================


@pytest.mark.e2e
@pytest.mark.django_db
class TestCompositeEvalAggregationCRUD:
    url = "/model-hub/eval-templates/create-composite/"

    def test_create_composite_with_aggregation_config(
        self, auth_client, child_eval_1, child_eval_2
    ):
        response = auth_client.post(
            self.url,
            {
                "name": "agg-composite",
                "child_template_ids": [str(child_eval_1.id), str(child_eval_2.id)],
                "aggregation_enabled": True,
                "aggregation_function": "min",
            },
            format="json",
        )
        assert response.status_code == 200
        result = response.data["result"]
        assert result["aggregation_enabled"] is True
        assert result["aggregation_function"] == "min"

        parent = EvalTemplate.objects.get(id=result["id"])
        assert parent.aggregation_enabled is True
        assert parent.aggregation_function == "min"

    def test_create_composite_with_weights(
        self, auth_client, child_eval_1, child_eval_2
    ):
        response = auth_client.post(
            self.url,
            {
                "name": "weighted-composite",
                "child_template_ids": [str(child_eval_1.id), str(child_eval_2.id)],
                "child_weights": {
                    str(child_eval_1.id): 2.0,
                    str(child_eval_2.id): 0.5,
                },
            },
            format="json",
        )
        assert response.status_code == 200
        result = response.data["result"]
        children = result["children"]
        assert children[0]["weight"] == 2.0
        assert children[1]["weight"] == 0.5

    def test_create_composite_aggregation_disabled(self, auth_client, child_eval_1):
        response = auth_client.post(
            self.url,
            {
                "name": "no-agg-composite",
                "child_template_ids": [str(child_eval_1.id)],
                "aggregation_enabled": False,
            },
            format="json",
        )
        assert response.status_code == 200
        result = response.data["result"]
        assert result["aggregation_enabled"] is False

        parent = EvalTemplate.objects.get(id=result["id"])
        assert parent.aggregation_enabled is False

    def test_detail_includes_aggregation_fields(
        self, auth_client, child_eval_1, child_eval_2
    ):
        create_resp = auth_client.post(
            self.url,
            {
                "name": "detail-agg-composite",
                "child_template_ids": [str(child_eval_1.id), str(child_eval_2.id)],
                "aggregation_function": "max",
                "child_weights": {str(child_eval_1.id): 3.0},
            },
            format="json",
        )
        composite_id = create_resp.data["result"]["id"]

        response = auth_client.get(
            f"/model-hub/eval-templates/{composite_id}/composite/"
        )
        assert response.status_code == 200
        result = response.data["result"]
        assert result["aggregation_enabled"] is True
        assert result["aggregation_function"] == "max"
        assert result["children"][0]["weight"] == 3.0
        assert result["children"][1]["weight"] == 1.0  # default


@pytest.mark.e2e
@pytest.mark.django_db
class TestCompositeEvalUpdateAPI:
    create_url = "/model-hub/eval-templates/create-composite/"

    def _create_composite(self, auth_client, name, child_ids, **extra):
        resp = auth_client.post(
            self.create_url,
            {"name": name, "child_template_ids": child_ids, **extra},
            format="json",
        )
        assert resp.status_code == 200, resp.data
        return resp.data["result"]["id"]

    def test_update_name_and_description(self, auth_client, child_eval_1):
        cid = self._create_composite(auth_client, "orig-name", [str(child_eval_1.id)])
        resp = auth_client.patch(
            f"/model-hub/eval-templates/{cid}/composite/",
            {"name": "new-name", "description": "Updated description"},
            format="json",
        )
        assert resp.status_code == 200, resp.data
        result = resp.data["result"]
        assert result["name"] == "new-name"
        assert result["description"] == "Updated description"

        parent = EvalTemplate.objects.get(id=cid)
        assert parent.name == "new-name"
        assert parent.description == "Updated description"

    def test_update_aggregation_config(self, auth_client, child_eval_1, child_eval_2):
        cid = self._create_composite(
            auth_client,
            "agg-update",
            [str(child_eval_1.id), str(child_eval_2.id)],
        )
        resp = auth_client.patch(
            f"/model-hub/eval-templates/{cid}/composite/",
            {
                "aggregation_enabled": False,
                "aggregation_function": "min",
            },
            format="json",
        )
        assert resp.status_code == 200
        result = resp.data["result"]
        assert result["aggregation_enabled"] is False
        assert result["aggregation_function"] == "min"

    def test_update_child_list_replaces_children(
        self, auth_client, child_eval_1, child_eval_2
    ):
        cid = self._create_composite(
            auth_client, "child-replace", [str(child_eval_1.id)]
        )
        resp = auth_client.patch(
            f"/model-hub/eval-templates/{cid}/composite/",
            {
                "child_template_ids": [
                    str(child_eval_2.id),
                    str(child_eval_1.id),
                ],
                "child_weights": {
                    str(child_eval_2.id): 2.5,
                    str(child_eval_1.id): 0.5,
                },
            },
            format="json",
        )
        assert resp.status_code == 200
        result = resp.data["result"]
        assert len(result["children"]) == 2
        # Order matches request
        assert result["children"][0]["child_id"] == str(child_eval_2.id)
        assert result["children"][0]["weight"] == 2.5
        assert result["children"][1]["child_id"] == str(child_eval_1.id)
        assert result["children"][1]["weight"] == 0.5

    def test_update_weights_only(self, auth_client, child_eval_1, child_eval_2):
        cid = self._create_composite(
            auth_client,
            "weight-only",
            [str(child_eval_1.id), str(child_eval_2.id)],
        )
        resp = auth_client.patch(
            f"/model-hub/eval-templates/{cid}/composite/",
            {
                "child_weights": {
                    str(child_eval_1.id): 3.0,
                    str(child_eval_2.id): 1.5,
                },
            },
            format="json",
        )
        assert resp.status_code == 200
        result = resp.data["result"]
        # Child list unchanged
        assert len(result["children"]) == 2
        by_id = {c["child_id"]: c for c in result["children"]}
        assert by_id[str(child_eval_1.id)]["weight"] == 3.0
        assert by_id[str(child_eval_2.id)]["weight"] == 1.5

    def test_update_partial_leaves_other_fields(
        self, auth_client, child_eval_1, child_eval_2
    ):
        cid = self._create_composite(
            auth_client,
            "partial-update",
            [str(child_eval_1.id), str(child_eval_2.id)],
            aggregation_function="max",
        )
        # Only update name — aggregation fields should remain
        resp = auth_client.patch(
            f"/model-hub/eval-templates/{cid}/composite/",
            {"name": "partial-updated"},
            format="json",
        )
        assert resp.status_code == 200
        result = resp.data["result"]
        assert result["name"] == "partial-updated"
        assert result["aggregation_function"] == "max"
        assert len(result["children"]) == 2

    def test_update_rejects_duplicate_name(self, auth_client, child_eval_1):
        self._create_composite(auth_client, "existing-name", [str(child_eval_1.id)])
        cid2 = self._create_composite(auth_client, "other-name", [str(child_eval_1.id)])
        resp = auth_client.patch(
            f"/model-hub/eval-templates/{cid2}/composite/",
            {"name": "existing-name"},
            format="json",
        )
        assert resp.status_code == 400

    def test_update_rejects_invalid_aggregation_function(
        self, auth_client, child_eval_1
    ):
        cid = self._create_composite(
            auth_client, "bad-fn-update", [str(child_eval_1.id)]
        )
        resp = auth_client.patch(
            f"/model-hub/eval-templates/{cid}/composite/",
            {"aggregation_function": "not_a_real_function"},
            format="json",
        )
        assert resp.status_code == 400

    def test_update_rejects_invalid_child_id(self, auth_client, child_eval_1):
        cid = self._create_composite(
            auth_client, "bad-child-update", [str(child_eval_1.id)]
        )
        resp = auth_client.patch(
            f"/model-hub/eval-templates/{cid}/composite/",
            {
                "child_template_ids": ["00000000-0000-0000-0000-000000000000"],
            },
            format="json",
        )
        assert resp.status_code == 400

    def test_update_nonexistent_composite_404(self, auth_client):
        resp = auth_client.patch(
            "/model-hub/eval-templates/00000000-0000-0000-0000-000000000000/composite/",
            {"name": "whatever"},
            format="json",
        )
        assert resp.status_code == 404

    def test_update_single_as_composite_404(self, auth_client, child_eval_1):
        # child_eval_1 is a single eval, not composite
        resp = auth_client.patch(
            f"/model-hub/eval-templates/{child_eval_1.id}/composite/",
            {"name": "shouldnt-work"},
            format="json",
        )
        assert resp.status_code == 404


# Axis-aware fixtures for composite_child_axis tests.
# These set output_type_normalized so _validate_child_matches_axis can assess.
@pytest.fixture
def pf_child_a(organization, workspace):
    return EvalTemplate.no_workspace_objects.create(
        name="pf-child-a",
        organization=organization,
        workspace=workspace,
        owner=OwnerChoices.USER.value,
        config={"output": "Pass/Fail"},
        eval_tags=["llm"],
        visible_ui=True,
        output_type_normalized="pass_fail",
    )


@pytest.fixture
def pf_child_b(organization, workspace):
    return EvalTemplate.no_workspace_objects.create(
        name="pf-child-b",
        organization=organization,
        workspace=workspace,
        owner=OwnerChoices.USER.value,
        config={"output": "Pass/Fail"},
        eval_tags=["llm"],
        visible_ui=True,
        output_type_normalized="pass_fail",
    )


@pytest.fixture
def pct_child(organization, workspace):
    return EvalTemplate.no_workspace_objects.create(
        name="pct-child",
        organization=organization,
        workspace=workspace,
        owner=OwnerChoices.USER.value,
        config={"output": "Percentage"},
        eval_tags=["llm"],
        visible_ui=True,
        output_type_normalized="percentage",
    )


@pytest.fixture
def code_child(organization, workspace):
    return EvalTemplate.no_workspace_objects.create(
        name="code-child",
        organization=organization,
        workspace=workspace,
        owner=OwnerChoices.USER.value,
        eval_type="code",
        config={"output": "Percentage"},
        eval_tags=["code"],
        visible_ui=True,
        output_type_normalized="percentage",
    )


@pytest.mark.e2e
@pytest.mark.django_db
class TestCompositeChildAxis:
    """composite_child_axis enforces homogeneous child output shape."""

    create_url = "/model-hub/eval-templates/create-composite/"

    def _detail_url(self, composite_id):
        return f"/model-hub/eval-templates/{composite_id}/composite/"

    def _execute_url(self, composite_id):
        return f"/model-hub/eval-templates/{composite_id}/composite/execute/"

    def test_create_pass_fail_axis_with_matching_children(
        self, auth_client, pf_child_a, pf_child_b
    ):
        resp = auth_client.post(
            self.create_url,
            {
                "name": "axis-pf-ok",
                "child_template_ids": [str(pf_child_a.id), str(pf_child_b.id)],
                "composite_child_axis": "pass_fail",
            },
            format="json",
        )
        assert resp.status_code == 200
        result = resp.data["result"]
        assert result["composite_child_axis"] == "pass_fail"

    def test_create_pass_fail_axis_rejects_percentage_child(
        self, auth_client, pf_child_a, pct_child
    ):
        resp = auth_client.post(
            self.create_url,
            {
                "name": "axis-pf-mixed",
                "child_template_ids": [str(pf_child_a.id), str(pct_child.id)],
                "composite_child_axis": "pass_fail",
            },
            format="json",
        )
        assert resp.status_code == 400
        assert (
            "pct-child" in str(resp.data).lower()
            or "pass/fail" in str(resp.data).lower()
        )

    def test_create_invalid_axis_value_rejected(self, auth_client, pf_child_a):
        resp = auth_client.post(
            self.create_url,
            {
                "name": "axis-bogus",
                "child_template_ids": [str(pf_child_a.id)],
                "composite_child_axis": "not-a-real-axis",
            },
            format="json",
        )
        assert resp.status_code == 400

    def test_create_code_axis_rejects_llm_child(self, auth_client, pf_child_a):
        # pf_child_a is an llm eval, not code
        resp = auth_client.post(
            self.create_url,
            {
                "name": "axis-code-llm",
                "child_template_ids": [str(pf_child_a.id)],
                "composite_child_axis": "code",
            },
            format="json",
        )
        assert resp.status_code == 400

    def test_create_code_axis_accepts_code_child(self, auth_client, code_child):
        resp = auth_client.post(
            self.create_url,
            {
                "name": "axis-code-ok",
                "child_template_ids": [str(code_child.id)],
                "composite_child_axis": "code",
            },
            format="json",
        )
        assert resp.status_code == 200

    def test_patch_add_mismatched_child_rejected(
        self, auth_client, pf_child_a, pct_child
    ):
        # Create a pass_fail composite with pf_child_a
        create = auth_client.post(
            self.create_url,
            {
                "name": "axis-patch-src",
                "child_template_ids": [str(pf_child_a.id)],
                "composite_child_axis": "pass_fail",
            },
            format="json",
        )
        assert create.status_code == 200
        composite_id = create.data["result"]["id"]

        # Try replacing children with a percentage child — should fail
        resp = auth_client.patch(
            self._detail_url(composite_id),
            {
                "child_template_ids": [str(pct_child.id)],
            },
            format="json",
        )
        assert resp.status_code == 400

    def test_patch_change_axis_with_incompatible_existing_children_rejected(
        self, auth_client, pf_child_a
    ):
        # Create a pass_fail composite
        create = auth_client.post(
            self.create_url,
            {
                "name": "axis-switch-src",
                "child_template_ids": [str(pf_child_a.id)],
                "composite_child_axis": "pass_fail",
            },
            format="json",
        )
        assert create.status_code == 200
        composite_id = create.data["result"]["id"]

        # Try to switch axis to percentage without changing children
        resp = auth_client.patch(
            self._detail_url(composite_id),
            {"composite_child_axis": "percentage"},
            format="json",
        )
        assert resp.status_code == 400

    def test_patch_change_axis_and_children_together(
        self, auth_client, pf_child_a, pct_child
    ):
        create = auth_client.post(
            self.create_url,
            {
                "name": "axis-combo-src",
                "child_template_ids": [str(pf_child_a.id)],
                "composite_child_axis": "pass_fail",
            },
            format="json",
        )
        assert create.status_code == 200
        composite_id = create.data["result"]["id"]

        # Switch axis + replace children in one PATCH
        resp = auth_client.patch(
            self._detail_url(composite_id),
            {
                "composite_child_axis": "percentage",
                "child_template_ids": [str(pct_child.id)],
            },
            format="json",
        )
        assert resp.status_code == 200
        assert resp.data["result"]["composite_child_axis"] == "percentage"

    def test_execute_rejects_drifted_child(self, auth_client, pf_child_a):
        # Create a pass_fail composite
        create = auth_client.post(
            self.create_url,
            {
                "name": "axis-drift",
                "child_template_ids": [str(pf_child_a.id)],
                "composite_child_axis": "pass_fail",
            },
            format="json",
        )
        assert create.status_code == 200
        composite_id = create.data["result"]["id"]

        # Simulate drift: edit the child's output_type_normalized directly
        pf_child_a.output_type_normalized = "percentage"
        pf_child_a.save(update_fields=["output_type_normalized"])

        # Execute should reject with a clear message naming the child
        resp = auth_client.post(
            self._execute_url(composite_id),
            {"mapping": {}},
            format="json",
        )
        assert resp.status_code == 400
        assert "pf-child-a" in str(resp.data)

    def test_create_without_axis_accepts_any_children_legacy(
        self, auth_client, pf_child_a, pct_child
    ):
        # No composite_child_axis → legacy path, mixed children allowed.
        resp = auth_client.post(
            self.create_url,
            {
                "name": "axis-legacy",
                "child_template_ids": [str(pf_child_a.id), str(pct_child.id)],
            },
            format="json",
        )
        assert resp.status_code == 200
        assert resp.data["result"]["composite_child_axis"] == ""
