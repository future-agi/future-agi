"""Tracer, accounts, socket and AI tool boundaries stay in the caller's scope.

Synthetic tenants only; channel layers, embeddings, knowledge base search
and LLM work are mocked. Rejections compare with an unknown id; controls
use the caller's own rows.
"""

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from rest_framework import serializers, status

from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from ai_tools.base import ToolContext
from model_hub.models.choices import DatasetSourceChoices, OwnerChoices
from model_hub.models.develop_dataset import Dataset, KnowledgeBaseFile, Row
from model_hub.models.evals_metric import EvalTemplate
from model_hub.models.experiments import ExperimentsTable
from model_hub.models.run_prompt import PromptTemplate, PromptVersion
from tfc.constants.levels import Level
from tfc.middleware.workspace_context import clear_workspace_context
from tfc.utils.tests.two_tenants import former_member, make_tenant, same_answer
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.project import Project
from tracer.models.trace import Trace

pytestmark = [pytest.mark.integration, pytest.mark.api]


@pytest.fixture
def tenants(db, settings):
    settings.FUTURE_AGI_LICENSE_URL = "http://127.0.0.1:9"
    return make_tenant("A"), make_tenant("B")


def _project(tenant, name="observe", trace_type="observe"):
    clear_workspace_context()
    return Project.no_workspace_objects.create(
        name=f"{name}-{uuid.uuid4().hex[:6]}",
        organization=tenant.organization,
        workspace=tenant.workspace,
        trace_type=trace_type,
        model_type="GenerativeLLM",
    )


def _eval_config(project):
    clear_workspace_context()
    template = EvalTemplate.no_workspace_objects.create(
        name=f"cfg-{uuid.uuid4().hex[:6]}",
        organization=project.organization,
        owner=OwnerChoices.USER.value,
        config={"required_keys": []},
    )
    return CustomEvalConfig.no_workspace_objects.create(
        name=f"config-{uuid.uuid4().hex[:6]}",
        eval_template=template,
        project=project,
        config={},
        mapping={},
    )


def _request(tenant):
    return SimpleNamespace(
        organization=tenant.organization, workspace=tenant.workspace, user=tenant.user
    )


# --- tracer ------------------------------------------------------------------


class TestTracerBoundaries:
    def test_span_serializer_refuses_foreign_prompt_version_and_eval_config(
        self, tenants
    ):
        from tracer.serializers.observation_span import ObservationSpanSerializer

        a, b = tenants
        clear_workspace_context()
        template = PromptTemplate.no_workspace_objects.create(
            name="b prompt",
            organization=b.organization,
            workspace=b.workspace,
            created_by=b.user,
        )
        foreign_version = PromptVersion.no_workspace_objects.create(
            original_template=template,
            template_version="v1",
            prompt_config_snapshot=[],
        )
        foreign_config = _eval_config(_project(b))
        unknown_id = uuid.uuid4()
        for field, foreign_id in (
            ("prompt_version", foreign_version.id),
            ("custom_eval_config", foreign_config.id),
        ):
            errors = []
            for value in (foreign_id, unknown_id):
                serializer = ObservationSpanSerializer(
                    data={field: str(value)},
                    partial=True,
                    context={"request": _request(a)},
                )
                assert not serializer.is_valid()
                errors.append(str(serializer.errors[field]).replace(str(value), "<id>"))
            assert errors[0] == errors[1]

    def test_trace_bulk_create_refuses_raw_relation_ids(self, tenants):
        a, b = tenants
        own = _project(a)
        foreign = _project(b)
        clear_workspace_context()
        response = a.client.post(
            "/tracer/trace/bulk_create/",
            {
                "traces": [
                    {"project": str(own.id), "project_id": str(foreign.id), "name": "t"}
                ]
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert not Trace.no_workspace_objects.filter(project=foreign).exists()
        assert not Trace.no_workspace_objects.filter(project=own, name="t").exists()

    def test_trace_bulk_create_control(self, tenants):
        a, _ = tenants
        own = _project(a)
        clear_workspace_context()
        response = a.client.post(
            "/tracer/trace/bulk_create/",
            {"traces": [{"project": str(own.id), "name": "t-ok"}]},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK, response.content
        assert Trace.no_workspace_objects.filter(project=own, name="t-ok").exists()

    def test_span_bulk_create_refuses_raw_relation_ids(self, tenants):
        a, b = tenants
        own = _project(a)
        clear_workspace_context()
        trace = Trace.no_workspace_objects.create(project=own, name="own trace")
        foreign_config = _eval_config(_project(b))
        clear_workspace_context()
        response = a.client.post(
            "/tracer/observation-span/bulk_create/",
            {
                "observation_spans": [
                    {
                        "project": str(own.id),
                        "trace": str(trace.id),
                        "name": "s",
                        "observation_type": "llm",
                        "custom_eval_config_id": str(foreign_config.id),
                    }
                ]
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        from tracer.models.observation_span import ObservationSpan

        assert not ObservationSpan.no_workspace_objects.filter(trace=trace).exists()

    def test_edit_eval_task_foreign_config_answers_like_unknown(self, tenants):
        from tracer.serializers.eval_task import EditEvalTaskSerializer

        a, b = tenants
        foreign = _eval_config(_project(b))
        unknown_id = uuid.uuid4()
        messages = []
        for value in (foreign.id, unknown_id):
            serializer = EditEvalTaskSerializer(context={"request": _request(a)})
            with pytest.raises(serializers.ValidationError) as raised:
                serializer.validate_evals([value])
            messages.append(str(raised.value.detail).replace(str(value), "<id>"))
        assert messages[0] == messages[1]
        own = _eval_config(_project(a))
        serializer = EditEvalTaskSerializer(context={"request": _request(a)})
        assert serializer.validate_evals([own.id]) == [own.id]

    def test_code_example_and_saved_views_foreign_project_answer_like_unknown(
        self, tenants
    ):
        a, b = tenants
        foreign = _project(b)
        unknown_id = uuid.uuid4()
        for url in ("/tracer/users/get_code_example/", "/tracer/saved-views/"):
            clear_workspace_context()
            got_foreign = a.client.get(url, {"project_id": str(foreign.id)})
            clear_workspace_context()
            got_unknown = a.client.get(url, {"project_id": str(unknown_id)})
            assert got_foreign.status_code == status.HTTP_404_NOT_FOUND
            assert same_answer(got_foreign, foreign.id, got_unknown, unknown_id)
        clear_workspace_context()
        own = a.client.get(
            "/tracer/users/get_code_example/", {"project_id": str(_project(a).id)}
        )
        assert own.status_code == status.HTTP_200_OK


class TestGraphSocketFilters:
    """frame filter values reach SQL only as parameters."""

    QUOTED_VALUE = "quoted'value"

    def _evaluation_query(self, filters):
        from tracer.socket import GraphDataConsumer

        consumer = GraphDataConsumer()
        consumer.project_id = str(uuid.uuid4())
        consumer.filters = filters
        consumer.interval, consumer.graph = "hour", "charts"
        consumer.eval_id, consumer.property = "", ""
        consumer.fetch_raw_data = AsyncMock(return_value=[])
        consumer.send_json = AsyncMock()
        asyncio.run(consumer.send_evaluation_data())
        consumer.fetch_raw_data.assert_awaited_once()
        return consumer.fetch_raw_data.await_args.args

    def test_system_metric_values_are_parameters(self):
        filters = [
            {
                "column_id": "avg_latency",
                "filter_config": {
                    "filter_type": "number",
                    "filter_op": "greater_than",
                    "filter_value": self.QUOTED_VALUE,
                },
            },
            {
                "column_id": "run_name",
                "filter_config": {
                    "filter_type": "text",
                    "filter_op": "contains",
                    "filter_value": self.QUOTED_VALUE,
                },
            },
            {
                "column_id": "bogus_column",
                "filter_config": {
                    "filter_type": "text",
                    "filter_op": "equals",
                    "filter_value": "x",
                },
            },
            {
                "column_id": "run_name",
                "filter_config": {
                    "filter_type": "text",
                    "filter_op": "drop",
                    "filter_value": "x",
                },
            },
        ]
        query, params = self._evaluation_query(filters)
        assert self.QUOTED_VALUE not in query
        assert query.count("%s") == len(params)
        assert params[5:] == [self.QUOTED_VALUE, f"%{self.QUOTED_VALUE}%"]

    def test_eval_metric_values_are_parameters(self):
        metric = str(uuid.uuid4())
        filters = [
            {
                "column_id": metric,
                "filter_config": {
                    "filter_type": "number",
                    "filter_op": "greater_than",
                    "filter_value": "0.5",
                },
            },
            {
                "column_id": metric,
                "filter_config": {
                    "filter_type": "number",
                    "filter_op": "less_than",
                    "filter_value": self.QUOTED_VALUE,
                },
            },
            {
                "column_id": "not-a-uuid'--",
                "filter_config": {
                    "filter_type": "number",
                    "filter_op": "equals",
                    "filter_value": 1,
                },
            },
            {
                "column_id": metric,
                "filter_config": {
                    "filter_type": "array",
                    "filter_op": "contains",
                    "filter_value": self.QUOTED_VALUE,
                },
            },
            {
                "column_id": f"{metric}_choice'x",
                "filter_config": {
                    "filter_type": "number",
                    "filter_op": "equals",
                    "filter_value": 2,
                },
            },
        ]
        query, params = self._evaluation_query(filters)
        assert self.QUOTED_VALUE not in query and "not-a-uuid" not in query
        assert "choice'x" not in query
        assert params[5:] == [0.5, f"%{self.QUOTED_VALUE}%", f"metric_{metric}", "choice'x", 2.0]
        assert query.count("%s") == len(params)


@pytest.mark.django_db(transaction=True)
class TestGraphSocketScope:
    """a workspace-restricted member, and an eval of
    another project, get no data."""

    @pytest.mark.parametrize("case", ["workspace", "organization", "evaluation", "own"])
    def test_project_in_an_inaccessible_workspace_and_foreign_eval(self, settings, case):
        from accounts.models.workspace import Workspace
        from tracer.socket import GraphDataConsumer

        settings.FUTURE_AGI_LICENSE_URL = "http://127.0.0.1:9"
        a = make_tenant("A")
        clear_workspace_context()
        hidden_workspace = Workspace.no_workspace_objects.create(
            name="A hidden",
            organization=a.organization,
            is_default=False,
            is_active=True,
            created_by=a.user,
        )
        member = User.objects.create_user(
            email=f"member-{uuid.uuid4().hex[:8]}@example.com",
            password="memberpass123",
            name="Member",
            organization=a.organization,
            organization_role="Member",
        )
        OrganizationMembership.no_workspace_objects.create(
            user=member,
            organization=a.organization,
            role="Member",
            level=Level.MEMBER,
            is_active=True,
        )
        hidden_project = Project.no_workspace_objects.create(
            name="hidden",
            organization=a.organization,
            workspace=hidden_workspace,
            trace_type="observe",
            model_type="GenerativeLLM",
        )
        own_project = _project(a)
        other_project_eval = _eval_config(_project(a, "other"))
        b = make_tenant("B")
        foreign_project = _project(b)
        cases = {
            "workspace": (member, hidden_project, "", False),
            "organization": (a.user, foreign_project, "", False),
            "evaluation": (a.user, own_project, str(other_project_eval.id), False),
            "own": (a.user, own_project, "", True),
        }
        for user, project, eval_id, allowed in (cases[case],):
            consumer = GraphDataConsumer()
            consumer.user = user
            consumer.scope = {}
            consumer._guard = SimpleNamespace(ensure_live=AsyncMock(return_value=True))
            consumer.send_json = AsyncMock()
            consumer.close = AsyncMock()

            async def data_frame():
                await consumer.send_json({"project_id": consumer.project_id, "data": []})

            consumer.send_metrics_data = AsyncMock(side_effect=data_frame)
            asyncio.run(consumer.receive_json({
                "projectId": str(project.pk), "graph": "charts", "evalIds": [eval_id],
            }))
            if allowed:
                consumer.close.assert_not_called()
                consumer.send_metrics_data.assert_awaited_once()
                consumer.send_json.assert_awaited_once()
            else:
                consumer.send_json.assert_not_called()
                consumer.send_metrics_data.assert_not_called()
                consumer.close.assert_awaited_once_with(code=4003)


class TestPromptStreamTemplate:
    """an unknown and a foreign template get the same
    frame and close code."""

    def _run(self, monkeypatch, template_lookup):
        from sockets.prompt_stream_consumer import PromptStreamConsumer

        consumer = PromptStreamConsumer()
        consumer.scope = {"type": "websocket"}
        consumer.session_uuid = "sess-1"
        consumer.workspace_id = "00000000-0000-0000-0000-000000000000"
        consumer.user = MagicMock()
        consumer.user.can_access_workspace = MagicMock(return_value=True)
        consumer.send_json = AsyncMock()
        consumer.close = AsyncMock()
        monkeypatch.setattr(
            "sockets.prompt_stream_consumer.Workspace.objects.get",
            lambda **kw: MagicMock(id="ws-a", organization_id="org-a"),
        )
        monkeypatch.setattr(
            "sockets.prompt_stream_consumer.PromptTemplate.objects.get", template_lookup
        )
        asyncio.run(
            consumer.execute_template_async(content={"version": 1}, template_id="t-1")
        )
        frames = [c.args[0].get("message") for c in consumer.send_json.await_args_list]
        return frames, consumer.close.await_args_list[-1].kwargs.get("code")

    def test_unknown_and_foreign_templates_get_the_same_answer(self, monkeypatch):
        from model_hub.models.run_prompt import PromptTemplate as Template

        def missing(**kw):
            raise Template.DoesNotExist()

        foreign = self._run(
            monkeypatch,
            lambda **kw: MagicMock(workspace_id="ws-b", organization_id="org-b"),
        )
        unknown = self._run(monkeypatch, missing)
        assert foreign == unknown


# --- accounts ----------------------------------------------------------------


class TestAccountBoundaries:
    @pytest.mark.parametrize(
        "url", ["/accounts/organizations/", "/accounts/organizations/switch/"]
    )
    def test_foreign_organization_answers_like_unknown(self, tenants, url):
        a, b = tenants
        unknown_id = uuid.uuid4()
        clear_workspace_context()
        foreign = a.client.post(
            url, {"organization_id": str(b.organization.id)}, format="json"
        )
        clear_workspace_context()
        unknown = a.client.post(
            url, {"organization_id": str(unknown_id)}, format="json"
        )
        assert same_answer(foreign, b.organization.id, unknown, unknown_id)
        clear_workspace_context()
        own = a.client.post(
            url, {"organization_id": str(a.organization.id)}, format="json"
        )
        assert own.status_code == status.HTTP_200_OK

    def test_team_delete_foreign_user_answers_like_unknown(self, tenants):
        a, b = tenants
        unknown_id = uuid.uuid4()
        clear_workspace_context()
        foreign = a.client.delete(f"/accounts/team/users/{b.user.id}/")
        clear_workspace_context()
        unknown = a.client.delete(f"/accounts/team/users/{unknown_id}/")
        assert same_answer(foreign, b.user.id, unknown, unknown_id)
        assert User.objects.filter(id=b.user.id).exists()

    def test_removed_member_with_another_organization_is_not_deleted(self, tenants):
        a, b = tenants
        user, _ = former_member(a, "removed")
        OrganizationMembership.no_workspace_objects.create(
            user=user,
            organization=b.organization,
            role="Member",
            level=Level.MEMBER,
            is_active=True,
        )
        clear_workspace_context()
        a.client.delete(
            "/accounts/delete-users/", {"user_ids": [str(user.id)]}, format="json"
        )
        unknown_id = uuid.uuid4()
        clear_workspace_context()
        removed = a.client.delete(f"/accounts/team/users/{user.id}/")
        clear_workspace_context()
        unknown = a.client.delete(f"/accounts/team/users/{unknown_id}/")
        assert same_answer(removed, user.id, unknown, unknown_id)
        assert User.objects.filter(id=user.id).exists()
        assert OrganizationMembership.no_workspace_objects.filter(
            user=user, organization=b.organization, is_active=True
        ).exists()
        clear_workspace_context()
        renamed = a.client.post(
            "/accounts/update-user/",
            {"user_id": str(user.id), "name": "Renamed"},
            format="json",
        )
        assert renamed.status_code == status.HTTP_400_BAD_REQUEST
        user.refresh_from_db()
        assert user.name != "Renamed"

    def test_turing_gate_foreign_code_template_is_gated_like_unknown(self, tenants):
        from tfc import ee_gates

        a, b = tenants
        clear_workspace_context()

        def code_template(tenant):
            return EvalTemplate.no_workspace_objects.create(
                name=f"code-{uuid.uuid4().hex[:6]}",
                organization=tenant.organization,
                owner=OwnerChoices.USER.value,
                eval_type="code",
                config={},
            )

        own, foreign = code_template(a), code_template(b)
        with patch.object(ee_gates, "_turing_denied_off_cloud", return_value=True):
            model = next(iter(ee_gates._TURING_MODELS))
            from tfc.middleware.workspace_context import workspace_context

            # The public gate resolves the caller from bound request context.
            # This call shape also exercises implementations without the optional
            # explicit organization argument.
            with workspace_context(a.workspace, a.organization, a.user):
                foreign_gate = ee_gates.turing_oss_gate_for_template(model, template_id=str(foreign.id))
                unknown_gate = ee_gates.turing_oss_gate_for_template(model, template_id=str(uuid.uuid4()))
                assert foreign_gate is not None and unknown_gate is not None
                assert foreign_gate.status_code == unknown_gate.status_code
                assert ee_gates.turing_oss_gate_for_template(model, template_id=str(own.id)) is None


class TestStreamPublishing:
    """only the system relay publishes to a stream."""

    @patch("tfc.views.socket.send_message_to_uuid")
    def test_a_member_cannot_publish_to_a_stream(self, send, tenants):
        a, _ = tenants
        clear_workspace_context()
        response = a.client.post(
            "/call-websocket/",
            {"message": "hello", "send_to_uuid": True, "uuid": str(uuid.uuid4())},
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN
        send.assert_not_called()

    @patch("tfc.views.socket.send_message_to_uuid")
    def test_the_system_relay_still_publishes(self, send, tenants):
        from rest_framework.test import APIClient

        from accounts.models.user import OrgApiKey

        a, _ = tenants
        clear_workspace_context()
        key = OrgApiKey.no_workspace_objects.filter(
            organization=a.organization, type="system", enabled=True
        ).first() or OrgApiKey.no_workspace_objects.create(
            organization=a.organization, workspace=a.workspace, type="system"
        )
        client = APIClient()
        stream = str(uuid.uuid4())
        response = client.post(
            "/call-websocket/",
            {"message": "hello", "send_to_uuid": True, "uuid": stream},
            format="json",
            HTTP_X_API_KEY=key.api_key,
            HTTP_X_SECRET_KEY=key.secret_key,
        )
        assert response.status_code == status.HTTP_200_OK, response.content
        send.assert_called_once()
        assert str(send.call_args.args[2]) == str(a.organization.id)


# --- AI tools and Falcon -----------------------------------------------------


def _context(tenant):
    return ToolContext(
        user=tenant.user, organization=tenant.organization, workspace=tenant.workspace
    )


class TestToolBoundaries:
    def test_search_lists_only_the_callers_experiments_and_traces(self, tenants):
        from ai_tools.tools.context.search import SearchInput, SearchTool

        a, b = tenants
        token = uuid.uuid4().hex[:8]
        clear_workspace_context()
        for tenant in (a, b):
            dataset = Dataset.no_workspace_objects.create(
                name=f"search-{token}-{tenant.organization.name}",
                organization=tenant.organization,
                workspace=tenant.workspace,
                source=DatasetSourceChoices.BUILD.value,
            )
            ExperimentsTable.no_workspace_objects.create(
                name=f"exp-{token}-{tenant.organization.name}",
                dataset=dataset,
                user=tenant.user,
            )
            Trace.no_workspace_objects.create(
                project=_project(tenant),
                name=f"trace-{token}-{tenant.organization.name}",
            )
        clear_workspace_context()
        result = SearchTool().execute(
            SearchInput(
                query=token, entity_types=["experiments", "traces", "datasets"]
            ),
            _context(a),
        )
        names = [
            item["name"] for items in result.data["results"].values() for item in items
        ]
        assert names and all(a.organization.name in name for name in names)
        assert not any(b.organization.name in name for name in names)

    def test_kb_search_refuses_a_foreign_kb(self, tenants):
        from ai_tools.tools.web.kb_search import KBSearchInput, KBSearchTool

        a, b = tenants
        clear_workspace_context()
        foreign = KnowledgeBaseFile.no_workspace_objects.create(
            name="b kb", organization=b.organization, workspace=b.workspace
        )
        with patch(
            "agentic_eval.core.embeddings.embedding_manager.EmbeddingManager"
        ) as manager:
            result = KBSearchTool().execute(
                KBSearchInput(query="q", kb_id=str(foreign.id)), _context(a)
            )
        assert result.is_error and result.error_code == "NOT_FOUND"
        manager.return_value.retrieve_rag_based_examples.assert_not_called()

    @pytest.mark.parametrize("reference", ["source_id", "kb_id"])
    @pytest.mark.parametrize("other_scope", ["workspace", "organization"])
    def test_evaluate_with_agent_scope_checks(self, tenants, reference, other_scope):
        from ai_tools.tools.evaluations.evaluate_with_agent import (
            EvaluateWithAgentInput, EvaluateWithAgentTool,
        )

        a, b = tenants
        clear_workspace_context()
        if other_scope == "workspace":
            from accounts.models.workspace import Workspace

            b = SimpleNamespace(organization=a.organization,
                workspace=Workspace.no_workspace_objects.create(
                    organization=a.organization, name="Other", created_by=a.user,
                    is_default=False, is_active=True,
                ))
        rows, kbs = [], []
        for tenant in (a, b):
            dataset = Dataset.no_workspace_objects.create(
                name=f"eval-agent-{tenant.organization.name}",
                organization=tenant.organization,
                workspace=tenant.workspace,
                source=DatasetSourceChoices.BUILD.value,
            )
            rows.append(Row.no_workspace_objects.create(dataset=dataset, order=0))
            kbs.append(KnowledgeBaseFile.no_workspace_objects.create(
                name="Reference", organization=tenant.organization, workspace=tenant.workspace,
            ))
        with (
            patch("tfc.ee_gating.check_ee_feature"),
            patch("ee.agenthub.eval_orchestrator.EvalScout") as scout,
            patch("ee.agenthub.eval_orchestrator.EvalOrchestrator") as orchestrator,
        ):
            scout.return_value.run.return_value = {}
            orchestrator.return_value.run.return_value = {"result": {"result": 1, "confidence": 1}}
            params = dict(source_id=str(rows[0].pk), input_scope="dataset_row",
                          criteria="Check response", kb_id=str(kbs[0].pk))
            control = EvaluateWithAgentTool().execute(EvaluateWithAgentInput(**params), _context(a))
            assert not control.is_error
            orchestrator.assert_called_once()
            orchestrator.reset_mock()
            scout.reset_mock()
            params[reference] = str(rows[1].pk if reference == "source_id" else kbs[1].pk)
            refused = EvaluateWithAgentTool().execute(EvaluateWithAgentInput(**params), _context(a))
            assert refused.is_error and refused.error_code == "NOT_FOUND", refused
            orchestrator.assert_not_called()
            scout.assert_not_called()


@pytest.mark.django_db(transaction=True)
class TestFalconReconnect:
    """reconnect replays only the caller's conversation."""

    def test_another_users_conversation_is_not_replayed(self, settings):
        from ee.falcon_ai.consumers import FalconAIConsumer
        from ee.falcon_ai.models import Conversation

        settings.FUTURE_AGI_LICENSE_URL = "http://127.0.0.1:9"
        a, b = make_tenant("A"), make_tenant("B")
        clear_workspace_context()
        conversation = Conversation.no_workspace_objects.create(
            user=a.user, organization=a.organization, workspace=a.workspace
        )
        buffer = MagicMock()
        buffer.get_status.return_value = "streaming"
        buffer.get_all_events.return_value = [{"type": "text_delta", "data": "secret"}]
        from accounts.models.workspace import Workspace

        other_workspace = Workspace.no_workspace_objects.create(
            name="Other workspace",
            organization=a.organization,
            is_default=False,
            is_active=True,
            created_by=a.user,
        )
        for user, organization, workspace, replayed in (
            (b.user, b.organization, b.workspace, False),
            (a.user, a.organization, other_workspace, False),
            (a.user, a.organization, a.workspace, True),
        ):
            consumer = FalconAIConsumer()
            consumer.user, consumer.organization, consumer.workspace = (
                user,
                organization,
                workspace,
            )
            consumer.send_json = AsyncMock()
            with patch("ee.falcon_ai.consumers.StreamBuffer", return_value=buffer):
                asyncio.run(
                    consumer._handle_reconnect(
                        {"conversation_id": str(conversation.id)}
                    )
                )
            frames = [c.args[0] for c in consumer.send_json.await_args_list]
            assert ({"type": "text_delta", "data": "secret"} in frames) is replayed
            if not replayed:
                assert frames == [
                    {"type": "reconnect_status", "data": {"status": "none"}}
                ]
