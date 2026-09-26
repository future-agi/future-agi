"""Tests for pre-check failures writing ERROR cells (OSS agent / usage limits)."""

from unittest.mock import patch

import pytest


@pytest.mark.django_db
class TestMarkCellsUsageLimitErrorCreatesMissingCells:
    """Pre-check failures must leave ERROR cells, not empty columns."""

    def _seed(self, organization, workspace):
        from model_hub.models.choices import (
            CellStatus,
            DatasetSourceChoices,
            DataTypeChoices,
            OwnerChoices,
            SourceChoices,
            StatusType,
        )
        from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
        from model_hub.models.evals_metric import EvalTemplate, UserEvalMetric

        dataset = Dataset.objects.create(
            name="Limit Error Dataset",
            organization=organization,
            workspace=workspace,
            source=DatasetSourceChoices.BUILD.value,
        )
        template = EvalTemplate.objects.create(
            name="agent_template",
            organization=organization,
            workspace=workspace,
            owner=OwnerChoices.USER.value,
            eval_type="agent",
            config={"eval_type_id": "AgentEvaluator"},
        )
        uem = UserEvalMetric.objects.create(
            name="agent_eval_col",
            dataset=dataset,
            template=template,
            organization=organization,
            workspace=workspace,
            status=StatusType.FAILED.value,
            config={"mapping": {"input": "in"}},
        )
        eval_col = Column.objects.create(
            name=uem.name,
            dataset=dataset,
            data_type=DataTypeChoices.BOOLEAN.value,
            source=SourceChoices.EVALUATION.value,
            source_id=str(uem.id),
        )
        reason_col = Column.objects.create(
            name=f"{uem.name}-reason",
            dataset=dataset,
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.EVALUATION_REASON.value,
            source_id=f"{eval_col.id}-sourceid-{uem.id}",
        )
        for i in range(2):
            Row.objects.create(dataset=dataset, order=i)
        return uem, eval_col, reason_col, Cell, CellStatus

    def test_creates_error_cells_when_none_exist(self, organization, workspace):
        from types import SimpleNamespace

        from model_hub.tasks.user_evaluation import _mark_cells_usage_limit_error

        uem, eval_col, reason_col, Cell, CellStatus = self._seed(
            organization, workspace
        )
        assert Cell.objects.filter(column_id=eval_col.id).count() == 0

        _mark_cells_usage_limit_error(
            uem,
            SimpleNamespace(
                error_code="ENTITLEMENT_DENIED",
                reason=(
                    "Agent evaluations are not available on your plan. "
                    "Use LLM-as-a-Judge or Code evaluations instead."
                ),
                dimension="",
                current_usage=0,
                limit=0,
                upgrade_cta=None,
            ),
        )

        eval_cells = list(Cell.objects.filter(column_id=eval_col.id, deleted=False))
        reason_cells = list(Cell.objects.filter(column_id=reason_col.id, deleted=False))
        assert len(eval_cells) == 2
        assert len(reason_cells) == 2
        for cell in eval_cells + reason_cells:
            assert cell.status == CellStatus.ERROR.value
            assert "Agent evaluations are not available" in (cell.value or "")
            infos = cell.value_infos or {}
            assert infos.get("error_code") == "ENTITLEMENT_DENIED"

    def test_oss_agent_block_writes_error_cells(self, organization, workspace):
        # Agent evals run self-hosted on user keys — deployment mode alone
        # must not deny (see process_single_evaluation). Simulate a
        # capability denial instead to cover the full block-and-mark flow.
        from types import SimpleNamespace

        from model_hub.models.choices import StatusType
        from model_hub.tasks.user_evaluation import process_single_evaluation

        uem, eval_col, _reason_col, Cell, CellStatus = self._seed(
            organization, workspace
        )
        uem.status = StatusType.NOT_STARTED.value
        uem.save(update_fields=["status"])

        with (
            patch("model_hub.tasks.user_evaluation.evaluation_tracker") as tracker,
            patch("model_hub.tasks.user_evaluation.track_mixpanel_event"),
            patch(
                "model_hub.tasks.user_evaluation.get_mixpanel_properties",
                return_value={},
            ),
            patch("tfc.capabilities.service.is_configured", return_value=True),
            patch(
                "tfc.capabilities.service.check",
                return_value=SimpleNamespace(allowed=False),
            ),
        ):
            tracker.is_running.return_value = False
            tracker.instance_id = "test"
            with pytest.raises(ValueError, match="Agent evaluations are not available"):
                process_single_evaluation(uem)

        uem.refresh_from_db()
        assert uem.status == StatusType.FAILED.value
        eval_cells = list(Cell.objects.filter(column_id=eval_col.id, deleted=False))
        assert len(eval_cells) == 2
        assert all(c.status == CellStatus.ERROR.value for c in eval_cells)
        assert all(
            (c.value_infos or {}).get("error_code") == "ENTITLEMENT_DENIED"
            for c in eval_cells
        )

    def test_completed_cells_are_not_overwritten(self, organization, workspace):
        from types import SimpleNamespace

        from model_hub.models.develop_dataset import Row
        from model_hub.tasks.user_evaluation import _mark_cells_usage_limit_error

        uem, eval_col, _reason_col, Cell, CellStatus = self._seed(
            organization, workspace
        )
        rows = list(Row.objects.filter(dataset=uem.dataset))
        for row in rows:
            Cell.objects.create(
                row=row,
                column=eval_col,
                dataset=uem.dataset,
                status=CellStatus.PASS.value,
                value="real result",
            )

        _mark_cells_usage_limit_error(
            uem,
            SimpleNamespace(
                error_code="ENTITLEMENT_DENIED",
                reason="Upgrade plan to unlock",
                dimension="",
                current_usage=0,
                limit=0,
                upgrade_cta=None,
            ),
        )

        for cell in Cell.objects.filter(column_id=eval_col.id, deleted=False):
            assert cell.status == CellStatus.PASS.value
            assert cell.value == "real result"
