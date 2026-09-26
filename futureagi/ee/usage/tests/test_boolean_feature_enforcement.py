from unittest.mock import MagicMock, patch

import pytest
from ee.usage.schemas.events import CheckResult
from tfc.ee_gating import FeatureUnavailable


@pytest.fixture(autouse=True)
def _bypass_validated_request_serializer():
    # ``@validated_request`` captures its ``request_serializer`` at import
    # time, so view-module-level patches don't reach it. In these unit
    # tests we mock request.data as a plain dict and want the view body
    # (which houses the entitlement gate under test) to run — replace the
    # decorator's serializer step with a pass-through so validated_data is
    # just the raw dict and the gate becomes reachable.
    def _passthrough(serializer_class, data, **_):
        serializer = MagicMock()
        serializer.validated_data = data if isinstance(data, dict) else {}
        return serializer, {}, True

    with patch("tfc.utils.api_contracts._validate_serializer", _passthrough):
        yield


class TestBooleanFeatureEnforcement:
    """Verify check_feature() is called at each enforcement point."""

    @patch("ee.usage.services.entitlements.Entitlements.check_feature")
    def test_agreement_metrics_blocked_when_not_allowed(self, mock_check):
        mock_check.return_value = CheckResult(
            allowed=False,
            reason="Agreement metrics requires Boost plan",
            error_code="ENTITLEMENT_DENIED",
        )

        from model_hub.views.annotation_queues import AnnotationQueueViewSet

        view = AnnotationQueueViewSet()
        view._gm = MagicMock()
        view._gm.forbidden_response.return_value = MagicMock(status_code=403)
        view.get_object = MagicMock()
        view.kwargs = {}
        view.format_kwarg = None

        request = MagicMock()
        request.user.organization.id = "org-1"
        request.organization = request.user.organization

        response = view.agreement(request, pk="queue-1")
        assert response.status_code == 403
        mock_check.assert_called_once_with("org-1", "has_agreement_metrics")

    @patch("ee.usage.services.entitlements.Entitlements.check_feature")
    def test_agreement_metrics_allowed(self, mock_check):
        mock_check.return_value = CheckResult(allowed=True)

        from model_hub.views.annotation_queues import AnnotationQueueViewSet

        view = AnnotationQueueViewSet()
        view._gm = MagicMock()
        view._gm.success_response.return_value = MagicMock(status_code=200)
        mock_queue = MagicMock()
        view.get_object = MagicMock(return_value=mock_queue)
        view.kwargs = {}
        view.format_kwarg = None

        request = MagicMock()
        request.user.organization.id = "org-1"
        request.organization = request.user.organization

        with patch(
            "model_hub.views.annotation_queues.calculate_agreement"
        ) as mock_calc:
            mock_calc.return_value = {"score": 0.85}
            response = view.agreement(request, pk="queue-1")
            assert response.status_code == 200

    @patch("ee.usage.deployment.DeploymentMode.is_cloud", return_value=True)
    @patch("tfc.ee_gates.voice_sim_oss_gate_response", return_value=None)
    @patch("ee.usage.services.entitlements.Entitlements.check_feature")
    def test_voice_sim_blocked_when_not_allowed(
        self, mock_check, _mock_oss_gate, _mock_is_cloud
    ):
        # voice_sim is NOT oss_locked: off-cloud it ships open, so the only
        # block is the cloud per-org entitlement check. Force the cloud path
        # (code present via oss_gate->None, is_cloud True) and deny the plan
        # feature, then assert the gate consulted the right entitlement key —
        # the previous mock target (tfc.ee_gating.check_ee_feature) is not on
        # the voice path at all, so it proved nothing.
        mock_check.return_value = CheckResult(
            allowed=False,
            reason="Voice simulation requires PAYG plan",
            error_code="ENTITLEMENT_DENIED",
        )

        from simulate.models.agent_definition import AgentDefinition
        from simulate.views.run_test import CreateRunTestView

        view = CreateRunTestView()
        view.gm = MagicMock()
        view.gm.forbidden_response.return_value = MagicMock(status_code=403)
        view.gm.bad_request.return_value = MagicMock(status_code=400)

        request = MagicMock()
        request.user.organization.id = "org-1"
        request.organization = request.user.organization
        request.data = {"agent_definition_id": "test-agent"}

        voice_agent = MagicMock(agent_type=AgentDefinition.AgentTypeChoices.VOICE)

        with (
            patch("simulate.views.run_test.CreateRunTestSerializer") as mock_ser,
            patch("simulate.views.run_test.AgentDefinition.objects") as mock_agent_qs,
        ):
            mock_ser.return_value.is_valid.return_value = True
            mock_ser.return_value.validated_data = {"agent_definition_id": "test-agent"}
            mock_agent_qs.get.return_value = voice_agent
            response = view.post(request)

        assert response.status_code == 403
        mock_check.assert_called_once_with("org-1", "has_voice_sim")

    @patch("ee.usage.services.entitlements.Entitlements.check_feature")
    def test_synthetic_data_blocked_when_not_allowed(self, mock_check):
        mock_check.return_value = CheckResult(
            allowed=False,
            reason="Synthetic data requires Boost plan",
            error_code="ENTITLEMENT_DENIED",
        )

        from model_hub.views.datasets.create.synthetic import CreateSyntheticDataset

        view = CreateSyntheticDataset()
        view._gm = MagicMock()
        view._gm.forbidden_response.return_value = MagicMock(status_code=403)

        request = MagicMock()
        request.user.organization.id = "org-1"
        request.organization = request.user.organization

        response = view.post(request)
        assert response.status_code == 403
        mock_check.assert_called_once_with("org-1", "has_synthetic_data")

    @patch("tfc.ee_gating.check_ee_feature")
    def test_optimization_blocked_when_not_allowed(self, mock_check):
        mock_check.side_effect = FeatureUnavailable(
            "optimization",
            detail="Optimization requires Scale plan",
        )

        from model_hub.views.dataset_optimization import DatasetOptimizationViewSet

        view = DatasetOptimizationViewSet()

        request = MagicMock()
        request.user.organization.id = "org-1"
        request.organization = request.user.organization

        with patch("rest_framework.views.APIView.initial", return_value=None):
            with pytest.raises(FeatureUnavailable, match="Optimization"):
                view.initial(request)

        feature_arg = mock_check.call_args.args[0]
        assert getattr(feature_arg, "value", feature_arg) == "optimization"
        assert mock_check.call_args.kwargs["org_id"] == "org-1"

    @patch("tfc.ee_gating.check_ee_feature")
    def test_custom_roles_blocked_when_not_allowed(self, mock_check):
        mock_check.side_effect = FeatureUnavailable(
            "custom_roles", detail="Custom roles requires Scale plan"
        )

        from accounts.views.rbac_views import MemberRoleUpdateAPIView

        view = MemberRoleUpdateAPIView()

        request = MagicMock()
        request.data = {"user_id": "u-1", "org_level_role": "admin"}

        mock_org = MagicMock()
        mock_org.id = "org-1"

        with patch("accounts.views.rbac_views.resolve_org", return_value=mock_org):
            with patch(
                "accounts.views.rbac_views.MemberRoleUpdateSerializer"
            ) as mock_ser:
                mock_ser.return_value.is_valid.return_value = True
                mock_ser.return_value.validated_data = {
                    "user_id": "u-1",
                    "org_level_role": "admin",
                }

                gm = MagicMock()
                gm.forbidden_response.return_value = MagicMock(status_code=403)

                with patch("accounts.views.rbac_views.GeneralMethods", return_value=gm):
                    with pytest.raises(FeatureUnavailable, match="Custom roles"):
                        view.post(request)

        # Lock down that the view gated the *right* feature for the right org
        # — the mock's side_effect raises regardless of args, so without this
        # the test would pass even if the view checked the wrong capability.
        feature_arg = mock_check.call_args.args[0]
        assert getattr(feature_arg, "value", feature_arg) == "custom_roles"
        assert mock_check.call_args.kwargs["org_id"] == "org-1"

    def test_review_workflow_is_oss_baseline_on_queue_create(self):
        """review_workflow is an OSS-baseline feature: queue creation with
        requires_review must never raise the license gate. (Downstream view
        internals need a full request; here we only assert the gate itself
        does not block.)"""
        from model_hub.views.annotation_queues import AnnotationQueueViewSet

        view = AnnotationQueueViewSet()
        view._gm = MagicMock()
        view.get_serializer = MagicMock(side_effect=RuntimeError("stop after the gate"))

        request = MagicMock()
        request.user.organization.id = "org-1"
        request.organization = request.user.organization
        request.data = {"name": "Q1", "requires_review": True}

        # The gate runs before get_serializer; a FeatureUnavailable here would
        # mean review_workflow got wrongly gated. Any other error is fine.
        with pytest.raises(Exception) as exc:
            view.create(request)
        assert not isinstance(exc.value, FeatureUnavailable)

    @patch("tfc.ee_gating.check_ee_feature")
    def test_required_labels_blocked_on_add_label(self, mock_check):
        mock_check.side_effect = FeatureUnavailable(
            "required_labels", detail="Required labels requires Boost plan"
        )

        from model_hub.views.annotation_queues import AnnotationQueueViewSet

        view = AnnotationQueueViewSet()
        view._gm = MagicMock()
        view._gm.forbidden_response.return_value = MagicMock(status_code=403)
        view.get_object = MagicMock(return_value=MagicMock())

        request = MagicMock()
        request.user.organization.id = "org-1"
        request.organization = request.user.organization
        request.data = {"label_id": "lbl-1", "required": True}

        with pytest.raises(FeatureUnavailable, match="Required labels"):
            view.add_label(request, pk="q-1")

        feature_arg = mock_check.call_args.args[0]
        assert getattr(feature_arg, "value", feature_arg) == "required_labels"
        assert mock_check.call_args.kwargs["org_id"] == "org-1"

    @pytest.mark.django_db
    @patch("tfc.ee_gating.check_ee_feature")
    def test_required_labels_blocked_on_annotations_create(self, mock_check):
        mock_check.side_effect = FeatureUnavailable(
            "required_labels", detail="Required labels requires Boost plan"
        )

        from model_hub.views.develop_annotations import AnnotationsViewSet

        view = AnnotationsViewSet()
        view._gm = MagicMock()
        view._gm.forbidden_response.return_value = MagicMock(status_code=403)

        request = MagicMock()
        request.user.organization.id = "org-1"
        request.organization = request.user.organization
        request.data = {
            "labels": [{"id": "lbl-1", "required": True}],
            "dataset": "ds-1",
            "name": "ann",
        }

        with pytest.raises(FeatureUnavailable, match="Required labels"):
            view.create(request)

        feature_arg = mock_check.call_args.args[0]
        assert getattr(feature_arg, "value", feature_arg) == "required_labels"
        assert mock_check.call_args.kwargs["org_id"] == "org-1"

    @patch("ee.usage.services.entitlements.Entitlements.check_feature")
    def test_annotation_summary_blocked_when_not_allowed(self, mock_check):
        mock_check.return_value = CheckResult(
            allowed=False,
            reason="Agreement metrics requires Boost plan",
            error_code="ENTITLEMENT_DENIED",
        )

        from model_hub.views.develop_annotations import AnnotationSummaryView

        view = AnnotationSummaryView()
        view._gm = MagicMock()
        view._gm.forbidden_response.return_value = MagicMock(status_code=403)

        request = MagicMock()
        request.user.organization.id = "org-1"
        request.organization = request.user.organization

        response = view.get(request, dataset_id="ds-1")
        assert response.status_code == 403
        mock_check.assert_called_once_with("org-1", "has_agreement_metrics")

    @patch("tfc.ee_gating.check_ee_feature")
    def test_data_masking_blocked_when_not_allowed(self, mock_check):
        mock_check.side_effect = FeatureUnavailable(
            "data_masking", detail="Data masking requires Enterprise plan"
        )

        from agentcc.views.org_config import AgentccOrgConfigViewSet

        view = AgentccOrgConfigViewSet()
        view._gm = MagicMock()
        view._gm.forbidden_response.return_value = MagicMock(status_code=403)
        view._gm.bad_request.return_value = MagicMock(status_code=400)

        request = MagicMock()
        request.user.organization.id = "org-1"
        request.organization = request.user.organization
        request.data = {"privacy": {"masking": {"enabled": True}}}

        with patch(
            "agentcc.views.org_config.AgentccOrgConfigWriteSerializer"
        ) as mock_ser:
            mock_ser.return_value.is_valid.return_value = True
            mock_ser.return_value.validated_data = {
                "privacy": {"masking": {"enabled": True}}
            }
            with pytest.raises(FeatureUnavailable, match="Data masking"):
                view.create(request)

        feature_arg = mock_check.call_args.args[0]
        assert getattr(feature_arg, "value", feature_arg) == "data_masking"
        assert mock_check.call_args.kwargs["org_id"] == "org-1"

    @patch("tfc.ee_gating.check_ee_can_create")
    def test_gateway_webhooks_blocked_when_limit_reached(self, mock_can_create):
        mock_can_create.side_effect = FeatureUnavailable(
            "gateway_webhooks", detail="You've reached webhook limit"
        )

        from agentcc.views.webhook_outbound import AgentccWebhookViewSet

        view = AgentccWebhookViewSet()
        view._gm = MagicMock()
        view._gm.forbidden_response.return_value = MagicMock(status_code=403)
        view._gm.bad_request.return_value = MagicMock(status_code=400)

        request = MagicMock()
        request.user.organization.id = "11111111-1111-1111-1111-111111111111"
        request.organization = request.user.organization
        request.data = {"name": "hook", "url": "https://example.com"}

        with patch(
            "agentcc.views.webhook_outbound.AgentccWebhook.no_workspace_objects.filter"
        ) as mock_filter:
            mock_filter.return_value.count.return_value = 3
            with pytest.raises(FeatureUnavailable, match="webhook limit"):
                view.create(request)

        mock_can_create.assert_called_once()
        resource_arg = mock_can_create.call_args.args[0]
        assert getattr(resource_arg, "value", resource_arg) == "gateway_webhooks"
        assert (
            mock_can_create.call_args.kwargs["org_id"]
            == "11111111-1111-1111-1111-111111111111"
        )
        assert mock_can_create.call_args.kwargs["current_count"] == 3

    @patch("tfc.ee_gating.check_ee_feature")
    def test_workspace_custom_roles_blocked_when_not_allowed(self, mock_check):
        mock_check.side_effect = FeatureUnavailable(
            "custom_roles", detail="Custom roles requires Scale plan"
        )

        from accounts.views.rbac_views import WorkspaceMemberRoleUpdateAPIView

        view = WorkspaceMemberRoleUpdateAPIView()
        request = MagicMock()
        request.data = {"user_id": "u-1", "ws_level": 2}

        mock_org = MagicMock()
        mock_org.id = "org-1"

        gm = MagicMock()
        gm.forbidden_response.return_value = MagicMock(status_code=403)

        with patch("accounts.views.rbac_views.resolve_org", return_value=mock_org):
            with patch("accounts.views.rbac_views.GeneralMethods", return_value=gm):
                with pytest.raises(FeatureUnavailable, match="Custom roles"):
                    view.post(request, workspace_id="ws-1")

    @patch("tfc.ee_gating.check_ee_feature")
    def test_create_scenario_blocked_when_not_allowed(self, mock_check):
        mock_check.side_effect = FeatureUnavailable(
            "synthetic_data", detail="Agentic eval requires Boost plan"
        )

        from simulate.views.scenarios import CreateScenarioView

        view = CreateScenarioView()
        view.gm = MagicMock()
        view.gm.forbidden_response.return_value = MagicMock(status_code=403)

        request = MagicMock()
        request.user.organization.id = "org-1"
        request.organization = request.user.organization
        request.data = {"kind": "dataset"}

        with pytest.raises(FeatureUnavailable, match="Agentic eval"):
            view.post(request)

        feature_arg = mock_check.call_args.args[0]
        assert getattr(feature_arg, "value", feature_arg) == "synthetic_data"
        assert mock_check.call_args.kwargs["org_id"] == "org-1"

    @patch("tfc.ee_gating.check_ee_feature")
    def test_add_scenario_rows_blocked_when_not_allowed(self, mock_check):
        mock_check.side_effect = FeatureUnavailable(
            "agentic_eval", detail="Agentic eval requires Boost plan"
        )

        from simulate.views.scenarios import AddScenarioRowsView

        view = AddScenarioRowsView()

        request = MagicMock()
        request.user.organization.id = "org-1"
        request.organization = request.user.organization

        with pytest.raises(FeatureUnavailable, match="Agentic eval"):
            view.post(request, scenario_id="scn-1")

        feature_arg = mock_check.call_args.args[0]
        assert getattr(feature_arg, "value", feature_arg) == "agentic_eval"
        assert mock_check.call_args.kwargs["org_id"] == "org-1"

    @patch("ee.usage.services.entitlements.Entitlements.check_feature")
    def test_add_scenario_columns_blocked_when_not_allowed(self, mock_check):
        mock_check.return_value = CheckResult(
            allowed=False,
            reason="Agentic eval requires Boost plan",
            error_code="ENTITLEMENT_DENIED",
        )

        from simulate.views.scenarios import AddScenarioColumnsView

        view = AddScenarioColumnsView()
        view.gm = MagicMock()
        view.gm.forbidden_response.return_value = MagicMock(status_code=403)

        request = MagicMock()
        request.user.organization.id = "org-1"
        request.organization = request.user.organization

        # @validated_request's serializer_context does a real
        # Scenarios.objects.filter(id=scenario_id, organization=...) that
        # coerces args to UUIDs before our fixture bypass runs; mock the
        # queryset out so the entitlement gate is reachable.
        scenario_id = "11111111-1111-1111-1111-111111111111"
        with patch("simulate.views.scenarios.Scenarios.objects") as mock_qs:
            mock_qs.filter.return_value.select_related.return_value.first.return_value = (
                None
            )
            response = view.post(request, scenario_id=scenario_id)
        assert response.status_code == 403
        mock_check.assert_called_once_with("org-1", "has_agentic_eval")

    @patch("ee.usage.services.entitlements.Entitlements.check_feature")
    def test_kb_patch_is_oss_baseline(self, mock_check):

        from model_hub.views.develop_dataset import CreateKnowledgeBaseView

        view = CreateKnowledgeBaseView()
        view._gm = MagicMock()
        view._gm.forbidden_response.return_value = MagicMock(status_code=403)

        request = MagicMock()
        request.user.organization.id = "org-1"
        request.organization = request.user.organization
        request.user.name = "tester"
        request.data = {"kb_id": "kb-1"}
        request.FILES.getlist.return_value = []

        with patch(
            "model_hub.views.develop_dataset.KnowledgeBaseFile.objects.filter"
        ) as mock_filter:
            mock_filter.return_value.first.return_value = MagicMock()
            response = view.patch(request)
            assert response.status_code != 403
            mock_check.assert_not_called()


class TestOSSCompatibility:
    def test_agreement_metrics_works_without_entitlements(self):
        from model_hub.views.annotation_queues import AnnotationQueueViewSet

        view = AnnotationQueueViewSet()
        view._gm = MagicMock()
        view._gm.success_response.return_value = MagicMock(status_code=200)
        mock_queue = MagicMock()
        view.get_object = MagicMock(return_value=mock_queue)
        view.kwargs = {}
        view.format_kwarg = None

        request = MagicMock()
        request.user.organization.id = "org-1"
        request.organization = request.user.organization

        with patch.dict("sys.modules", {"ee.usage.services.entitlements": None}):
            with patch(
                "model_hub.views.annotation_queues.calculate_agreement"
            ) as mock_calc:
                mock_calc.return_value = {"score": 0.9}
                response = view.agreement(request, pk="q-1")
                assert response.status_code == 200
