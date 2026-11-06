"""Cekura run-completed webhook: auth, contract and eval-score ingestion.

The scores land in ``EvalLogger`` because that is the table the trace and
eval read paths query. ``ClickHouseWriter.write_evaluation`` looks like the
sink for this but has no caller anywhere in the repo, so a score written
through it would never be read back.
"""

import uuid

import pytest
from rest_framework import status

from integrations.transformers.cekura_transformer import CekuraTransformer
from model_hub.models.ai_model import AIModel
from tracer.models.cekura_integration import CekuraIntegration
from tracer.models.observation_span import EvalLogger, ObservationSpan
from tracer.models.project import Project
from tracer.models.trace import Trace

SECRET = "cekura-shared-secret"
RUN_ID = "run-1a2b3c"


def webhook_path(project_id):
    return f"/tracer/cekura/webhook/{project_id}/"


def make_project(organization, workspace):
    return Project.no_workspace_objects.create(
        name=f"cekura-project-{uuid.uuid4().hex[:8]}",
        organization=organization,
        workspace=workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
    )


def make_integration(organization, workspace, *, enabled=True, secret=SECRET):
    project = make_project(organization, workspace)
    return CekuraIntegration.no_workspace_objects.create(
        project=project,
        organization=organization,
        workspace=workspace,
        signing_secret=secret,
        enabled=enabled,
    )


def run_payload(**overrides):
    payload = {
        "run_id": RUN_ID,
        "status": "completed",
        "metrics": [
            {
                "name": "task_completion",
                "score": 0.92,
                "passed": True,
                "explanation": "Booked the appointment on the first turn.",
            },
            {"name": "tone", "score": 0.4, "passed": False},
        ],
    }
    payload.update(overrides)
    return payload


def post_run(api_client, integration, payload=None, secret=SECRET, headers=None):
    request_headers = {} if headers is None else dict(headers)
    if secret is not None:
        request_headers.setdefault("X-Webhook-Secret", secret)
    return api_client.post(
        webhook_path(integration.project_id),
        data=run_payload() if payload is None else payload,
        format="json",
        headers=request_headers,
    )


def eval_rows(integration):
    return EvalLogger.no_workspace_objects.filter(
        trace__project_id=integration.project_id
    )


def result(response):
    body = response.json()
    return body.get("result", body)


@pytest.mark.django_db
class TestWebhookAuthentication:
    """Every rejection path answers the same way and writes nothing."""

    def test_valid_secret_is_accepted(self, api_client, organization, workspace):
        integration = make_integration(organization, workspace)

        response = post_run(api_client, integration)

        assert response.status_code == status.HTTP_200_OK
        assert result(response) == {"ingested": 2, "skipped": 0}

    def test_missing_secret_header_is_rejected(
        self, api_client, organization, workspace
    ):
        integration = make_integration(organization, workspace)

        response = post_run(api_client, integration, secret=None)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert not eval_rows(integration).exists()

    def test_wrong_secret_is_rejected(self, api_client, organization, workspace):
        integration = make_integration(organization, workspace)

        response = post_run(api_client, integration, secret="not-the-secret")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert not eval_rows(integration).exists()

    def test_non_ascii_secret_header_is_rejected_not_crashed(
        self, api_client, organization, workspace
    ):
        """``hmac.compare_digest`` raises TypeError on non-ASCII str input."""
        integration = make_integration(organization, workspace)

        response = post_run(api_client, integration, secret="segredo-inválido")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert not eval_rows(integration).exists()

    def test_disabled_integration_is_rejected(
        self, api_client, organization, workspace
    ):
        integration = make_integration(organization, workspace, enabled=False)

        response = post_run(api_client, integration)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert not eval_rows(integration).exists()

    def test_blank_stored_secret_never_matches(
        self, api_client, organization, workspace
    ):
        """A row saved without a secret must not accept an empty header."""
        integration = make_integration(organization, workspace, secret="")

        response = post_run(api_client, integration, secret="")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert not eval_rows(integration).exists()

    def test_project_without_integration_is_rejected(self, api_client):
        response = api_client.post(
            webhook_path(uuid.uuid4()),
            data=run_payload(),
            format="json",
            headers={"X-Webhook-Secret": SECRET},
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_secret_of_another_project_does_not_authenticate(
        self, api_client, organization, workspace
    ):
        target = make_integration(organization, workspace, secret="target-secret")
        other = make_integration(organization, workspace, secret="other-secret")

        response = post_run(api_client, target, secret=other.signing_secret)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert not eval_rows(target).exists()

    def test_rejection_message_does_not_leak_the_reason(
        self, api_client, organization, workspace
    ):
        """Disabled and wrong-secret must be indistinguishable to the caller."""
        disabled = make_integration(organization, workspace, enabled=False)
        wrong_secret = make_integration(organization, workspace)

        disabled_response = post_run(api_client, disabled)
        mismatch_response = post_run(api_client, wrong_secret, secret="nope")

        assert disabled_response.json() == mismatch_response.json()


@pytest.mark.django_db
class TestWebhookContract:
    def test_payload_without_run_id_is_rejected(
        self, api_client, organization, workspace
    ):
        integration = make_integration(organization, workspace)

        response = post_run(
            api_client, integration, payload={"status": "completed", "metrics": []}
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_blank_run_id_is_rejected(self, api_client, organization, workspace):
        integration = make_integration(organization, workspace)

        response = post_run(api_client, integration, payload=run_payload(run_id=""))

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_unknown_fields_are_tolerated(self, api_client, organization, workspace):
        """Cekura owns this payload; a field they add must not 400 here."""
        integration = make_integration(organization, workspace)

        response = post_run(
            api_client,
            integration,
            payload=run_payload(agent_id="agt_1", scorecard={"version": 3}),
        )

        assert response.status_code == status.HTTP_200_OK
        assert result(response)["ingested"] == 2

    def test_run_without_metrics_writes_nothing(
        self, api_client, organization, workspace
    ):
        integration = make_integration(organization, workspace)

        response = post_run(api_client, integration, payload=run_payload(metrics=[]))

        assert response.status_code == status.HTTP_200_OK
        assert result(response) == {"ingested": 0, "skipped": 0}
        assert not eval_rows(integration).exists()
        assert not Trace.no_workspace_objects.filter(
            project_id=integration.project_id
        ).exists()


@pytest.mark.django_db
class TestScoreIngestion:
    def test_metrics_become_eval_rows_with_their_values(
        self, api_client, organization, workspace
    ):
        integration = make_integration(organization, workspace)

        post_run(api_client, integration)

        rows = {row.eval_type_id: row for row in eval_rows(integration)}
        assert set(rows) == {"task_completion", "tone"}
        assert rows["task_completion"].output_float == pytest.approx(0.92)
        assert rows["task_completion"].output_bool is True
        assert (
            rows["task_completion"].eval_explanation
            == "Booked the appointment on the first turn."
        )
        assert rows["tone"].output_bool is False
        assert rows["tone"].eval_id == f"cekura:{RUN_ID}:tone"

    def test_run_is_anchored_to_a_trace_keyed_by_the_run_id(
        self, api_client, organization, workspace
    ):
        integration = make_integration(organization, workspace)

        post_run(api_client, integration)

        trace = Trace.no_workspace_objects.get(
            project_id=integration.project_id, external_id=RUN_ID
        )
        span = ObservationSpan.no_workspace_objects.get(trace=trace)
        assert span.id == f"root-{RUN_ID}"
        assert span.project_id == integration.project_id
        assert span.org_id == integration.organization_id
        assert all(row.trace_id == trace.id for row in eval_rows(integration))
        assert all(row.observation_span_id == span.id for row in eval_rows(integration))

    def test_existing_trace_for_the_run_is_reused(
        self, api_client, organization, workspace
    ):
        """An imported transcript and this webhook must not split in two."""
        integration = make_integration(organization, workspace)
        imported = Trace.no_workspace_objects.create(
            project_id=integration.project_id,
            external_id=RUN_ID,
            name="Imported transcript",
        )

        post_run(api_client, integration)

        traces = Trace.no_workspace_objects.filter(
            project_id=integration.project_id, external_id=RUN_ID
        )
        assert [trace.id for trace in traces] == [imported.id]
        assert Trace.no_workspace_objects.get(id=imported.id).name == (
            "Imported transcript"
        )

    def test_existing_span_is_used_instead_of_a_placeholder(
        self, api_client, organization, workspace
    ):
        integration = make_integration(organization, workspace)
        trace = Trace.no_workspace_objects.create(
            project_id=integration.project_id, external_id=RUN_ID
        )
        transcript_span = ObservationSpan.no_workspace_objects.create(
            id=f"root-{RUN_ID}",
            trace=trace,
            project_id=integration.project_id,
            name="Imported conversation",
            observation_type="chain",
        )

        post_run(api_client, integration)

        assert ObservationSpan.no_workspace_objects.filter(trace=trace).count() == 1
        assert ObservationSpan.no_workspace_objects.get(id=transcript_span.id).name == (
            "Imported conversation"
        )
        assert all(
            row.observation_span_id == transcript_span.id
            for row in eval_rows(integration)
        )

    def test_redelivery_updates_instead_of_duplicating(
        self, api_client, organization, workspace
    ):
        integration = make_integration(organization, workspace)

        post_run(api_client, integration)
        second = post_run(
            api_client,
            integration,
            payload=run_payload(
                metrics=[{"name": "task_completion", "score": 0.5, "passed": False}]
            ),
        )

        assert second.status_code == status.HTTP_200_OK
        assert eval_rows(integration).count() == 2
        updated = eval_rows(integration).get(eval_type_id="task_completion")
        assert updated.output_float == pytest.approx(0.5)
        assert updated.output_bool is False
        assert (
            Trace.no_workspace_objects.filter(
                project_id=integration.project_id, external_id=RUN_ID
            ).count()
            == 1
        )

    def test_repeated_metric_name_collapses_to_the_last_value(
        self, api_client, organization, workspace
    ):
        integration = make_integration(organization, workspace)

        response = post_run(
            api_client,
            integration,
            payload=run_payload(
                metrics=[
                    {"name": "tone", "score": 0.1},
                    {"name": "tone", "score": 0.9},
                ]
            ),
        )

        assert result(response) == {"ingested": 1, "skipped": 1}
        assert eval_rows(integration).get(eval_type_id="tone").output_float == (
            pytest.approx(0.9)
        )

    def test_metric_without_a_name_is_skipped(
        self, api_client, organization, workspace
    ):
        integration = make_integration(organization, workspace)

        response = post_run(
            api_client,
            integration,
            payload=run_payload(
                metrics=[{"score": 0.5}, {"name": "tone", "score": 0.7}]
            ),
        )

        assert result(response) == {"ingested": 1, "skipped": 1}
        assert [row.eval_type_id for row in eval_rows(integration)] == ["tone"]

    def test_in_flight_run_is_not_ingested(self, api_client, organization, workspace):
        integration = make_integration(organization, workspace)

        response = post_run(
            api_client, integration, payload=run_payload(status="running")
        )

        assert response.status_code == status.HTTP_200_OK
        assert result(response) == {"ingested": 0, "skipped": 2}
        assert not eval_rows(integration).exists()

    def test_failed_run_is_ingested(self, api_client, organization, workspace):
        """A regression run that fails is the one whose scores matter."""
        integration = make_integration(organization, workspace)

        response = post_run(
            api_client, integration, payload=run_payload(status="failed")
        )

        assert result(response)["ingested"] == 2

    def test_run_window_lands_on_the_placeholder_span(
        self, api_client, organization, workspace
    ):
        integration = make_integration(organization, workspace)

        post_run(
            api_client,
            integration,
            payload=run_payload(
                started_at="2026-08-18T10:00:00Z",
                completed_at="2026-08-18T10:00:12Z",
            ),
        )

        span = ObservationSpan.no_workspace_objects.get(id=f"root-{RUN_ID}")
        assert span.latency_ms == 12000
        assert span.start_time.isoformat().startswith("2026-08-18T10:00:00")

    def test_malformed_timestamps_do_not_lose_the_scores(
        self, api_client, organization, workspace
    ):
        integration = make_integration(organization, workspace)

        response = post_run(
            api_client,
            integration,
            payload=run_payload(started_at="not-a-timestamp", completed_at=""),
        )

        assert result(response)["ingested"] == 2
        assert (
            ObservationSpan.no_workspace_objects.get(id=f"root-{RUN_ID}").latency_ms
            is None
        )

    def test_run_id_longer_than_the_serializer_allows_is_rejected(
        self, api_client, organization, workspace
    ):
        integration = make_integration(organization, workspace)

        response = post_run(
            api_client, integration, payload=run_payload(run_id="r" * 256)
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_max_length_run_id_fits_every_id_column(
        self, api_client, organization, workspace
    ):
        """Span ids cap at 255 and eval ids at 255; both halves are input."""
        integration = make_integration(organization, workspace)
        long_run_id = "r" * 255

        response = post_run(
            api_client, integration, payload=run_payload(run_id=long_run_id)
        )

        assert response.status_code == status.HTTP_200_OK
        span = ObservationSpan.no_workspace_objects.get(trace__external_id=long_run_id)
        assert len(span.id) == 250
        assert all(len(row.eval_id) <= 255 for row in eval_rows(integration))

    def test_redelivery_of_a_long_run_still_matches_its_rows(
        self, api_client, organization, workspace
    ):
        """The hashed key has to stay stable, or replays would duplicate."""
        integration = make_integration(organization, workspace)
        payload = run_payload(run_id="r" * 255)

        post_run(api_client, integration, payload=payload)
        post_run(api_client, integration, payload=payload)

        assert eval_rows(integration).count() == 2

    def test_scores_stay_inside_their_own_project(
        self, api_client, organization, workspace
    ):
        integration = make_integration(organization, workspace)
        neighbour = make_integration(organization, workspace)

        post_run(api_client, integration)

        assert eval_rows(integration).count() == 2
        assert not eval_rows(neighbour).exists()


@pytest.mark.unit
class TestCekuraTransformer:
    """Value coercion, isolated from the webhook and the database."""

    transformer = CekuraTransformer()

    def test_boolean_score_is_not_stored_as_a_float(self):
        """``bool`` is an ``int`` subclass; True must not become 1.0."""
        fields = self.transformer.to_eval_logger_fields(
            {"run_id": "r", "metrics": [{"name": "tone", "score": True}]}
        )

        assert fields[0]["output_float"] is None

    def test_numeric_strings_are_coerced(self):
        fields = self.transformer.to_eval_logger_fields(
            {"run_id": "r", "metrics": [{"name": "tone", "score": "0.75"}]}
        )

        assert fields[0]["output_float"] == pytest.approx(0.75)

    def test_unparseable_score_becomes_null(self):
        fields = self.transformer.to_eval_logger_fields(
            {"run_id": "r", "metrics": [{"name": "tone", "score": "high"}]}
        )

        assert fields[0]["output_float"] is None

    @pytest.mark.parametrize(
        "reported,expected",
        [
            (True, True),
            (False, False),
            ("pass", True),
            ("FAILED", False),
            ("maybe", None),
            (1, None),
            (None, None),
        ],
    )
    def test_pass_flag_coercion(self, reported, expected):
        fields = self.transformer.to_eval_logger_fields(
            {"run_id": "r", "metrics": [{"name": "tone", "passed": reported}]}
        )

        assert fields[0]["output_bool"] is expected

    def test_metric_name_longer_than_its_column_is_dropped(self):
        """A 500 here would cost the run its other scores on every retry."""
        fields = self.transformer.to_eval_logger_fields(
            {
                "run_id": "r",
                "metrics": [{"name": "n" * 256}, {"name": "tone"}],
            }
        )

        assert [field["eval_type_id"] for field in fields] == ["tone"]

    def test_non_dict_metric_is_ignored(self):
        fields = self.transformer.to_eval_logger_fields(
            {"run_id": "r", "metrics": ["tone", {"name": "tone"}]}
        )

        assert len(fields) == 1

    @pytest.mark.parametrize(
        "run_status,ingestible",
        [
            ("completed", True),
            ("failed", True),
            ("", True),
            ("RUNNING", False),
            ("queued", False),
            (" in_progress ", False),
            ("cancelled", False),
        ],
    )
    def test_run_state_gating(self, run_status, ingestible):
        assert self.transformer.is_ingestible({"status": run_status}) is ingestible

    def test_absent_status_is_ingestible(self):
        assert self.transformer.is_ingestible({}) is True
