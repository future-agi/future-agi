"""Commercial caps A1, A2, A4 and the legacy observe/prototype/trace checks
apply on Future AGI Cloud only (TH-8084).

Self-hosted installs keep seeding and logging the Free-tier rows; nothing is
enforced from them. Cloud-mode cases pin the existing Cloud behaviour.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from ee.usage.models.usage import APICallLog, APICallType, ResourceLimits
from ee.usage.utils import usage_entries
from ee.usage.utils.usage_entries import (
    check_if_api_call_is_rate_limited,
    check_if_kb_creation_is_allowed,
    check_if_observe_creation_is_allowed,
    check_if_prototype_creation_is_allowed,
    check_if_row_limit_reached,
    check_if_trace_creation_is_allowed,
    check_if_user_creation_is_allowed,
    log_and_deduct_cost_for_api_request,
    log_and_deduct_cost_for_resource_request,
    log_api_call,
)
from tfc.capabilities import edition
from tfc.constants.api_calls import APICallStatusChoices, APICallTypeChoices

pytestmark = [pytest.mark.django_db, pytest.mark.requires_ee]

UPLOAD_URL = "/model-hub/develops/create-dataset-from-local-file/"


@pytest.fixture
def self_hosted(monkeypatch):
    monkeypatch.setattr(edition, "is_cloud", lambda: False)


def _no_usage_tables_read(ctx) -> bool:
    sql = " ".join(q["sql"] for q in ctx.captured_queries)
    return not any(
        table in sql
        for table in (
            "usage_organizationsubscription",
            "usage_resourcelimits",
            "usage_ratelimit",
        )
    )


# ---------------------------------------------------------------------------
# A1 dataset rows
# ---------------------------------------------------------------------------


class TestRowLimit:
    @pytest.mark.parametrize("rows", [1999, 2000, 2001])
    def test_self_hosted_accepts_any_row_count(self, self_hosted, organization, rows):
        """AC-04 / A1: 1,999, 2,000 and 2,001 rows are all accepted off-cloud."""
        with CaptureQueriesContext(connection) as ctx:
            assert check_if_row_limit_reached(organization, rows) == (True, {})
        assert _no_usage_tables_read(ctx)

        row = log_and_deduct_cost_for_resource_request(
            organization,
            APICallTypeChoices.ROW_ADD.value,
            config={"total_rows": rows},
        )
        assert row is not None
        assert row.status != APICallStatusChoices.RESOURCE_LIMIT.value

    def test_self_hosted_ignores_a_missing_free_tier_row(
        self, self_hosted, organization
    ):
        """AC-04 / A1: the Free-tier row used to fail closed when missing."""
        ResourceLimits.objects.filter(resource_type__name="rows").delete()
        assert check_if_row_limit_reached(organization, 10) == (True, {})

    @pytest.mark.parametrize(
        ("rows", "allowed"), [(1999, True), (2000, False), (2001, False)]
    )
    def test_cloud_is_unchanged(self, edition_cloud, organization, rows, allowed):
        """AC-13 / A1: Cloud Free tier still caps at 2,000 rows."""
        result, detail = check_if_row_limit_reached(organization, rows)
        assert result is allowed
        if not allowed:
            assert detail == {"resource_name": "rows", "limit": 2000}

    def _upload(self, auth_client, monkeypatch, rows):
        from model_hub.views.datasets.create import file_upload

        monkeypatch.setattr(
            file_upload,
            "upload_file_to_minio",
            lambda file_obj, object_key, org_id=None: f"minio://{object_key}",
        )
        monkeypatch.setattr(
            file_upload.process_dataset_from_file, "delay", lambda *a, **k: None
        )
        content = "input,output\n" + "".join(f"q{i},a{i}\n" for i in range(rows))
        return auth_client.post(
            UPLOAD_URL,
            {
                "new_dataset_name": f"rows-{rows}",
                "model_type": "GenerativeLLM",
                "file": SimpleUploadedFile(
                    "rows.csv", content.encode(), content_type="text/csv"
                ),
            },
            format="multipart",
        )

    def test_real_file_upload_import_accepts_2001_rows(
        self, self_hosted, auth_client, monkeypatch
    ):
        """AC-04 / A1: the real import path (file_upload.py) is uncapped off-cloud."""
        response = self._upload(auth_client, monkeypatch, 2001)
        assert response.status_code == 200, response.content
        assert response.json()["result"]["estimated_rows"] == 2001

    def test_real_file_upload_import_still_capped_on_cloud(
        self, edition_cloud, auth_client, monkeypatch
    ):
        """AC-13 / A1: the same upload is refused on the Cloud Free tier."""
        response = self._upload(auth_client, monkeypatch, 2001)
        assert response.status_code == 429, response.content


# ---------------------------------------------------------------------------
# A2 API call rate limits (evals, protect, synthetic, run-prompt, ...)
# ---------------------------------------------------------------------------

EVAL = APICallTypeChoices.DATASET_EVALUATION.value


def _seed_calls(organization, count, age):
    api_call_type = APICallType.objects.get(name=EVAL)
    APICallLog.objects.bulk_create(
        [
            APICallLog(
                api_call_type=api_call_type,
                organization=organization,
                cost=0,
                deducted_cost=0,
                status=APICallStatusChoices.SUCCESS.value,
                config="{}",
            )
            for _ in range(count)
        ]
    )
    APICallLog.objects.filter(organization=organization).update(
        created_at=timezone.now() - age
    )


WINDOWS = [
    pytest.param(51, timedelta(seconds=20), "minutely", id="51-per-minute"),
    pytest.param(301, timedelta(minutes=30), "hourly", id="301-per-hour"),
    pytest.param(1001, timedelta(hours=12), "daily", id="1001-per-day"),
]


class TestApiRateLimit:
    @pytest.mark.parametrize(("count", "age", "_window"), WINDOWS)
    def test_self_hosted_never_rate_limits(
        self, self_hosted, organization, count, age, _window
    ):
        """AC-05 / A2: above 50/min, 300/h and 1,000/day nothing is RATE_LIMITED."""
        _seed_calls(organization, count, age)
        with CaptureQueriesContext(connection) as ctx:
            assert check_if_api_call_is_rate_limited(organization, EVAL) == (False, {})
        assert _no_usage_tables_read(ctx)

        row = log_api_call(EVAL, organization, config={"reference_id": "local-eval"})
        assert row.status == APICallStatusChoices.PROCESSING.value

    @pytest.mark.parametrize(("count", "age", "_window"), WINDOWS)
    def test_byok_eval_is_not_rate_limited(
        self, self_hosted, organization, count, age, _window
    ):
        """AC-05 / A2: BYOK (non-Future AGI) evals are included."""
        _seed_calls(organization, count, age)
        row = log_and_deduct_cost_for_api_request(
            organization, EVAL, config={"is_futureagi_eval": False}
        )
        assert row is not None
        assert row.status != APICallStatusChoices.RATE_LIMITED.value

    @pytest.mark.parametrize(("count", "age", "window"), WINDOWS)
    def test_cloud_is_unchanged(self, edition_cloud, organization, count, age, window):
        """AC-13 / A2: the Cloud Free tier still rate-limits at the same windows."""
        _seed_calls(organization, count, age)
        limited, detail = check_if_api_call_is_rate_limited(organization, EVAL)
        assert limited is True
        assert detail["time"] == window


# ---------------------------------------------------------------------------
# A4 knowledge bases and the legacy resource checks
# ---------------------------------------------------------------------------


class TestKnowledgeBaseAndLegacy:
    def _make_kbs(self, organization, user, count):
        from model_hub.models.develop_dataset import KnowledgeBaseFile

        for i in range(count):
            KnowledgeBaseFile.objects.create(
                name=f"kb-{i}", organization=organization, created_by=user.email
            )

    def test_third_knowledge_base_is_allowed_off_cloud(
        self, self_hosted, organization, user
    ):
        """AC-03 / A4: a 3rd knowledge base is allowed."""
        self._make_kbs(organization, user, 2)
        assert check_if_kb_creation_is_allowed(organization) == (True, {})

    def test_cloud_still_caps_knowledge_bases(self, edition_cloud, organization, user):
        """AC-13 / A4: Cloud Free tier still stops at 2."""
        self._make_kbs(organization, user, 2)
        allowed, detail = check_if_kb_creation_is_allowed(organization)
        assert allowed is False
        assert detail["limit"] == 2

    @pytest.mark.parametrize(
        "check",
        [
            lambda org: check_if_observe_creation_is_allowed(org),
            lambda org: check_if_prototype_creation_is_allowed(org),
            check_if_trace_creation_is_allowed,
            lambda org: check_if_user_creation_is_allowed(org, {"user_count": 99}),
        ],
        ids=["observe", "prototype", "trace", "users"],
    )
    def test_legacy_checks_allow_off_cloud(self, self_hosted, organization, check):
        """AC-03: the legacy Free-tier checks never deny off-cloud."""
        ResourceLimits.objects.all().delete()
        with CaptureQueriesContext(connection) as ctx:
            assert check(organization) == (True, {})
        assert _no_usage_tables_read(ctx)

    def test_cloud_legacy_check_still_fails_closed(self, edition_cloud, organization):
        """AC-13: on Cloud a missing Free-tier row still denies, as today."""
        ResourceLimits.objects.all().delete()
        allowed, _detail = check_if_observe_creation_is_allowed(organization)
        assert allowed is False


def test_off_cloud_caps_never_create_subscriptions(self_hosted, organization):
    """AC-09-style: off-cloud guards precede the subscription bootstrap."""
    from ee.usage.models.usage import OrganizationSubscription

    check_if_row_limit_reached(organization, 5000)
    check_if_api_call_is_rate_limited(organization, EVAL)
    check_if_kb_creation_is_allowed(organization)
    assert not OrganizationSubscription.objects.filter(
        organization=organization
    ).exists()
    assert usage_entries.edition is edition
