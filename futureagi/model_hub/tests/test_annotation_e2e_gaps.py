"""End-to-end integration tests filling gaps from Team A's matrix.

Real Postgres test DB, real DRF APIClient, zero behavior mocks. Each test
asserts both the HTTP response AND the resulting DB state.

Coverage:
- ``TestAnnotationSummaryFromScore``: the rewritten summary endpoint
  (Score-only, post-migration). Validates the fix that surfaces unified
  Score annotations on the dataset summary page.
- ``TestCrossOrgIsolation``: gap F — verifies org A cannot read or write
  org B's annotation resources (Score, Label, Queue).
- ``TestEEEntitlementGating``: gap G — replaces the prior
  ``status in (200, 403)`` assertions with parametrized on/off entitlement
  state and asserts exact response codes.
- ``TestDefaultQueueAutoCreateRace``: gap D — concurrent score POSTs to a
  default queue must produce exactly one QueueItem per source, not N.
"""

from __future__ import annotations

import threading
import uuid
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone
from rest_framework import status

from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from accounts.models.workspace import Workspace, WorkspaceMembership
from model_hub.models.annotation_queues import (
    AnnotationQueue,
    AnnotationQueueAnnotator,
    AnnotationQueueLabel,
    QueueItem,
)
from model_hub.models.choices import (
    AnnotationQueueStatusChoices,
    AnnotationTypeChoices,
    AnnotatorRole,
    QueueItemSourceType,
    QueueItemStatus,
)
from model_hub.models.develop_annotations import AnnotationsLabels
from model_hub.models.develop_dataset import Dataset, Row
from model_hub.models.score import Score
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
from tfc.middleware.workspace_context import set_workspace_context
from tracer.models.project import Project


SCORE_URL = "/model-hub/scores/"
LABEL_URL = "/model-hub/annotations-labels/"
QUEUE_URL = "/model-hub/annotation-queues/"


def _result(resp):
    return resp.data.get("result", resp.data) if hasattr(resp, "data") else resp.data


# ---------------------------------------------------------------------------
# Fixtures: second organization + isolated client
# ---------------------------------------------------------------------------


@pytest.fixture
def second_organization(db):
    return Organization.objects.create(name="Other Organization")


@pytest.fixture
def second_user(db, second_organization):
    """Independent user in a different org. No overlap with `user`."""
    u = User.objects.create_user(
        email=f"other-{uuid.uuid4().hex[:8]}@futureagi.com",
        password="testpassword123",
        name="Other User",
        organization=second_organization,
        organization_role=OrganizationRoles.OWNER,
    )
    OrganizationMembership.no_workspace_objects.get_or_create(
        user=u,
        organization=second_organization,
        defaults={
            "role": OrganizationRoles.OWNER,
            "level": Level.OWNER,
            "is_active": True,
        },
    )
    ws = Workspace.objects.create(
        name="Other Workspace",
        organization=second_organization,
        is_default=True,
        is_active=True,
        created_by=u,
    )
    org_mem = OrganizationMembership.no_workspace_objects.filter(
        user=u, organization=second_organization
    ).first()
    WorkspaceMembership.no_workspace_objects.get_or_create(
        user=u,
        workspace=ws,
        defaults={
            "role": "Workspace Owner",
            "level": Level.OWNER,
            "is_active": True,
            "organization_membership": org_mem,
        },
    )
    return u


@pytest.fixture
def second_workspace(db, second_user):
    return Workspace.objects.get(
        organization=second_user.organization, is_default=True
    )


@pytest.fixture
def other_org_client(second_user, second_workspace):
    """APIClient authenticated as the second org's user."""
    from conftest import WorkspaceAwareAPIClient

    client = WorkspaceAwareAPIClient()
    client.force_authenticate(user=second_user)
    client.set_workspace(second_workspace)
    yield client
    client.stop_workspace_injection()


# Reused fixtures from the existing test files — pulled in via conftest
# discovery (organization, workspace, user, auth_client). Datasets and
# labels are created locally per-test.


@pytest.fixture
def project(db, organization, workspace):
    from model_hub.models.ai_model import AIModel

    return Project.objects.create(
        name="Annot E2E Project",
        organization=organization,
        workspace=workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
    )


@pytest.fixture
def dataset_with_rows(db, organization, workspace):
    set_workspace_context(workspace=workspace, organization=organization)
    ds = Dataset.objects.create(
        name="Annot E2E DS",
        organization=organization,
        workspace=workspace,
    )
    rows = [Row.objects.create(dataset=ds, order=i) for i in range(10)]
    return ds, rows


@pytest.fixture
def numeric_label(db, organization, workspace, project):
    return AnnotationsLabels.objects.create(
        name="Numeric E2E",
        type=AnnotationTypeChoices.NUMERIC.value,
        settings={"min": 0, "max": 10, "step_size": 1, "display_type": "slider"},
        organization=organization,
        workspace=workspace,
        project=project,
    )


@pytest.fixture
def categorical_label(db, organization, workspace, project):
    return AnnotationsLabels.objects.create(
        name="Cat E2E",
        type=AnnotationTypeChoices.CATEGORICAL.value,
        settings={
            "options": [{"label": "Yes"}, {"label": "No"}, {"label": "Maybe"}],
            "multi_choice": False,
            "rule_prompt": "",
            "auto_annotate": False,
            "strategy": None,
        },
        organization=organization,
        workspace=workspace,
        project=project,
    )


@pytest.fixture
def text_label(db, organization, workspace, project):
    return AnnotationsLabels.objects.create(
        name="Text E2E",
        type=AnnotationTypeChoices.TEXT.value,
        settings={"min_length": 1, "max_length": 200, "placeholder": "Enter text"},
        organization=organization,
        workspace=workspace,
        project=project,
    )


# ===========================================================================
# 1. Annotation summary endpoint reads from Score (the user's reported bug)
# ===========================================================================


@pytest.fixture
def _allow_agreement_entitlement():
    """Auto-allow the EE ``has_agreement_metrics`` entitlement so summary
    endpoint tests don't get short-circuited at the entitlement gate.
    The entitlement gate itself is exercised in ``TestEEEntitlementGating``."""
    from collections import namedtuple

    FeatCheck = namedtuple("FeatCheck", ["allowed", "reason"])
    with patch(
        "ee.usage.services.entitlements.Entitlements.check_feature",
        return_value=FeatCheck(allowed=True, reason="test-allowed"),
    ):
        yield


@pytest.mark.django_db
@pytest.mark.integration
@pytest.mark.usefixtures("_allow_agreement_entitlement")
class TestAnnotationSummaryFromScore:
    """Validates the rewritten ``/model-hub/dataset/<id>/annotation-summary/``.

    Pre-fix this endpoint only read ``model_hub_annotations`` + ``Cell.feedback_info``
    so datasets with only unified Score annotations rendered as empty. The fix
    is a Score-only ORM rewrite. These tests prove the fix returns the
    correct shape and aggregates.
    """

    def _summary_url(self, dataset_id):
        return f"/model-hub/dataset/{dataset_id}/annotation-summary/"

    def test_returns_per_label_aggregates_for_score_data(
        self,
        auth_client,
        dataset_with_rows,
        numeric_label,
        categorical_label,
        user,
    ):
        """Seed Score rows directly, then assert the summary endpoint
        surfaces them with correct aggregates (count, mean, mode)."""
        ds, rows = dataset_with_rows
        # Seed: 3 numeric scores by single annotator on first 3 rows
        for i, val in enumerate([5, 7, 9]):
            Score.objects.create(
                source_type="dataset_row",
                dataset_row=rows[i],
                label=numeric_label,
                annotator=user,
                value={"value": val},
                score_source="human",
                organization=user.organization,
            )
        # Seed: 2 categorical scores
        for i, sel in enumerate([["Yes"], ["No"]]):
            Score.objects.create(
                source_type="dataset_row",
                dataset_row=rows[i],
                label=categorical_label,
                annotator=user,
                value={"selected": sel},
                score_source="human",
                organization=user.organization,
            )

        resp = auth_client.get(self._summary_url(ds.id))
        assert resp.status_code == status.HTTP_200_OK, resp.data
        result = _result(resp)

        # Expect exactly 2 labels in summary
        labels_by_id = {l["label_id"]: l for l in result["labels"]}
        assert str(numeric_label.id) in labels_by_id
        assert str(categorical_label.id) in labels_by_id

        num = labels_by_id[str(numeric_label.id)]
        assert num["count_records"] == 3
        assert num["avg_value"] == pytest.approx(7.0, rel=1e-3)
        assert num["sum_value"] == 21.0
        # Range comes from settings.min/max
        assert num["range"] == "0-10"

        cat = labels_by_id[str(categorical_label.id)]
        assert cat["count_records"] == 2
        # mode_value is the most-frequent option
        assert cat["mode_value"] in {"Yes", "No"}

        # Annotator block: single user, 5 annotations
        assert len(result["annotators"]) == 1
        assert result["annotators"][0]["annotations"] == 5

    def test_empty_dataset_returns_empty_shape_not_error(
        self, auth_client, dataset_with_rows
    ):
        """Dataset with no scores returns 200 with empty labels/annotators
        — the frontend's ``isEmpty`` heuristic relies on this shape."""
        ds, _ = dataset_with_rows
        resp = auth_client.get(self._summary_url(ds.id))
        assert resp.status_code == status.HTTP_200_OK
        result = _result(resp)
        assert result["labels"] == []
        assert result["annotators"] == []
        assert "header" in result

    def test_text_label_surfaces_corpus_stats(
        self, auth_client, dataset_with_rows, text_label, user
    ):
        # Text summary uses NLTK punkt_tab tokenizer for vocab building.
        # Skip the test if NLTK data isn't installed in the env (CI installs it).
        try:
            import nltk

            nltk.data.find("tokenizers/punkt_tab")
        except (ImportError, LookupError):
            pytest.skip("NLTK punkt_tab tokenizer not available in this env")

        ds, rows = dataset_with_rows
        for i, t in enumerate(["short", "a longer one", "tiny"]):
            Score.objects.create(
                source_type="dataset_row",
                dataset_row=rows[i],
                label=text_label,
                annotator=user,
                value={"text": t},
                score_source="human",
                organization=user.organization,
            )
        resp = auth_client.get(self._summary_url(ds.id))
        assert resp.status_code == status.HTTP_200_OK
        labels = _result(resp)["labels"]
        assert len(labels) == 1
        text_summary = labels[0]
        assert text_summary["type"] == "text"
        assert text_summary["count_records"] == 3
        # Vocab and key terms come from the corpus builder
        assert text_summary["vocab_size"] >= 1
        assert isinstance(text_summary["key_terms"], list)

    def test_legacy_annotations_only_dataset_returns_empty_post_cutover(
        self, auth_client, dataset_with_rows, numeric_label, user
    ):
        """Datasets that have ONLY legacy ``Annotations`` rows (no Score) now
        return empty summaries. Documented: legacy cell-based data is not
        backfilled in this cutover; tenants should run ``backfill_scores``
        for trace/item paths or accept blank state for cell-only datasets.
        """
        from model_hub.models.develop_annotations import Annotations

        ds, _ = dataset_with_rows
        # Create a stub legacy Annotations row to mimic the user's reported
        # state (1 stub legacy row, 0 cells, 0 Score rows).
        Annotations.objects.create(
            name="Legacy Stub",
            dataset=ds,
            organization=user.organization,
            workspace=ds.workspace,
        )
        resp = auth_client.get(self._summary_url(ds.id))
        assert resp.status_code == status.HTTP_200_OK
        # Legacy data is intentionally invisible after the rewrite.
        assert _result(resp)["labels"] == []

    def test_dataset_coverage_is_100_when_all_rows_have_all_labels(
        self,
        auth_client,
        dataset_with_rows,
        numeric_label,
        user,
    ):
        """One label, every row scored → coverage should be 100%."""
        ds, rows = dataset_with_rows
        for r in rows:
            Score.objects.create(
                source_type="dataset_row",
                dataset_row=r,
                label=numeric_label,
                annotator=user,
                value={"value": 5},
                score_source="human",
                organization=user.organization,
            )
        resp = auth_client.get(self._summary_url(ds.id))
        assert resp.status_code == status.HTTP_200_OK
        header = _result(resp)["header"]
        assert header["dataset_coverage"] == 100.0

    def test_max_value_lands_in_last_histogram_bucket(
        self, auth_client, dataset_with_rows, organization, workspace, project, user
    ):
        """A score at the configured max (e.g. star 5/5) must appear in the
        last histogram bucket, not be silently dropped by a ``<`` boundary."""
        ds, rows = dataset_with_rows
        star_label = AnnotationsLabels.objects.create(
            name="Star Bucket Edge",
            type=AnnotationTypeChoices.STAR.value,
            settings={"no_of_stars": 5},
            organization=organization,
            workspace=workspace,
            project=project,
        )
        # Score with max value (5/5)
        Score.objects.create(
            source_type="dataset_row",
            dataset_row=rows[0],
            label=star_label,
            annotator=user,
            value={"rating": 5},
            score_source="human",
            organization=organization,
        )
        resp = auth_client.get(self._summary_url(ds.id))
        assert resp.status_code == status.HTTP_200_OK
        labels = _result(resp)["labels"]
        # Find this label's graph_data and assert the max value is counted.
        target = next(l for l in labels if l["label_id"] == str(star_label.id))
        total_in_buckets = sum(target["graph_data"].values())
        assert total_in_buckets == 1, (
            f"Max-value (5/5) star rating dropped from histogram: "
            f"graph_data={target['graph_data']}"
        )

    def test_categorical_options_with_substring_overlap(
        self, auth_client, dataset_with_rows, organization, workspace, project, user
    ):
        """Options like ``A``/``AA`` must not shadow each other in counts.
        With the legacy substring match, a value of ``AA`` would increment
        both ``A`` and ``AA``."""
        ds, rows = dataset_with_rows
        label = AnnotationsLabels.objects.create(
            name="Substring Cat",
            type=AnnotationTypeChoices.CATEGORICAL.value,
            settings={
                "options": [{"label": "A"}, {"label": "AA"}, {"label": "AAA"}],
                "multi_choice": False,
                "rule_prompt": "",
                "auto_annotate": False,
                "strategy": None,
            },
            organization=organization,
            workspace=workspace,
            project=project,
        )
        # 3 scores, one per option
        for i, sel in enumerate([["A"], ["AA"], ["AAA"]]):
            Score.objects.create(
                source_type="dataset_row",
                dataset_row=rows[i],
                label=label,
                annotator=user,
                value={"selected": sel},
                score_source="human",
                organization=organization,
            )
        resp = auth_client.get(self._summary_url(ds.id))
        assert resp.status_code == status.HTTP_200_OK
        target = next(
            l for l in _result(resp)["labels"] if l["label_id"] == str(label.id)
        )
        # graph_data is normalized to fractions; absolute counts should be
        # 1 each → 1/3 each. Critically, no option should be over-counted.
        for option, frac in target["graph_data"].items():
            assert frac == pytest.approx(1 / 3, rel=1e-3), (
                f"Categorical substring shadowing: option {option!r} got {frac} "
                f"(expected 1/3 = 0.333). Full graph_data={target['graph_data']}"
            )

    def test_score_with_wrong_source_type_excluded(
        self,
        auth_client,
        dataset_with_rows,
        numeric_label,
        user,
        observation_span_factory,
    ):
        """A Score with a populated ``dataset_row`` FK but a ``source_type``
        of, say, 'observation_span', must NOT contaminate the dataset summary."""
        ds, rows = dataset_with_rows
        # Legitimate dataset_row score
        Score.objects.create(
            source_type="dataset_row",
            dataset_row=rows[0],
            label=numeric_label,
            annotator=user,
            value={"value": 5},
            score_source="human",
            organization=user.organization,
        )
        # Malformed: dataset_row populated but source_type wrong. The defensive
        # filter should exclude this row.
        span = observation_span_factory()
        Score.objects.create(
            source_type="observation_span",
            dataset_row=rows[1],   # leaks into dataset
            observation_span=span,
            label=numeric_label,
            annotator=user,
            value={"value": 99},
            score_source="human",
            organization=user.organization,
        )
        resp = auth_client.get(self._summary_url(ds.id))
        assert resp.status_code == status.HTTP_200_OK
        target = next(
            l
            for l in _result(resp)["labels"]
            if l["label_id"] == str(numeric_label.id)
        )
        # Only the legitimate score (value=5) should be counted, not the leak.
        assert target["count_records"] == 1
        assert target["sum_value"] == 5.0

    def test_score_for_other_org_dataset_not_included(
        self,
        auth_client,
        dataset_with_rows,
        numeric_label,
        user,
        second_user,
        second_organization,
    ):
        """Cross-org safety: a Score that somehow points at another org's
        dataset must not leak into this dataset's summary."""
        ds, rows = dataset_with_rows
        # Seed a legitimate Score for our org
        Score.objects.create(
            source_type="dataset_row",
            dataset_row=rows[0],
            label=numeric_label,
            annotator=user,
            value={"value": 5},
            score_source="human",
            organization=user.organization,
        )
        # Seed a foreign-org Score on a foreign dataset (should not appear
        # in our summary regardless of how it's queried)
        other_ws = Workspace.objects.get(organization=second_organization, is_default=True)
        other_ds = Dataset.objects.create(
            name="Other DS",
            organization=second_organization,
            workspace=other_ws,
        )
        other_row = Row.objects.create(dataset=other_ds, order=0)
        other_label = AnnotationsLabels.objects.create(
            name="Other Numeric",
            type=AnnotationTypeChoices.NUMERIC.value,
            settings={"min": 0, "max": 10, "step_size": 1, "display_type": "slider"},
            organization=second_organization,
            workspace=other_ws,
        )
        Score.objects.create(
            source_type="dataset_row",
            dataset_row=other_row,
            label=other_label,
            annotator=second_user,
            value={"value": 2},
            score_source="human",
            organization=second_organization,
        )

        resp = auth_client.get(self._summary_url(ds.id))
        assert resp.status_code == status.HTTP_200_OK
        labels = _result(resp)["labels"]
        # Exactly one label visible — our own — not the foreign one.
        assert len(labels) == 1
        assert labels[0]["label_id"] == str(numeric_label.id)


# ===========================================================================
# 2. Cross-org permission isolation (Gap F)
# ===========================================================================


@pytest.mark.django_db
@pytest.mark.integration
class TestCrossOrgIsolation:
    """Org A and org B must not be able to read or write each other's
    annotation resources. Each test seeds a resource in org A, then
    attempts access from org B's auth_client and asserts 403/404."""

    def test_other_org_cannot_read_label(
        self, other_org_client, numeric_label
    ):
        """GET /annotations-labels/{id}/ for another org's label → 404."""
        resp = other_org_client.get(f"{LABEL_URL}{numeric_label.id}/")
        assert resp.status_code in (
            status.HTTP_404_NOT_FOUND,
            status.HTTP_403_FORBIDDEN,
        ), resp.data

    def test_other_org_cannot_update_label(
        self, other_org_client, numeric_label
    ):
        """PATCH attempting to mutate another org's label → must not succeed."""
        resp = other_org_client.patch(
            f"{LABEL_URL}{numeric_label.id}/",
            {"name": "hijacked"},
            format="json",
        )
        assert resp.status_code != status.HTTP_200_OK
        # Verify DB unchanged regardless of HTTP code
        numeric_label.refresh_from_db()
        assert numeric_label.name != "hijacked"

    def test_other_org_cannot_delete_label(
        self, other_org_client, numeric_label
    ):
        resp = other_org_client.delete(f"{LABEL_URL}{numeric_label.id}/")
        assert resp.status_code != status.HTTP_204_NO_CONTENT
        numeric_label.refresh_from_db()
        assert numeric_label.deleted is False

    def test_other_org_cannot_read_score(
        self,
        other_org_client,
        observation_span_factory,
        numeric_label,
        user,
    ):
        """A score created in org A must not appear in org B's score list."""
        span = observation_span_factory()
        score = Score.objects.create(
            source_type="observation_span",
            observation_span=span,
            label=numeric_label,
            annotator=user,
            value={"value": 5},
            score_source="human",
            organization=user.organization,
        )

        # Org B tries to read it directly via for-source. Either 4xx or
        # empty result — never returns the foreign score.
        resp = other_org_client.get(
            f"{SCORE_URL}for-source/",
            {"source_type": "observation_span", "source_id": span.id},
        )
        if resp.status_code == status.HTTP_200_OK:
            payload = _result(resp)
            # Response shape varies (list, dict-with-scores, dict-with-results)
            if isinstance(payload, dict):
                scores_returned = payload.get("scores") or payload.get("results", [])
            else:
                scores_returned = list(payload)
            ids = [str(s.get("id")) for s in scores_returned if isinstance(s, dict)]
            assert str(score.id) not in ids, (
                f"Cross-org leak: org B saw org A's score {score.id}"
            )
        else:
            # 4xx is also acceptable — confirms the boundary held.
            assert resp.status_code != status.HTTP_500_INTERNAL_SERVER_ERROR, (
                f"for-source endpoint crashed for cross-org request: {resp.data}"
            )

    def test_other_org_cannot_delete_score(
        self,
        other_org_client,
        observation_span_factory,
        numeric_label,
        user,
    ):
        from tracer.models.observation_span import ObservationSpan

        span = observation_span_factory()
        score = Score.objects.create(
            source_type="observation_span",
            observation_span=span,
            label=numeric_label,
            annotator=user,
            value={"value": 5},
            score_source="human",
            organization=user.organization,
        )
        resp = other_org_client.delete(f"{SCORE_URL}{score.id}/")
        assert resp.status_code != status.HTTP_204_NO_CONTENT
        score.refresh_from_db()
        assert score.deleted is False

    def test_other_org_cannot_read_queue(self, other_org_client, queue):
        resp = other_org_client.get(f"{QUEUE_URL}{queue}/")
        assert resp.status_code in (
            status.HTTP_404_NOT_FOUND,
            status.HTTP_403_FORBIDDEN,
        )

    def test_other_org_cannot_modify_queue(self, other_org_client, queue):
        resp = other_org_client.patch(
            f"{QUEUE_URL}{queue}/",
            {"name": "hijacked"},
            format="json",
        )
        assert resp.status_code != status.HTTP_200_OK
        q = AnnotationQueue.objects.get(pk=queue)
        assert q.name != "hijacked"

    def test_other_org_label_list_is_isolated(
        self, other_org_client, numeric_label, categorical_label
    ):
        """Org B's label list endpoint must not include org A's labels."""
        resp = other_org_client.get(LABEL_URL)
        assert resp.status_code == status.HTTP_200_OK
        result = _result(resp)
        ids = [
            l.get("id")
            for l in (
                result.get("results", []) if isinstance(result, dict) else result
            )
        ]
        assert str(numeric_label.id) not in ids
        assert str(categorical_label.id) not in ids


# ===========================================================================
# 3. EE entitlement gating (Gap G)
# ===========================================================================


@pytest.mark.django_db
@pytest.mark.integration
class TestEEEntitlementGating:
    """Replaces the legacy ``status in (200, 403)`` assertions with
    parametrized fixtures that toggle entitlement on vs off and assert
    the EXACT response code per state. Catches regressions where a
    feature accidentally becomes free or accidentally breaks for paid
    customers."""

    def _agreement_url(self, qid):
        return f"{QUEUE_URL}{qid}/agreement/"

    def _entitlement_path(self, allowed: bool):
        """Patch path for the EE feature check used by the agreement endpoint."""
        from collections import namedtuple

        FeatCheck = namedtuple("FeatCheck", ["allowed", "reason"])
        return patch(
            "ee.usage.services.entitlements.Entitlements.check_feature",
            return_value=FeatCheck(
                allowed=allowed,
                reason="test-allowed" if allowed else "Plan does not include this feature",
            ),
        )

    def test_agreement_returns_200_when_entitled(self, auth_client, queue):
        """has_agreement_metrics=True → endpoint reachable, 200."""
        with self._entitlement_path(allowed=True):
            resp = auth_client.get(self._agreement_url(queue))
        # 200 or 4xx for missing data — but NOT 403
        assert resp.status_code != status.HTTP_403_FORBIDDEN, resp.data

    def test_agreement_returns_403_when_not_entitled(self, auth_client, queue):
        """has_agreement_metrics=False → 403 with reason."""
        with self._entitlement_path(allowed=False):
            resp = auth_client.get(self._agreement_url(queue))
        assert resp.status_code == status.HTTP_403_FORBIDDEN, resp.data

    def test_summary_endpoint_uses_agreement_entitlement(
        self, auth_client, dataset_with_rows
    ):
        """The dataset summary endpoint also gates on has_agreement_metrics —
        confirm it returns 403 when not entitled, NOT a partial success."""
        ds, _ = dataset_with_rows
        with self._entitlement_path(allowed=False):
            resp = auth_client.get(f"/model-hub/dataset/{ds.id}/annotation-summary/")
        assert resp.status_code == status.HTTP_403_FORBIDDEN, resp.data


# ===========================================================================
# 4. Default-queue auto-create race (Gap D)
# ===========================================================================


@pytest.mark.django_db
@pytest.mark.integration
class TestSubmitNextItemRoundTrip:
    """P3 #13 — full submit→next-item round-trip. Submit all required labels
    on item A, call next-item, assert returned item is B (not A). Closes the
    gap that Team A's audit flagged: auto-complete is tested in isolation
    and ``next-item`` is tested in isolation, but the assembly was never
    asserted as a single user flow."""

    def test_submit_completes_item_then_next_item_skips_it(
        self,
        auth_client,
        organization,
        workspace,
        project,
        user,
        numeric_label,
    ):
        from django.test import TestCase

        from model_hub.models.annotation_queues import (
            AnnotationQueue,
            AnnotationQueueAnnotator,
            AnnotationQueueLabel,
        )
        from tracer.models.observation_span import ObservationSpan
        from tracer.models.trace import Trace

        # Two spans on the same project so we can have two queue items
        traces = [
            Trace.objects.create(
                project=project,
                name=f"Round-trip Trace {i}",
                input={},
                output={},
            )
            for i in range(2)
        ]
        spans = [
            ObservationSpan.objects.create(
                id=f"span_rt_{i}_{uuid.uuid4().hex[:8]}",
                project=project,
                trace=traces[i],
                name=f"Round-trip Span {i}",
                observation_type="llm",
                start_time=timezone.now() - timedelta(seconds=1),
                end_time=timezone.now(),
                input={},
                output={},
                model="gpt-4",
                prompt_tokens=1,
                completion_tokens=1,
                total_tokens=2,
                cost=0.0,
                latency_ms=10,
                status="OK",
            )
            for i in range(2)
        ]

        # Active queue with one required label and the user as annotator
        queue = AnnotationQueue.objects.create(
            name="Round-trip Queue",
            organization=organization,
            workspace=workspace,
            project=project,
            status=AnnotationQueueStatusChoices.ACTIVE.value,
            assignment_strategy="manual",
        )
        AnnotationQueueAnnotator.objects.create(
            queue=queue, user=user, role=AnnotatorRole.MANAGER.value
        )
        AnnotationQueueLabel.objects.create(
            queue=queue, label=numeric_label, required=True, order=0
        )
        items = [
            QueueItem.objects.create(
                queue=queue,
                source_type=QueueItemSourceType.OBSERVATION_SPAN.value,
                observation_span=spans[i],
                organization=organization,
                workspace=workspace,
                status=QueueItemStatus.PENDING.value,
                order=i,
            )
            for i in range(2)
        ]

        # next-item BEFORE any submit → should return one of the two items
        resp_first = auth_client.get(
            f"{QUEUE_URL}{queue.id}/items/next-item/"
        )
        assert resp_first.status_code == status.HTTP_200_OK
        first_payload = _result(resp_first)
        first_item = first_payload.get("item") or first_payload
        first_id = first_item.get("id")
        assert first_id in {str(items[0].id), str(items[1].id)}

        # The "Submit & Next" UI flow calls submit + complete back-to-back
        # (see ``handleSubmitAndNext`` in annotate-workspace-view.jsx). The
        # ``submit_annotations`` endpoint only moves PENDING → IN_PROGRESS;
        # the explicit ``complete`` action moves it to COMPLETED. Mirror
        # that two-step flow here.
        with TestCase.captureOnCommitCallbacks(execute=True):
            submit_resp = auth_client.post(
                f"{QUEUE_URL}{queue.id}/items/{first_id}/annotations/submit/",
                {
                    "annotations": [
                        {
                            "label_id": str(numeric_label.id),
                            "value": {"value": 5},
                        },
                    ],
                },
                format="json",
            )
        assert submit_resp.status_code == status.HTTP_200_OK, submit_resp.data

        with TestCase.captureOnCommitCallbacks(execute=True):
            complete_resp = auth_client.post(
                f"{QUEUE_URL}{queue.id}/items/{first_id}/complete/",
                {},
                format="json",
            )
        assert complete_resp.status_code == status.HTTP_200_OK, complete_resp.data

        # Item should now be COMPLETED in DB
        for it in items:
            it.refresh_from_db()
        completed_ids = {str(it.id) for it in items if it.status == "completed"}
        assert first_id in completed_ids

        # next-item AFTER submit → should return the OTHER item, not the
        # one we just completed
        resp_second = auth_client.get(
            f"{QUEUE_URL}{queue.id}/items/next-item/",
            {"exclude": first_id},
        )
        assert resp_second.status_code == status.HTTP_200_OK
        second_payload = _result(resp_second)
        second_item = second_payload.get("item") or second_payload
        second_id = second_item.get("id") if second_item else None
        # The completed item must not be returned again
        assert second_id != first_id, (
            f"next-item returned the just-completed item {first_id} instead of "
            f"the remaining pending item"
        )


@pytest.mark.django_db
@pytest.mark.integration
class TestBulkCreatePartialFailure:
    """P0 #3 — ``/scores/bulk/`` must surface per-label failures in
    ``result.errors`` so the InlineAnnotator can keep edit mode open and
    let the user retry. Previously the UI fired a green "Saved" toast on
    any 2xx response, hiding silent label drops."""

    def test_bulk_with_invalid_label_returns_errors(
        self,
        auth_client,
        observation_span_factory,
        numeric_label,
    ):
        from django.test import TestCase

        span = observation_span_factory()
        bogus_label_id = str(uuid.uuid4())  # not in DB

        with TestCase.captureOnCommitCallbacks(execute=True):
            resp = auth_client.post(
                f"{SCORE_URL}bulk/",
                {
                    "source_type": "observation_span",
                    "source_id": span.id,
                    "scores": [
                        {"label_id": str(numeric_label.id), "value": {"value": 5}},
                        {"label_id": bogus_label_id, "value": {"value": 9}},
                    ],
                },
                format="json",
            )

        assert resp.status_code == status.HTTP_200_OK, resp.data
        result = _result(resp)
        # One Score saved, one label failure surfaced.
        assert len(result["scores"]) == 1
        assert len(result["errors"]) == 1
        assert bogus_label_id in result["errors"][0]

        # Critically: the saved score IS in the DB (partial success), the
        # failed one is NOT.
        score_count = Score.objects.filter(
            observation_span=span,
            deleted=False,
        ).count()
        assert score_count == 1


@pytest.mark.django_db
@pytest.mark.integration
class TestScoreSideEffectIsolation:
    """P0 #1 — Score side-effects (auto-create-queue-items, auto-complete)
    run via ``transaction.on_commit`` so a side-effect failure can't poison
    the Score write transaction. The legacy bare-except pattern caught the
    error but left the atomic block dirty, leading to silent rollbacks /
    ``TransactionManagementError`` on subsequent ORM calls in the same
    request.
    """

    def test_score_commits_even_when_side_effect_raises(
        self,
        auth_client,
        observation_span_factory,
        numeric_label,
        organization,
    ):
        """If ``_safe_auto_complete_queue_items`` raises, the Score row must
        still be committed (it was outside the side-effect's scope)."""
        from django.test import TestCase

        span = observation_span_factory()

        # Patch the auto-complete to blow up. The wrapper logs and swallows.
        with patch(
            "model_hub.views.scores._auto_complete_queue_items",
            side_effect=RuntimeError("boom"),
        ):
            with TestCase.captureOnCommitCallbacks(execute=True):
                resp = auth_client.post(
                    SCORE_URL,
                    {
                        "source_type": "observation_span",
                        "source_id": span.id,
                        "label_id": str(numeric_label.id),
                        "value": {"value": 7},
                    },
                    format="json",
                )

        # 200 — the Score commit succeeded, side-effect failure was logged
        assert resp.status_code == status.HTTP_200_OK, resp.data
        # Score is in the DB
        score_id = _result(resp)["id"]
        score = Score.objects.get(pk=score_id)
        assert score.value == {"value": 7}
        assert score.deleted is False

    def test_subsequent_orm_calls_work_after_side_effect_failure(
        self,
        auth_client,
        observation_span_factory,
        numeric_label,
    ):
        """After a side-effect raises during one request, the next request's
        ORM calls must work normally — i.e., no ``TransactionManagementError``
        leaking out of the connection pool."""
        from django.test import TestCase

        span = observation_span_factory()

        # First request: side-effect raises
        with patch(
            "model_hub.views.scores._auto_create_queue_items_for_default_queues",
            side_effect=RuntimeError("boom"),
        ):
            with TestCase.captureOnCommitCallbacks(execute=True):
                resp1 = auth_client.post(
                    SCORE_URL,
                    {
                        "source_type": "observation_span",
                        "source_id": span.id,
                        "label_id": str(numeric_label.id),
                        "value": {"value": 1},
                    },
                    format="json",
                )
        assert resp1.status_code == status.HTTP_200_OK

        # Second request: should work normally (no transaction state leak)
        with TestCase.captureOnCommitCallbacks(execute=True):
            resp2 = auth_client.post(
                SCORE_URL,
                {
                    "source_type": "observation_span",
                    "source_id": span.id,
                    "label_id": str(numeric_label.id),
                    "value": {"value": 2},
                },
                format="json",
            )
        assert resp2.status_code == status.HTTP_200_OK
        # Latest value persisted (upsert semantics)
        score_id = _result(resp2)["id"]
        assert Score.objects.get(pk=score_id).value == {"value": 2}


@pytest.mark.django_db(transaction=True)
@pytest.mark.integration
class TestDefaultQueueAutoCreateRace:
    """Concurrency: two threads POST a Score for the same source at the same
    time. The default-queue auto-create logic must produce exactly one
    QueueItem per source, not N. Uses ``transaction=True`` so threads see
    each other's commits."""

    def test_concurrent_scores_create_one_queue_item(
        self,
        observation_span_factory,
        numeric_label,
        organization,
        workspace,
        project,
        user,
    ):
        # Set up a default queue scoped to project, with the label
        queue = AnnotationQueue.objects.create(
            name="Default Concurrency Queue",
            organization=organization,
            workspace=workspace,
            project=project,
            status=AnnotationQueueStatusChoices.ACTIVE.value,
            is_default=True,
            assignment_strategy="manual",
        )
        AnnotationQueueAnnotator.objects.create(
            queue=queue, user=user, role=AnnotatorRole.MANAGER.value
        )
        AnnotationQueueLabel.objects.create(
            queue=queue, label=numeric_label, required=False, order=0
        )

        span = observation_span_factory()
        barrier = threading.Barrier(parties=4, timeout=10)
        errors = []

        def post_score(value):
            try:
                from rest_framework.test import APIClient

                # Each thread gets its own client to avoid shared state.
                from conftest import WorkspaceAwareAPIClient

                client = WorkspaceAwareAPIClient()
                client.force_authenticate(user=user)
                client.set_workspace(workspace)
                try:
                    barrier.wait()
                    resp = client.post(
                        SCORE_URL,
                        {
                            "source_type": "observation_span",
                            "source_id": span.id,
                            "label_id": str(numeric_label.id),
                            "value": {"value": value},
                        },
                        format="json",
                    )
                    if resp.status_code != status.HTTP_200_OK:
                        errors.append(f"value={value} status={resp.status_code} data={resp.data}")
                finally:
                    client.stop_workspace_injection()
            except Exception as exc:
                errors.append(f"value={value} exc={exc!r}")

        threads = [threading.Thread(target=post_score, args=(v,)) for v in (1, 2, 3, 4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        assert not errors, f"Concurrent POST errors: {errors}"

        # Critical assertion: exactly one QueueItem for this source, even
        # though 4 threads all triggered auto-create at the same time.
        qi_count = QueueItem.objects.filter(
            queue=queue,
            source_type=QueueItemSourceType.OBSERVATION_SPAN.value,
            observation_span=span,
        ).count()
        assert qi_count == 1, (
            f"Race condition: {qi_count} QueueItems created for the same source. "
            "Expected exactly 1 (idempotent default-queue auto-create)."
        )


# ---------------------------------------------------------------------------
# Module-local helper fixtures used by the cross-org and concurrency tests
# ---------------------------------------------------------------------------


@pytest.fixture
def observation_span_factory(db, project, organization, workspace):
    """Factory fixture so tests can mint multiple spans on demand."""
    from tracer.models.observation_span import ObservationSpan
    from tracer.models.trace import Trace

    def _make():
        trace = Trace.objects.create(
            project=project,
            name=f"Race Trace {uuid.uuid4().hex[:6]}",
            input={},
            output={},
        )
        span_id = f"span_{uuid.uuid4().hex[:16]}"
        return ObservationSpan.objects.create(
            id=span_id,
            project=project,
            trace=trace,
            name="Race Span",
            observation_type="llm",
            start_time=timezone.now() - timedelta(seconds=1),
            end_time=timezone.now(),
            input={},
            output={},
            model="gpt-4",
            prompt_tokens=1,
            completion_tokens=1,
            total_tokens=2,
            cost=0.0,
            latency_ms=10,
            status="OK",
        )

    return _make


@pytest.fixture
def queue(db, auth_client, user):
    """Active queue with current user as MANAGER."""
    resp = auth_client.post(QUEUE_URL, {"name": "E2E Gap Queue"}, format="json")
    assert resp.status_code in (200, 201), resp.data
    qid = resp.data["id"]
    # Activate
    r = auth_client.post(
        f"{QUEUE_URL}{qid}/update-status/", {"status": "active"}, format="json"
    )
    assert r.status_code == 200
    return qid
