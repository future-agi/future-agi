# ruff: noqa: F811
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.utils import timezone

from simulate.models import HostedHarnessJob, HostedHarnessStageOutput
from simulate.models.run_test import RunTest
from simulate.models.test_execution import TestExecution
from simulate.services.harness_environment import (
    AGENT_TYPE_CHAT,
    AGENT_TYPE_VOICE,
    STATUS_BUILDING,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_RUNNING,
    _sub_goals_count,
    _tools_count,
    agent_type,
    environment_name,
    status_for,
)
from simulate.services.hosted_harness import (
    DELETE_CANCEL_REASON,
    HostedHarnessError,
    create_hosted_job,
    finish_deferred_delete,
)

from .test_harness_environment_evals import env_client, environment  # noqa: F401
from .test_hosted_harness_channels import _payload

ENVIRONMENTS = "/simulate/api/harness-environments"

pytestmark = pytest.mark.django_db


def _job(user, workspace, key, **overrides):
    payload = _payload(**overrides)
    job, _ = create_hosted_job(
        user.organization, payload, idempotency_key=key, workspace=workspace
    )
    return job


def _list(client, workspace, **params):
    return client.get(f"{ENVIRONMENTS}/", params, HTTP_X_WORKSPACE_ID=str(workspace.id))


def _detail(client, job_id, workspace):
    return client.get(
        f"{ENVIRONMENTS}/{job_id}/", HTTP_X_WORKSPACE_ID=str(workspace.id)
    )


def _rename(client, job_id, workspace, body):
    return client.patch(
        f"{ENVIRONMENTS}/{job_id}/",
        body,
        format="json",
        HTTP_X_WORKSPACE_ID=str(workspace.id),
    )


def _delete(client, job_id, workspace):
    return client.delete(
        f"{ENVIRONMENTS}/{job_id}/", HTTP_X_WORKSPACE_ID=str(workspace.id)
    )


def _run(client, job_id, workspace):
    return client.post(
        f"{ENVIRONMENTS}/{job_id}/run/",
        {},
        format="json",
        HTTP_X_WORKSPACE_ID=str(workspace.id),
    )


def _set_state(job, state):
    job.state = state
    job.save(update_fields=["state", "updated_at"])
    return job


def _stage_output(job, kind, data):
    return HostedHarnessStageOutput.objects.create(
        job=job, title=kind, summary="", kind=kind, data=data
    )


def _other_workspace(user, name="Other workspace"):
    from accounts.models.workspace import Workspace

    return Workspace.objects.create(
        name=name,
        organization=user.organization,
        is_default=False,
        is_active=True,
        created_by=user,
    )


class TestList:
    def test_paginates_with_limit_and_page(self, env_client, user, workspace):
        for index in range(3):
            _job(user, workspace, f"env-page-{index}")

        first = _list(env_client, workspace, limit=2)
        second = _list(env_client, workspace, limit=2, page=2)

        assert first.status_code == 200, first.content
        body = first.json()
        assert body["count"] == 3
        assert body["total_pages"] == 2
        assert body["current_page"] == 1
        assert len(body["results"]) == 2
        assert body["next"] is not None
        assert second.json()["current_page"] == 2
        assert len(second.json()["results"]) == 1
        assert second.json()["next"] is None

    def test_limit_above_the_cap_is_a_400(self, env_client, workspace):
        response = _list(env_client, workspace, limit=101)

        assert response.status_code == 400

    def test_newest_environment_first(self, env_client, user, workspace):
        first = _job(user, workspace, "env-order-1")
        second = _job(user, workspace, "env-order-2")
        third = _job(user, workspace, "env-order-3")
        first.content_updated_at = timezone.now()
        first.save(update_fields=["content_updated_at", "updated_at"])

        response = _list(env_client, workspace)

        assert [row["id"] for row in response.json()["results"]] == [
            str(third.id),
            str(second.id),
            str(first.id),
        ]

    @pytest.mark.parametrize(
        "state,expected",
        [
            (HostedHarnessJob.State.RECEIVED, STATUS_BUILDING),
            (HostedHarnessJob.State.QUEUED, STATUS_BUILDING),
            (HostedHarnessJob.State.ADMITTED, STATUS_BUILDING),
            (HostedHarnessJob.State.PROVISIONING, STATUS_BUILDING),
            (HostedHarnessJob.State.RETRY_WAIT, STATUS_BUILDING),
            (HostedHarnessJob.State.RUNNING, STATUS_RUNNING),
            (HostedHarnessJob.State.FINALIZING, STATUS_RUNNING),
            (HostedHarnessJob.State.CLEANING_UP, STATUS_RUNNING),
            (HostedHarnessJob.State.COMPLETED, STATUS_COMPLETED),
            (HostedHarnessJob.State.FAILED, STATUS_FAILED),
            (HostedHarnessJob.State.CANCELED, STATUS_FAILED),
        ],
    )
    def test_status_collapses_every_job_state(
        self, env_client, user, workspace, state, expected
    ):
        job = _set_state(_job(user, workspace, f"env-state-{state}"), state)

        row = _list(env_client, workspace).json()["results"][0]

        assert row["id"] == str(job.id)
        assert row["status"] == expected
        assert row["stage"] == job.current_stage

    def test_soft_deleted_rows_are_hidden(self, env_client, user, workspace):
        job = _job(user, workspace, "env-hidden")
        job.deleted = True
        job.save(update_fields=["deleted", "updated_at"])

        assert _list(env_client, workspace).json()["count"] == 0
        assert _detail(env_client, job.id, workspace).status_code == 404

    def test_scoped_to_the_requested_workspace(self, env_client, user, workspace):
        mine = _job(user, workspace, "env-mine")
        _job(user, _other_workspace(user), "env-theirs")

        response = _list(env_client, workspace)

        assert [row["id"] for row in response.json()["results"]] == [str(mine.id)]

    def test_another_organizations_rows_are_invisible(
        self, env_client, user, workspace
    ):
        from accounts.models.organization import Organization
        from accounts.models.workspace import Workspace

        other_org = Organization.objects.create(name="Elsewhere")
        other_workspace = Workspace.objects.create(
            name="Elsewhere's workspace",
            organization=other_org,
            is_default=True,
            is_active=True,
            created_by=user,
        )
        foreign, _ = create_hosted_job(
            other_org,
            _payload(),
            idempotency_key="env-foreign-org",
            workspace=other_workspace,
        )

        assert _list(env_client, workspace).json()["count"] == 0
        assert _detail(env_client, foreign.id, workspace).status_code == 404

    def test_counts_are_null_until_authoring_writes_them(
        self, env_client, user, workspace
    ):
        _job(user, workspace, "env-unauthored")

        row = _list(env_client, workspace).json()["results"][0]

        assert row["description"] is None
        assert row["tools_count"] is None
        assert row["sub_goals_count"] is None
        assert row["domain"] is None
        assert row["runs_count"] == 0

    def test_counts_read_from_the_verified_snapshots(self, env_client, user, workspace):
        job = _job(user, workspace, "env-authored")
        _stage_output(
            job,
            "contract",
            {
                "one_liner": "Handles refunds",
                "tools": [{"name": "lookup_order"}, {"name": "issue_refund"}],
            },
        )
        _stage_output(
            job,
            "sub_goals",
            {"sub_goals": [{"name": "verify identity"}, {"what": "unnamed"}]},
        )

        row = _list(env_client, workspace).json()["results"][0]

        assert row["description"] == "Handles refunds"
        assert row["tools_count"] == 2
        assert row["sub_goals_count"] == 1

    def test_counts_fall_back_to_the_live_stage_outputs(
        self, env_client, user, workspace
    ):
        job = _job(user, workspace, "env-live-copy")
        job.stage_outputs = [
            {"kind": "contract", "data": {"one_liner": "Live copy", "tools": []}}
        ]
        job.save(update_fields=["stage_outputs", "updated_at"])

        row = _list(env_client, workspace).json()["results"][0]

        assert row["description"] == "Live copy"
        assert row["tools_count"] == 0

    def test_last_updated_falls_back_to_created_at(self, env_client, user, workspace):
        job = _job(user, workspace, "env-undated")
        job.content_updated_at = None
        job.save(update_fields=["content_updated_at", "updated_at"])

        row = _list(env_client, workspace).json()["results"][0]

        assert row["last_updated"] == row["created_at"]
        assert row["created_at"] == job.created_at.isoformat()

    def test_registered_scenarios_outrank_the_requested_count(
        self,
        env_client,
        environment,
        workspace,
    ):
        environment.scenario_count = 5
        environment.save(update_fields=["scenario_count", "updated_at"])

        row = _list(env_client, workspace).json()["results"][0]

        assert row["scenario_count"] == 1

    def test_runs_count_reflects_a_test_execution(self, env_client, user, workspace):
        job = _job(user, workspace, "env-ran")
        run_test = RunTest.objects.create(
            name="Hosted run", organization=user.organization, workspace=workspace
        )
        job.run_test = run_test
        job.test_execution = TestExecution.objects.create(
            run_test=run_test, status=TestExecution.ExecutionStatus.COMPLETED
        )
        job.save(update_fields=["run_test", "test_execution", "updated_at"])

        row = _list(env_client, workspace).json()["results"][0]

        assert row["runs_count"] == 1

    def test_metadata_name_wins_over_the_derived_one(self, env_client, user, workspace):
        _job(user, workspace, "env-named", metadata={"agent_name": "Refund bot"})

        row = _list(env_client, workspace).json()["results"][0]

        assert row["name"] == "Refund bot"


class TestProjectionHelpers:
    def test_name_precedence(self):
        job = HostedHarnessJob(
            id="12345678-0000-0000-0000-000000000000",
            payload={
                "metadata": {"agent_name": "Agent name", "name": "Run name"},
                "source": {"repository": "acme/support-agent"},
            },
        )

        assert environment_name(job) == "Agent name"
        job.payload["metadata"].pop("agent_name")
        assert environment_name(job) == "Run name"
        job.payload["metadata"] = {}
        assert environment_name(job) == "support-agent"
        job.payload["source"] = {}
        assert environment_name(job) == "simulation-12345678"
        job.name = "  Renamed by hand  "
        assert environment_name(job) == "Renamed by hand"

    @pytest.mark.parametrize(
        "connector,contract,expected",
        [
            ("vapi", {}, AGENT_TYPE_VOICE),
            ("phone", {}, AGENT_TYPE_VOICE),
            ("livekit", {}, AGENT_TYPE_VOICE),
            ("retell_chat", {"modality": "voice"}, AGENT_TYPE_CHAT),
            ("auto", {"modality": "voice"}, AGENT_TYPE_VOICE),
            ("auto", {"modality": "text"}, AGENT_TYPE_CHAT),
            ("auto", {}, AGENT_TYPE_CHAT),
            ("", {}, AGENT_TYPE_CHAT),
        ],
    )
    def test_agent_type(self, connector, contract, expected):
        job = HostedHarnessJob(payload={"agent": {"connector": connector}})

        assert agent_type(job, contract) == expected

    def test_status_for_uses_the_same_table_as_the_list(self):
        assert status_for(HostedHarnessJob(state="finalizing")) == STATUS_RUNNING
        assert status_for(HostedHarnessJob(state="canceled")) == STATUS_FAILED
        assert status_for(HostedHarnessJob(state="received")) == STATUS_BUILDING

    def test_sub_goals_count_distinguishes_absent_from_empty(self):
        assert _sub_goals_count(None) is None
        assert _sub_goals_count({"sub_goals": "not-a-list"}) is None
        assert _sub_goals_count({"sub_goals": []}) == 0
        assert _sub_goals_count({"sub_goals": [{"name": "a"}, {"name": ""}, 3]}) == 1

    def test_tools_count_distinguishes_absent_from_empty(self):
        assert _tools_count({}) is None
        assert _tools_count({"tools": None}) is None
        assert _tools_count({"tools": []}) == 0
        assert _tools_count({"tools": [{}, {}]}) == 2


class TestDetail:
    def test_unknown_id_is_a_404(self, env_client, workspace):
        response = _detail(
            env_client, "00000000-0000-0000-0000-000000000000", workspace
        )

        assert response.status_code == 404
        assert response.json()["detail"] == "Environment not found"

    def test_non_uuid_id_is_a_404_not_a_500(self, env_client, workspace):
        response = _detail(env_client, "not-a-uuid", workspace)

        assert response.status_code == 404

    def test_sections_are_null_before_authoring(self, env_client, user, workspace):
        job = _job(user, workspace, "env-detail-bare")

        body = _detail(env_client, job.id, workspace).json()

        assert body["id"] == str(job.id)
        assert body["contract"] is None
        assert body["world"] is None
        assert body["scenarios"] == []
        assert body["evaluations"] == {"selected": [], "results": []}
        assert body["overview"]["agent"] is None
        assert body["overview"]["run"] == {
            "run_test_id": None,
            "test_execution_id": None,
            "simulation_url": None,
        }
        assert body["overview"]["evaluations_count"] == 0
        assert body["overview"]["personas_count"] is None

    def test_settings_split_secret_refs_into_values_and_files(
        self, env_client, user, workspace
    ):
        job = _job(
            user,
            workspace,
            "env-detail-settings",
            agent={
                "connector": "vapi",
                "mode": "connect_only",
                "call_direction": "outbound",
                "config": {"assistant_id": "asst_1", "api_key": "sk-live"},
                "secret_refs": {
                    "VAPI_API_KEY": {
                        "manager": "platform-vault",
                        "key": "harness-vapi-key",
                        "version": "1",
                        "purpose": "target_provider",
                    },
                    "GOOGLE_APPLICATION_CREDENTIALS_JSON": {
                        "manager": "platform-vault",
                        "key": "harness-google-adc-abc123",
                        "version": "1",
                        "purpose": "target_provider",
                    },
                    "MOUNTED_FILE": {
                        "manager": "harness_environment_file",
                        "key": "file-1",
                    },
                },
            },
        )

        settings = _detail(env_client, job.id, workspace).json()["settings"]

        assert settings["agent"]["connector"] == "vapi"
        assert settings["agent"]["mode"] == "connect_only"
        assert settings["agent"]["call_direction"] == "outbound"
        assert settings["agent"]["secret_refs"] == [
            "GOOGLE_APPLICATION_CREDENTIALS_JSON",
            "MOUNTED_FILE",
            "VAPI_API_KEY",
        ]
        assert settings["agent"]["secrets"] == ["VAPI_API_KEY"]
        assert settings["agent"]["credential_files"] == [
            {"environment_name": "GOOGLE_APPLICATION_CREDENTIALS_JSON"},
            {"environment_name": "MOUNTED_FILE"},
        ]
        assert settings["agent"]["config"]["assistant_id"] == "asst_1"
        assert settings["agent"]["config"].get("api_key") != "sk-live"
        assert settings["source"] == {
            "kind": "remote",
            "endpoint": "https://agent.example.com",
            "visibility": "public",
        }
        assert settings["scenario_count"] == 1
        assert settings["seed"] == 7
        assert settings["schema_version"] == "futureagi.harness-job.v1"

    def test_agent_appears_once_the_run_is_provisioned(
        self,
        env_client,
        environment,
        workspace,
    ):
        body = _detail(env_client, environment.id, workspace).json()

        agent = body["overview"]["agent"]
        assert agent is not None
        assert agent["provider"] == "vapi"
        assert agent["versions_count"] == 0
        assert agent["active_version"] is None
        assert body["overview"]["run"]["run_test_id"] == str(environment.run_test_id)
        assert body["overview"]["run"]["simulation_url"] is None
        assert [row["scenario_key"] for row in body["scenarios"]] == ["refund-request"]


class TestRename:
    def test_renames_and_moves_the_content_clock(self, env_client, user, workspace):
        job = _job(user, workspace, "env-rename")
        before = job.content_updated_at

        response = _rename(env_client, job.id, workspace, {"name": "  Refunds v2  "})

        assert response.status_code == 200, response.content
        assert response.json()["overview"]["name"] == "Refunds v2"
        job.refresh_from_db()
        assert job.name == "Refunds v2"
        assert job.content_updated_at > before
        row = _list(env_client, workspace).json()["results"][0]
        assert row["name"] == "Refunds v2"
        assert row["last_updated"] == job.content_updated_at.isoformat()

    @pytest.mark.parametrize("body", [{"name": ""}, {"name": "   "}, {}])
    def test_blank_name_is_refused(self, env_client, user, workspace, body):
        job = _job(user, workspace, "env-rename-blank")

        response = _rename(env_client, job.id, workspace, body)

        assert response.status_code == 400
        job.refresh_from_db()
        assert job.name == ""

    def test_only_the_name_is_editable(self, env_client, user, workspace):
        job = _job(user, workspace, "env-rename-extra")

        response = _rename(
            env_client, job.id, workspace, {"name": "ok", "domain": "billing"}
        )

        assert response.status_code == 400

    def test_name_longer_than_the_column_is_refused(self, env_client, user, workspace):
        job = _job(user, workspace, "env-rename-long")

        response = _rename(env_client, job.id, workspace, {"name": "x" * 256})

        assert response.status_code == 400

    def test_unknown_environment_is_a_404(self, env_client, workspace):
        response = _rename(
            env_client, "00000000-0000-0000-0000-000000000000", workspace, {"name": "x"}
        )

        assert response.status_code == 404

    def test_cannot_rename_across_workspaces(self, env_client, user, workspace):
        job = _job(user, _other_workspace(user), "env-rename-foreign")

        response = _rename(env_client, job.id, workspace, {"name": "mine now"})

        assert response.status_code == 404
        job.refresh_from_db()
        assert job.name == ""


class TestDelete:
    @pytest.mark.parametrize(
        "state",
        [
            HostedHarnessJob.State.COMPLETED,
            HostedHarnessJob.State.FAILED,
            HostedHarnessJob.State.CANCELED,
        ],
    )
    def test_terminal_job_is_hidden_at_once(self, env_client, user, workspace, state):
        job = _set_state(_job(user, workspace, f"env-delete-{state}"), state)

        with patch(
            "simulate.temporal.client.cancel_hosted_harness_gateway_workflow"
        ) as cancel:
            response = _delete(env_client, job.id, workspace)

        assert response.status_code == 204
        cancel.assert_not_called()
        job.refresh_from_db()
        assert job.deleted is True
        assert job.deleted_at is not None
        assert not job.cancel_reason
        assert _detail(env_client, job.id, workspace).status_code == 404

    def test_live_job_is_cancelled_and_stays_visible_until_cleanup(
        self, env_client, user, workspace
    ):
        job = _set_state(
            _job(user, workspace, "env-delete-live"), HostedHarnessJob.State.RUNNING
        )

        with patch(
            "simulate.temporal.client.cancel_hosted_harness_gateway_workflow"
        ) as cancel:
            response = _delete(env_client, job.id, workspace)

        assert response.status_code == 204
        cancel.assert_called_once_with(str(job.id))
        job.refresh_from_db()
        assert job.deleted is False
        assert job.cancel_reason == DELETE_CANCEL_REASON
        assert job.cancel_requested_at is not None
        assert job.state == HostedHarnessJob.State.CLEANING_UP
        assert job.current_stage == HostedHarnessJob.State.CLEANING_UP
        row = _list(env_client, workspace).json()["results"][0]
        assert row["id"] == str(job.id)
        assert row["status"] == STATUS_RUNNING

    def test_unreachable_scheduler_still_hides_the_row(
        self, env_client, user, workspace
    ):
        job = _set_state(
            _job(user, workspace, "env-delete-unreachable"),
            HostedHarnessJob.State.RUNNING,
        )

        with patch(
            "simulate.temporal.client.cancel_hosted_harness_gateway_workflow",
            side_effect=RuntimeError("temporal down"),
        ):
            response = _delete(env_client, job.id, workspace)

        assert response.status_code == 204
        job.refresh_from_db()
        assert job.deleted is True
        assert job.cancel_reason == DELETE_CANCEL_REASON
        assert _list(env_client, workspace).json()["count"] == 0

    def test_second_delete_is_a_404(self, env_client, user, workspace):
        job = _set_state(
            _job(user, workspace, "env-delete-twice"), HostedHarnessJob.State.COMPLETED
        )

        assert _delete(env_client, job.id, workspace).status_code == 204
        assert _delete(env_client, job.id, workspace).status_code == 404

    def test_cannot_delete_across_workspaces(self, env_client, user, workspace):
        job = _set_state(
            _job(user, _other_workspace(user), "env-delete-foreign"),
            HostedHarnessJob.State.COMPLETED,
        )

        response = _delete(env_client, job.id, workspace)

        assert response.status_code == 404
        job.refresh_from_db()
        assert job.deleted is False

    def test_finish_deferred_delete_only_hides_a_job_deleted_while_running(
        self, user, workspace
    ):
        job = _job(user, workspace, "env-deferred")

        assert finish_deferred_delete(job) == []
        assert job.deleted is False

        job.cancel_reason = "user_requested"
        assert finish_deferred_delete(job) == []

        job.cancel_reason = DELETE_CANCEL_REASON
        assert finish_deferred_delete(job) == ["deleted", "deleted_at"]
        assert job.deleted is True
        assert job.deleted_at is not None

        assert finish_deferred_delete(job) == []


class TestRun:
    def test_accepts_and_reports_the_started_run(self, env_client, user, workspace):
        job = _job(user, workspace, "env-run")
        provider = SimpleNamespace(
            rerun_saved=lambda *args, **kwargs: {
                "job": {"job_id": str(job.id), "run_id": "run-42"},
                "status": {"state": "queued", "stage": "queued"},
            }
        )

        with patch(
            "simulate.views.harness_environment.get_harness_provider",
            return_value=provider,
        ):
            response = _run(env_client, job.id, workspace)

        assert response.status_code == 202, response.content
        assert response.json() == {
            "environment_id": str(job.id),
            "job_id": str(job.id),
            "run_id": "run-42",
            "state": "queued",
            "stage": "queued",
        }

    def test_passes_the_callers_scope_and_no_environment_values(
        self, env_client, user, workspace
    ):
        job = _job(user, workspace, "env-run-scope")
        seen = {}

        def rerun_saved(job_id, **kwargs):
            seen["job_id"] = job_id
            seen.update(kwargs)
            return {"job": {}, "status": {}}

        with patch(
            "simulate.views.harness_environment.get_harness_provider",
            return_value=SimpleNamespace(rerun_saved=rerun_saved),
        ):
            response = _run(env_client, job.id, workspace)

        assert response.status_code == 202
        assert seen["job_id"] == str(job.id)
        assert seen["organization"] == user.organization
        assert seen["workspace"] == workspace
        assert seen["environment_values"] == {}
        assert response.json()["job_id"] == str(job.id)
        assert response.json()["run_id"] is None

    def test_provider_refusal_keeps_its_status_and_envelope(
        self, env_client, user, workspace
    ):
        job = _job(user, workspace, "env-run-refused")
        refusal = HostedHarnessError(
            "rerun_authoring_snapshot_missing",
            "no authoring snapshot to rerun",
            status_code=409,
        )

        with patch(
            "simulate.views.harness_environment.get_harness_provider",
            return_value=SimpleNamespace(
                rerun_saved=lambda *a, **k: (_ for _ in ()).throw(refusal)
            ),
        ):
            response = _run(env_client, job.id, workspace)

        assert response.status_code == 409
        assert response.json() == {
            "error": "rerun_authoring_snapshot_missing",
            "message": "no authoring snapshot to rerun",
            "retryable": False,
        }

    def test_body_must_be_empty(self, env_client, user, workspace):
        job = _job(user, workspace, "env-run-body")

        response = env_client.post(
            f"{ENVIRONMENTS}/{job.id}/run/",
            {"scenario_ids": ["a"]},
            format="json",
            HTTP_X_WORKSPACE_ID=str(workspace.id),
        )

        assert response.status_code == 400

    def test_unknown_environment_is_a_404(self, env_client, workspace):
        with patch(
            "simulate.views.harness_environment.get_harness_provider"
        ) as provider:
            response = _run(
                env_client, "00000000-0000-0000-0000-000000000000", workspace
            )

        assert response.status_code == 404
        provider.return_value.rerun_saved.assert_not_called()
