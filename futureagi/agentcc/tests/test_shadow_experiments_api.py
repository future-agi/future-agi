"""Coverage for the /agentcc/shadow-experiments/ and /agentcc/shadow-results/ endpoints."""

import json
from unittest.mock import patch

import pytest

from agentcc.models.shadow_experiment import AgentccShadowExperiment
from agentcc.models.shadow_result import AgentccShadowResult


def _make_experiment(user, name="test-exp", status=None, **overrides):
    kwargs = {
        "organization": user.organization,
        "workspace": None,
        "created_by": user,
        "name": name,
        "source_model": "gpt-4o",
        "shadow_model": "claude-3-5-sonnet",
        "shadow_provider": "anthropic",
        "sample_rate": 0.1,
    }
    if status is not None:
        kwargs["status"] = status
    kwargs.update(overrides)
    return AgentccShadowExperiment.no_workspace_objects.create(**kwargs)


@pytest.mark.integration
@pytest.mark.api
class TestShadowExperimentCRUD:
    def test_list_returns_experiments(self, auth_client, user):
        _make_experiment(user, name="exp-a")
        _make_experiment(user, name="exp-b")

        response = auth_client.get("/agentcc/shadow-experiments/")
        assert response.status_code == 200
        result = response.json()["result"]
        names = {row["name"] for row in result}
        assert {"exp-a", "exp-b"} <= names

    def test_list_filter_by_status(self, auth_client, user):
        _make_experiment(
            user, name="active-one", status=AgentccShadowExperiment.STATUS_ACTIVE
        )
        _make_experiment(
            user, name="paused-one", status=AgentccShadowExperiment.STATUS_PAUSED
        )

        response = auth_client.get("/agentcc/shadow-experiments/?status=paused")
        assert response.status_code == 200
        names = {row["name"] for row in response.json()["result"]}
        assert names == {"paused-one"}

    def test_list_unauthenticated(self, api_client):
        response = api_client.get("/agentcc/shadow-experiments/")
        assert response.status_code in (401, 403)

    def test_create_answers_501_and_stores_nothing(self, auth_client, user):
        # docs-agentcc-711, item 3: no experiment mirrors traffic, so none is
        # created (the plan-limit check is never reached either).
        with patch("tfc.ee_gating.check_ee_can_create") as limit:
            response = auth_client.post(
                "/agentcc/shadow-experiments/",
                {
                    "name": "brand-new-exp",
                    "source_model": "gpt-4o",
                    "shadow_model": "claude-3-5-sonnet",
                    "shadow_provider": "anthropic",
                    "sample_rate": 0.25,
                },
                format="json",
            )
        assert response.status_code == 501, response.json()
        assert response.json()["code"] == "shadow_experiments_not_active"
        assert not AgentccShadowExperiment.no_workspace_objects.filter(
            name="brand-new-exp"
        ).exists()
        limit.assert_not_called()

    def test_retrieve_returns_experiment(self, auth_client, user):
        exp = _make_experiment(user, name="retrieve-me")
        response = auth_client.get(f"/agentcc/shadow-experiments/{exp.id}/")
        assert response.status_code == 200
        assert response.json()["result"]["id"] == str(exp.id)

    def test_partial_update_writes_field(self, auth_client, user):
        exp = _make_experiment(user, name="orig-name")
        response = auth_client.patch(
            f"/agentcc/shadow-experiments/{exp.id}/",
            {"description": "now with a description"},
            format="json",
        )
        assert response.status_code == 200
        exp.refresh_from_db()
        assert exp.description == "now with a description"

    @pytest.mark.parametrize("method", ["put", "patch"])
    @pytest.mark.parametrize(
        "before, after",
        [
            (before, after)
            for before in ("active", "paused", "completed")
            for after in ("active", "paused", "completed")
            if before != after
        ],
    )
    def test_ordinary_update_refuses_lifecycle_changes(
        self, auth_client, user, method, before, after
    ):
        exp = _make_experiment(user, status=before, description="original")
        payload = {
            "name": exp.name,
            "source_model": exp.source_model,
            "shadow_model": exp.shadow_model,
            "shadow_provider": exp.shadow_provider,
            "description": "must not save either",
            "status": after,
        }
        with patch("agentcc.services.config_push.push_org_config") as push:
            response = getattr(auth_client, method)(
                f"/agentcc/shadow-experiments/{exp.id}/", payload, format="json"
            )

        assert response.status_code == 501, response.json()
        assert response.json()["code"] == "shadow_experiments_not_active"
        exp.refresh_from_db()
        assert exp.status == before
        assert exp.description == "original"
        push.assert_not_called()

    @pytest.mark.parametrize("method", ["put", "patch"])
    @pytest.mark.parametrize("stored_status", ["active", "paused", "completed"])
    @pytest.mark.parametrize("include_status", [False, True])
    def test_metadata_update_can_omit_or_preserve_status(
        self, auth_client, user, method, stored_status, include_status
    ):
        exp = _make_experiment(user, status=stored_status)
        payload = {
            "name": exp.name,
            "source_model": exp.source_model,
            "shadow_model": exp.shadow_model,
            "shadow_provider": exp.shadow_provider,
            "description": "metadata edit",
        }
        if include_status:
            payload["status"] = stored_status
        response = getattr(auth_client, method)(
            f"/agentcc/shadow-experiments/{exp.id}/", payload, format="json"
        )

        assert response.status_code == 200, response.json()
        exp.refresh_from_db()
        assert exp.description == "metadata edit"
        assert exp.status == stored_status

    def test_destroy_soft_deletes_experiment_and_results(self, auth_client, user):
        exp = _make_experiment(user, name="to-delete")
        # Attach a result row so we can verify the cascade.
        result = AgentccShadowResult.no_workspace_objects.create(
            experiment=exp,
            organization=user.organization,
            workspace=None,
            source_model="gpt-4o",
            shadow_model="claude-3-5-sonnet",
        )

        response = auth_client.delete(f"/agentcc/shadow-experiments/{exp.id}/")
        assert response.status_code == 200
        assert response.json()["result"]["deleted"] is True

        exp.refresh_from_db()
        assert exp.deleted is True
        assert exp.deleted_at is not None
        # Results are cascade-soft-deleted (raw SQL check bypasses any
        # manager-level deleted filter).
        from django.db import connection

        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT deleted FROM agentcc_shadow_result WHERE id = %s",
                [str(result.id)],
            )
            row = cursor.fetchone()
        assert row is not None and row[0] is True


@pytest.mark.integration
@pytest.mark.api
class TestShadowExperimentLifecycle:
    """docs-agentcc-711, item 3: pause, resume and complete answer 501, leave
    the stored experiment as it was and push nothing (they used to answer
    gateway_synced for a push that carried nothing about the experiment)."""

    @pytest.mark.parametrize(
        "action, status",
        [
            ("pause", AgentccShadowExperiment.STATUS_ACTIVE),
            ("resume", AgentccShadowExperiment.STATUS_PAUSED),
            ("complete", AgentccShadowExperiment.STATUS_ACTIVE),
        ],
    )
    @patch("agentcc.services.config_push.push_org_config", return_value=True)
    def test_lifecycle_action_answers_501_and_changes_nothing(
        self, mock_push, auth_client, user, action, status
    ):
        exp = _make_experiment(user, status=status)
        response = auth_client.patch(f"/agentcc/shadow-experiments/{exp.id}/{action}/")
        assert response.status_code == 501, response.json()
        body = response.json()
        assert body["code"] == "shadow_experiments_not_active"
        assert "gateway_synced" not in json.dumps(body)
        assert "restart" not in json.dumps(body)
        exp.refresh_from_db()
        assert exp.status == status
        mock_push.assert_not_called()


@pytest.mark.integration
@pytest.mark.api
class TestShadowExperimentStats:
    def test_stats_aggregates_over_results(self, auth_client, user):
        exp = _make_experiment(user)
        for source_ms, shadow_ms in [(100, 150), (200, 250), (300, 400)]:
            AgentccShadowResult.no_workspace_objects.create(
                experiment=exp,
                organization=user.organization,
                workspace=None,
                source_model="gpt-4o",
                shadow_model="claude-3-5-sonnet",
                source_latency_ms=source_ms,
                shadow_latency_ms=shadow_ms,
                source_status_code=200,
                shadow_status_code=200,
            )

        response = auth_client.get(f"/agentcc/shadow-experiments/{exp.id}/stats/")
        assert response.status_code == 200
        stats = response.json()["result"]
        assert stats["total_comparisons"] == 3
        assert stats["avg_source_latency_ms"] == 200.0
        assert stats["avg_shadow_latency_ms"] == pytest.approx(266.7, rel=1e-2)

    def test_stats_empty_returns_zeroed_shape(self, auth_client, user):
        exp = _make_experiment(user)
        response = auth_client.get(f"/agentcc/shadow-experiments/{exp.id}/stats/")
        assert response.status_code == 200
        stats = response.json()["result"]
        assert stats["total_comparisons"] == 0
        assert stats["latency_delta_pct"] == 0


@pytest.mark.integration
@pytest.mark.api
class TestShadowResultReadOnly:
    def test_list_returns_results_for_org(self, auth_client, user):
        exp = _make_experiment(user)
        AgentccShadowResult.no_workspace_objects.create(
            experiment=exp,
            organization=user.organization,
            workspace=None,
            source_model="gpt-4o",
            shadow_model="claude-3-5-sonnet",
        )

        response = auth_client.get("/agentcc/shadow-results/")
        assert response.status_code == 200
        body = response.json()
        # DRF pagination wraps results under "results"; the unpaginated
        # branch wraps under _gm.success_response's "result" key. Accept
        # both shapes.
        rows = body.get("result") if "result" in body else body.get("results", [])
        assert isinstance(rows, list)
        assert len(rows) >= 1

    def test_retrieve_returns_result(self, auth_client, user):
        exp = _make_experiment(user)
        row = AgentccShadowResult.no_workspace_objects.create(
            experiment=exp,
            organization=user.organization,
            workspace=None,
            source_model="gpt-4o",
            shadow_model="claude-3-5-sonnet",
        )

        response = auth_client.get(f"/agentcc/shadow-results/{row.id}/")
        assert response.status_code == 200
        assert response.json()["result"]["id"] == str(row.id)
