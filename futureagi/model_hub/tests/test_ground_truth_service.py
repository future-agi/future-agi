"""Unit tests for GroundTruthService with a mocked EmbeddingManager."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from model_hub.models.evals_metric import EvalGroundTruth
from model_hub.services.ground_truth_service import (
    EmbedDatasetResult,
    GroundTruthService,
    ServiceError,
)



# ─────────────────────────────────────────────────────────────────────
# Stand-in for the Django models so we don't need a DB fixture.
# ─────────────────────────────────────────────────────────────────────


class _NoopCM:
    def __enter__(self):
        return None

    def __exit__(self, exc_type, exc, tb):
        return False


@dataclass
class _FakeOrg:
    id: str = "org-fake"


@dataclass
class _FakeWorkspace:
    id: str = "ws-fake"


@dataclass
class _FakeTemplate:
    id: str = "tpl-fake"
    output_type_normalized: str | None = None
    choice_scores: dict[str, float] | None = None
    pass_threshold: float | None = None
    owner: str = "user"
    config: dict[str, Any] = field(default_factory=dict)

    def save(self, update_fields=None):
        pass


@dataclass
class _FakeGT:
    """Honours every attribute the service touches and records save calls."""

    id: str = "gt-fake"
    eval_template: Any = None
    columns: list[str] = field(default_factory=list)
    variable_mapping: dict[str, Any] = field(default_factory=dict)
    role_mapping: dict[str, Any] = field(default_factory=dict)
    embedding_status: str = EvalGroundTruth.EmbeddingStatus.PENDING
    embedded_row_count: int = 0
    data: list[dict[str, Any]] = field(default_factory=list)
    eval_template_id: str = "tpl-fake"
    organization: _FakeOrg = field(default_factory=_FakeOrg)
    workspace: _FakeWorkspace = field(default_factory=_FakeWorkspace)
    organization_id: str = "org-fake"
    workspace_id: str = "ws-fake"
    is_active: bool = False
    enabled: bool = True
    max_examples: int = 3
    similarity_threshold: float = 0.7
    injection_format: str = "structured"
    save_calls: list[list[str]] = field(default_factory=list)
    refresh_overrides: dict[str, Any] = field(default_factory=dict)

    def save(self, update_fields=None):
        self.save_calls.append(list(update_fields or []))

    def refresh_from_db(self, fields=None):
        for key in fields or self.refresh_overrides.keys():
            if key in self.refresh_overrides:
                setattr(self, key, self.refresh_overrides[key])


# ─────────────────────────────────────────────────────────────────────
# create_from_upload
# ─────────────────────────────────────────────────────────────────────


def test_create_from_upload_persists_rows_verbatim():
    captured = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        return type("GT", (), kwargs)()

    with patch(
        "model_hub.services.ground_truth_service.EvalGroundTruth.objects.create",
        side_effect=fake_create,
    ):
        GroundTruthService.create_from_upload(
            eval_template=_FakeTemplate(),
            name="ds",
            description="",
            file_name="ds.csv",
            columns=["q", "a"],
            data=[{"q": "hi", "a": "yo"}, {"q": "ho", "a": "yo"}],
            variable_mapping={"q": "q"},
            role_mapping={"output": "a"},
            organization=_FakeOrg(),
            workspace=_FakeWorkspace(),
        )

    stored = captured["data"]
    assert stored == [{"q": "hi", "a": "yo"}, {"q": "ho", "a": "yo"}]
    assert all("item_id" not in row for row in stored)
    assert captured["row_count"] == 2
    assert captured["embedding_status"] == EvalGroundTruth.EmbeddingStatus.PENDING


# ─────────────────────────────────────────────────────────────────────
# embed_dataset
# ─────────────────────────────────────────────────────────────────────


def test_embed_dataset_fails_when_no_rows():
    gt = _FakeGT(data=[], variable_mapping={"q": "question"})
    result = GroundTruthService.embed_dataset(gt=gt)
    assert isinstance(result, EmbedDatasetResult)
    assert result.status == EvalGroundTruth.EmbeddingStatus.FAILED
    assert "no rows" in (result.error or "").lower()
    assert gt.embedding_status == EvalGroundTruth.EmbeddingStatus.FAILED


def test_embed_dataset_fails_when_no_mapped_columns():
    gt = _FakeGT(
        data=[{"q": "hi"}],
        variable_mapping={},
    )
    result = GroundTruthService.embed_dataset(gt=gt)
    assert result.status == EvalGroundTruth.EmbeddingStatus.FAILED
    assert "mapping" in (result.error or "").lower()


def test_embed_dataset_marks_failed_when_writer_raises():
    gt = _FakeGT(
        data=[{"q": "hi"}],
        variable_mapping={"q": "q"},
    )

    with patch(
        "agentic_eval.core.embeddings.embedding_manager.EmbeddingManager.soft_delete_vectors"
    ), patch(
        "agentic_eval.core.embeddings.embedding_manager.EmbeddingManager.parallel_process_metadata",
        side_effect=RuntimeError("ch unreachable"),
    ):
        result = GroundTruthService.embed_dataset(gt=gt)

    assert result.status == EvalGroundTruth.EmbeddingStatus.FAILED
    assert "ch unreachable" in (result.error or "")
    assert gt.embedding_status == EvalGroundTruth.EmbeddingStatus.FAILED


def test_embed_dataset_marks_completed_on_success():
    gt = _FakeGT(
        data=[{"q": "hi"}, {"q": "yo"}],
        variable_mapping={"q": "q"},
    )

    def fake_process(*_args, **kwargs):
        cb = kwargs.get("progress_callback")
        if cb:
            for i in range(1, len(kwargs.get("metadatas") or []) + 1):
                cb(i)

    with patch(
        "agentic_eval.core.embeddings.embedding_manager.EmbeddingManager.soft_delete_vectors"
    ), patch(
        "agentic_eval.core.embeddings.embedding_manager.EmbeddingManager.parallel_process_metadata",
        side_effect=fake_process,
    ), patch(
        "model_hub.services.ground_truth_service.EvalGroundTruth.objects.filter"
    ):
        result = GroundTruthService.embed_dataset(gt=gt)

    assert result.status == EvalGroundTruth.EmbeddingStatus.COMPLETED
    assert result.rows_embedded == 2
    assert gt.embedded_row_count == 2
    assert gt.embedding_status == EvalGroundTruth.EmbeddingStatus.COMPLETED


def test_embed_dataset_marks_pending_when_mapping_changes_during_embed():
    gt = _FakeGT(
        data=[{"q": "hi"}, {"q": "yo"}],
        variable_mapping={"q": "q"},
    )
    gt.refresh_overrides = {"variable_mapping": {"q": "q_renamed"}}

    def fake_process(*_args, **kwargs):
        cb = kwargs.get("progress_callback")
        if cb:
            for i in range(1, len(kwargs.get("metadatas") or []) + 1):
                cb(i)

    with patch(
        "agentic_eval.core.embeddings.embedding_manager.EmbeddingManager.soft_delete_vectors"
    ), patch(
        "agentic_eval.core.embeddings.embedding_manager.EmbeddingManager.parallel_process_metadata",
        side_effect=fake_process,
    ), patch(
        "model_hub.services.ground_truth_service.EvalGroundTruth.objects.filter"
    ):
        result = GroundTruthService.embed_dataset(gt=gt)

    assert result.status == EvalGroundTruth.EmbeddingStatus.PENDING
    assert result.rows_embedded == 2
    assert gt.embedding_status == EvalGroundTruth.EmbeddingStatus.PENDING
    assert gt.embedded_row_count == 2


def test_embed_dataset_marks_failed_when_zero_rows_written():
    gt = _FakeGT(
        data=[{"q": "hi"}, {"q": "yo"}],
        variable_mapping={"q": "q"},
    )

    with patch(
        "agentic_eval.core.embeddings.embedding_manager.EmbeddingManager.soft_delete_vectors"
    ), patch(
        "agentic_eval.core.embeddings.embedding_manager.EmbeddingManager.parallel_process_metadata"
    ):
        # parallel_process_metadata is a noop here: no callback fires, so
        # no source row was persisted. embed_dataset must surface FAILED
        # rather than blindly stamping COMPLETED.
        result = GroundTruthService.embed_dataset(gt=gt)

    assert result.status == EvalGroundTruth.EmbeddingStatus.FAILED
    assert "rows were written" in (result.error or "").lower()


def test_embed_dataset_forwards_progress_callback_to_manager():
    """The service must pass a ``progress_callback`` into
    ``parallel_process_metadata`` so the embed manager can tick the live
    row count from inside its worker threads."""
    gt = _FakeGT(
        data=[{"q": "hi"}, {"q": "yo"}, {"q": "lo"}],
        variable_mapping={"q": "q"},
    )

    captured = {}

    def fake_process(*_args, **kwargs):
        captured["progress_callback"] = kwargs.get("progress_callback")

    with patch(
        "agentic_eval.core.embeddings.embedding_manager.EmbeddingManager.soft_delete_vectors"
    ), patch(
        "agentic_eval.core.embeddings.embedding_manager.EmbeddingManager.parallel_process_metadata",
        side_effect=fake_process,
    ):
        GroundTruthService.embed_dataset(gt=gt)

    assert callable(captured.get("progress_callback")), (
        "embed_dataset must forward a progress_callback to the manager"
    )


def test_embed_dataset_progress_callback_persists_row_count_via_update():
    """The callback must update ``embedded_row_count`` through Django's
    queryset update (not a stale in-memory model save) so concurrent
    worker writes do not race the activity-level snapshot."""
    gt = _FakeGT(
        data=[{"q": "hi"}, {"q": "yo"}, {"q": "lo"}],
        variable_mapping={"q": "q"},
    )

    captured = {}
    update_calls = []

    def fake_process(*_args, **kwargs):
        captured["progress_callback"] = kwargs.get("progress_callback")

    class _Queryset:
        def update(self, **values):
            update_calls.append(values)

    def fake_filter(**filter_kwargs):
        update_calls.append(("filter", filter_kwargs))
        return _Queryset()

    with patch(
        "agentic_eval.core.embeddings.embedding_manager.EmbeddingManager.soft_delete_vectors"
    ), patch(
        "agentic_eval.core.embeddings.embedding_manager.EmbeddingManager.parallel_process_metadata",
        side_effect=fake_process,
    ), patch(
        "model_hub.services.ground_truth_service.EvalGroundTruth.objects.filter",
        side_effect=fake_filter,
    ):
        GroundTruthService.embed_dataset(gt=gt)
        cb = captured["progress_callback"]
        cb(1)
        cb(2)
        cb(3)

    filter_payloads = [c for c in update_calls if isinstance(c, tuple) and c[0] == "filter"]
    update_payloads = [c for c in update_calls if isinstance(c, dict)]
    assert len(filter_payloads) == 3
    assert all(c[1]["id"] == "gt-fake" for c in filter_payloads)
    assert update_payloads == [
        {"embedded_row_count": 1},
        {"embedded_row_count": 2},
        {"embedded_row_count": 3},
    ]


# ─────────────────────────────────────────────────────────────────────
# retrieve_few_shot
# ─────────────────────────────────────────────────────────────────────


def test_retrieve_few_shot_short_circuits_when_not_completed():
    gt = _FakeGT(embedding_status=EvalGroundTruth.EmbeddingStatus.PENDING, variable_mapping={"q": "q"})
    rows, column_types = GroundTruthService.retrieve_few_shot(
        gt=gt, inputs={"q": "hi"}, max_results=3
    )
    assert rows == []
    assert column_types == {}


def test_retrieve_few_shot_short_circuits_when_mapping_missing():
    gt = _FakeGT(embedding_status=EvalGroundTruth.EmbeddingStatus.COMPLETED, variable_mapping={})
    rows, column_types = GroundTruthService.retrieve_few_shot(
        gt=gt, inputs={"q": "hi"}, max_results=3
    )
    assert rows == []
    assert column_types == {}


def test_retrieve_few_shot_builds_rows_from_ch_metadata():
    import base64

    encoded_url = base64.urlsafe_b64encode(
        b"http://example.com/cat.png"
    ).decode().rstrip("=")
    gt = _FakeGT(
        embedding_status=EvalGroundTruth.EmbeddingStatus.COMPLETED,
        variable_mapping={"q": "question", "img": "image"},
        columns=["question", "image", "verdict"],
    )

    raw_groups = [
        [
            {
                "question": "what is this",
                "image": encoded_url,
                "verdict": "Pass",
                "item_id": "abc",
                "input_type": "text",
                "column_name": "question",
                "index_column": "what is this",
                "organization_id": "org",
                "workspace_id": "ws",
            },
            {
                "question": "what is this",
                "image": encoded_url,
                "verdict": "Pass",
                "item_id": "abc",
                "input_type": "image",
                "column_name": "image",
                "index_column": "<binary>",
                "organization_id": "org",
                "workspace_id": "ws",
            },
        ],
        [
            {
                "question": "yo",
                "image": encoded_url,
                "verdict": "Fail",
                "item_id": "def",
                "input_type": "text",
                "column_name": "question",
                "organization_id": "org",
                "workspace_id": "ws",
            }
        ],
    ]

    with patch(
        "agentic_eval.core.embeddings.embedding_manager.EmbeddingManager"
    ) as mock_manager_cls:
        mock_manager = mock_manager_cls.return_value
        mock_manager.retrieve_avg_rag_based_examples.return_value = raw_groups
        mock_manager.decode_path.side_effect = lambda v: base64.urlsafe_b64decode(
            v + "=" * (-len(v) % 4)
        ).decode()
        rows, column_types = GroundTruthService.retrieve_few_shot(
            gt=gt, inputs={"q": "hi"}, max_results=5
        )

    assert rows == [
        {
            "question": "what is this",
            "image": "http://example.com/cat.png",
            "verdict": "Pass",
        },
        {
            "question": "yo",
            "image": "http://example.com/cat.png",
            "verdict": "Fail",
        },
    ]
    assert column_types == {"question": "text", "image": "image"}


def test_retrieve_few_shot_skips_empty_groups():
    gt = _FakeGT(
        embedding_status=EvalGroundTruth.EmbeddingStatus.COMPLETED,
        variable_mapping={"q": "question"},
        columns=["question", "verdict"],
    )

    raw_groups = [
        [{"question": "hi", "verdict": "Pass", "item_id": "abc"}],
        [],
        [{"question": "yo", "verdict": "Fail", "item_id": "def"}],
    ]

    with patch(
        "agentic_eval.core.embeddings.embedding_manager.EmbeddingManager"
    ) as mock_manager_cls:
        mock_manager = mock_manager_cls.return_value
        mock_manager.retrieve_avg_rag_based_examples.return_value = raw_groups
        mock_manager.decode_path.side_effect = ValueError("not encoded")
        rows, column_types = GroundTruthService.retrieve_few_shot(
            gt=gt, inputs={"q": "hi"}, max_results=5
        )

    assert rows == [
        {"question": "hi", "verdict": "Pass"},
        {"question": "yo", "verdict": "Fail"},
    ]
    assert column_types == {}


def test_resolve_preview_examples_returns_none_when_no_active_gt():
    template = _FakeTemplate()
    with patch(
        "model_hub.services.ground_truth_service.GroundTruthService.load_active_gt",
        return_value=None,
    ):
        result = GroundTruthService.resolve_preview_examples(
            eval_template=template,
            eval_inputs={"q": "hi"},
            organization_id="org-fake",
            workspace_id="ws-fake",
        )
    assert result is None


def test_resolve_preview_examples_returns_none_when_inputs_blank():
    template = _FakeTemplate()
    result = GroundTruthService.resolve_preview_examples(
        eval_template=template,
        eval_inputs={},
        organization_id="org-fake",
        workspace_id="ws-fake",
    )
    assert result is None


def test_resolve_preview_examples_returns_empty_list_when_active_but_no_matches():
    template = _FakeTemplate()
    gt = _FakeGT(variable_mapping={"q": "question"}, role_mapping={})

    with patch(
        "model_hub.services.ground_truth_service.GroundTruthService.load_active_gt",
        return_value=gt,
    ), patch(
        "model_hub.services.ground_truth_service.GroundTruthService.retrieve_few_shot",
        return_value=([], {}),
    ):
        result = GroundTruthService.resolve_preview_examples(
            eval_template=template,
            eval_inputs={"q": "hi"},
            organization_id="org-fake",
            workspace_id="ws-fake",
        )
    assert result == []


def test_resolve_preview_examples_swallows_database_error_returning_none():
    from django.db import DatabaseError

    template = _FakeTemplate()
    gt = _FakeGT(variable_mapping={"q": "input"}, role_mapping={"output": "answer"})
    with patch(
        "model_hub.services.ground_truth_service.GroundTruthService.load_active_gt",
        return_value=gt,
    ), patch(
        "model_hub.services.ground_truth_service.GroundTruthService.retrieve_few_shot",
        side_effect=DatabaseError("ch down"),
    ):
        result = GroundTruthService.resolve_preview_examples(
            eval_template=template,
            eval_inputs={"q": "hi"},
            organization_id="org-fake",
            workspace_id="ws-fake",
        )
    assert result is None


def test_resolve_preview_examples_propagates_programmer_errors():
    template = _FakeTemplate()
    with patch(
        "model_hub.services.ground_truth_service.GroundTruthService.load_active_gt",
        side_effect=RuntimeError("boom"),
    ), pytest.raises(RuntimeError, match="boom"):
        GroundTruthService.resolve_preview_examples(
            eval_template=template,
            eval_inputs={"q": "hi"},
            organization_id="org-fake",
            workspace_id="ws-fake",
        )


def test_resolve_preview_examples_enriches_rows_with_mappings():
    template = _FakeTemplate()
    retrieved_rows = [
        {"question": "What is 2+2?", "answer": "4", "notes": "trivial"},
        {"question": "Capital of France?", "answer": "Paris", "notes": "easy"},
    ]
    gt = _FakeGT(
        variable_mapping={"q": "question"},
        role_mapping={"output": "answer", "explanation": "notes"},
    )

    with patch(
        "model_hub.services.ground_truth_service.GroundTruthService.load_active_gt",
        return_value=gt,
    ), patch(
        "model_hub.services.ground_truth_service.GroundTruthService.retrieve_few_shot",
        return_value=(retrieved_rows, {"question": "text"}),
    ):
        result = GroundTruthService.resolve_preview_examples(
            eval_template=template,
            eval_inputs={"q": "hi"},
            organization_id="org-fake",
            workspace_id="ws-fake",
        )

    assert len(result) == 2
    for enriched, original in zip(result, retrieved_rows, strict=True):
        assert enriched["row"] == original
        assert enriched["variable_mapping"] == {"q": "question"}
        assert enriched["role_mapping"] == {
            "output": "answer",
            "explanation": "notes",
        }
        assert enriched["column_types"] == {"question": "text"}


def test_update_setup_writes_runtime_knobs_onto_the_row():
    template = _FakeTemplate(owner="system")
    gt = _FakeGT(columns=["q", "a"], variable_mapping={"q": "q"}, eval_template=template)

    class _NoopQS:
        def exclude(self, **_):
            return self

        def update(self, **_):
            return 0

    with patch(
        "model_hub.services.ground_truth_service.EvalGroundTruth.objects.filter",
        return_value=_NoopQS(),
    ), patch(
        "model_hub.services.ground_truth_service.transaction.atomic",
        return_value=_NoopCM(),
    ):
        result = GroundTruthService.update_setup(
            gt=gt,
            eval_template=template,
            variable_mapping={"q": "q"},
            role_mapping={"output": "a"},
            max_examples=5,
            enabled=True,
        )

    assert not isinstance(result, ServiceError)
    assert gt.is_active is True
    assert gt.enabled is True
    assert gt.max_examples == 5
    assert gt.variable_mapping == {"q": "q"}
    assert gt.role_mapping == {"output": "a"}


def test_update_setup_does_not_mutate_eval_template_config():
    """The runtime config no longer rides on the (possibly shared)
    EvalTemplate.config dict - that was the cross-tenant leak."""
    template = _FakeTemplate(owner="system", config={"existing_key": "preserved"})
    gt = _FakeGT(columns=["q", "a"], variable_mapping={"q": "q"}, eval_template=template)

    class _NoopQS:
        def exclude(self, **_):
            return self

        def update(self, **_):
            return 0

    with patch(
        "model_hub.services.ground_truth_service.EvalGroundTruth.objects.filter",
        return_value=_NoopQS(),
    ), patch(
        "model_hub.services.ground_truth_service.transaction.atomic",
        return_value=_NoopCM(),
    ):
        GroundTruthService.update_setup(
            gt=gt,
            eval_template=template,
            variable_mapping={"q": "q"},
            role_mapping={"output": "a"},
            max_examples=3,
            enabled=True,
        )

    assert "ground_truth" not in template.config
    assert template.config["existing_key"] == "preserved"


def test_update_setup_clears_sibling_active_flag():
    template = _FakeTemplate()
    gt = _FakeGT(columns=["q", "a"], variable_mapping={"q": "q"}, eval_template=template)
    sibling_updates = {}

    class _SiblingQS:
        def __init__(self):
            self._excluded_ids = []

        def exclude(self, **kw):
            self._excluded_ids.append(kw.get("id"))
            return self

        def update(self, **kw):
            sibling_updates["fields"] = kw
            sibling_updates["excluded_ids"] = list(self._excluded_ids)
            return 1

    with patch(
        "model_hub.services.ground_truth_service.EvalGroundTruth.objects.filter",
        return_value=_SiblingQS(),
    ), patch(
        "model_hub.services.ground_truth_service.transaction.atomic",
        return_value=_NoopCM(),
    ):
        GroundTruthService.update_setup(
            gt=gt,
            eval_template=template,
            variable_mapping={"q": "q"},
            role_mapping={"output": "a"},
            max_examples=3,
            enabled=True,
        )

    assert sibling_updates["fields"] == {"is_active": False}
    assert gt.id in sibling_updates["excluded_ids"]


def test_create_from_upload_succeeds_for_system_template():
    captured = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        return type("GT", (), kwargs)()

    with patch(
        "model_hub.services.ground_truth_service.EvalGroundTruth.objects.create",
        side_effect=fake_create,
    ):
        gt = GroundTruthService.create_from_upload(
            eval_template=_FakeTemplate(owner="system"),
            name="ds",
            description="",
            file_name="ds.csv",
            columns=["q", "a"],
            data=[{"q": "hi", "a": "yo"}],
            variable_mapping={"q": "q"},
            role_mapping={"output": "a"},
            organization=_FakeOrg(),
            workspace=_FakeWorkspace(),
        )

    assert not isinstance(gt, ServiceError)
    assert captured["organization"].id == "org-fake"
    assert captured["workspace"].id == "ws-fake"
    assert captured["embedding_status"] == EvalGroundTruth.EmbeddingStatus.PENDING


def test_load_active_gt_filters_by_tenant_scope():
    template = _FakeTemplate(owner="system")
    captured = {}
    order_by_calls: list = []

    class _QS:
        def order_by(self, *args, **_kwargs):
            order_by_calls.append(args)
            return self

        def first(self):
            return None

    def fake_filter(**kw):
        captured.update(kw)
        return _QS()

    with patch(
        "model_hub.services.ground_truth_service.EvalGroundTruth.objects.filter",
        side_effect=fake_filter,
    ):
        GroundTruthService.load_active_gt(
            eval_template=template,
            organization_id="org-A",
            workspace_id="ws-A",
        )

    assert captured["organization_id"] == "org-A"
    assert captured["workspace_id"] == "ws-A"
    assert captured["is_active"] is True
    assert captured["enabled"] is True
    assert captured["deleted"] is False
    assert order_by_calls == [("-created_at",)]


def test_load_active_gt_returns_none_when_organization_id_is_falsy():
    template = _FakeTemplate(owner="system")
    calls: list = []

    def fake_filter(**kw):
        calls.append(kw)
        return MagicMock()

    with patch(
        "model_hub.services.ground_truth_service.EvalGroundTruth.objects.filter",
        side_effect=fake_filter,
    ):
        for falsy in (None, "", 0):
            assert (
                GroundTruthService.load_active_gt(
                    eval_template=template,
                    organization_id=falsy,
                    workspace_id="ws-A",
                )
                is None
            )

    assert calls == []
