"""Unit tests for GroundTruthService with a mocked EmbeddingManager."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import patch

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


@dataclass
class _FakeGT:
    """Honours every attribute the service touches and records save calls."""

    id: str = "gt-fake"
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
    save_calls: list[list[str]] = field(default_factory=list)

    def save(self, update_fields=None):
        self.save_calls.append(list(update_fields or []))


# ─────────────────────────────────────────────────────────────────────
# create_from_upload
# ─────────────────────────────────────────────────────────────────────


def test_create_from_upload_stamps_item_ids_and_persists():
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

    stamped = captured["data"]
    assert len(stamped) == 2
    assert all(len(row["item_id"]) == 32 for row in stamped)
    assert stamped[0]["item_id"] != stamped[1]["item_id"]
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

    with patch(
        "agentic_eval.core.embeddings.embedding_manager.EmbeddingManager.soft_delete_vectors"
    ), patch(
        "agentic_eval.core.embeddings.embedding_manager.EmbeddingManager.parallel_process_metadata"
    ):
        result = GroundTruthService.embed_dataset(gt=gt)

    assert result.status == EvalGroundTruth.EmbeddingStatus.COMPLETED
    assert result.rows_embedded == 2
    assert gt.embedded_row_count == 2
    assert gt.embedding_status == EvalGroundTruth.EmbeddingStatus.COMPLETED


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
    rows = GroundTruthService.retrieve_few_shot(
        gt=gt, inputs={"q": "hi"}, max_results=3
    )
    assert rows == []


def test_retrieve_few_shot_short_circuits_when_mapping_missing():
    gt = _FakeGT(embedding_status=EvalGroundTruth.EmbeddingStatus.COMPLETED, variable_mapping={})
    rows = GroundTruthService.retrieve_few_shot(
        gt=gt, inputs={"q": "hi"}, max_results=3
    )
    assert rows == []


def test_retrieve_few_shot_hydrates_canonical_rows_from_pg_by_item_id():
    gt = _FakeGT(
        embedding_status=EvalGroundTruth.EmbeddingStatus.COMPLETED,
        variable_mapping={"q": "question"},
        data=[
            {"item_id": "abc123", "question": "hi", "verdict": "Pass"},
            {"item_id": "def456", "question": "yo", "verdict": "Fail"},
        ],
    )

    raw_groups = [
        [{"item_id": "abc123", "column_name": "question", "input_type": "text"}],
        [{"item_id": "def456", "column_name": "question", "input_type": "text"}],
    ]

    with patch(
        "agentic_eval.core.embeddings.embedding_manager.EmbeddingManager"
    ) as mock_manager_cls:
        mock_manager = mock_manager_cls.return_value
        mock_manager.retrieve_avg_rag_based_examples.return_value = raw_groups
        rows = GroundTruthService.retrieve_few_shot(
            gt=gt, inputs={"q": "hi"}, max_results=2
        )

    mock_manager.retrieve_avg_rag_based_examples.assert_called_once()
    assert rows == [
        {"question": "hi", "verdict": "Pass"},
        {"question": "yo", "verdict": "Fail"},
    ]


def test_retrieve_few_shot_skips_match_when_item_id_missing_from_pg():
    gt = _FakeGT(
        embedding_status=EvalGroundTruth.EmbeddingStatus.COMPLETED,
        variable_mapping={"q": "question"},
        data=[{"item_id": "abc123", "question": "hi", "verdict": "Pass"}],
    )

    raw_groups = [
        [{"item_id": "abc123", "column_name": "question", "input_type": "text"}],
        [{"item_id": "gone999", "column_name": "question", "input_type": "text"}],
    ]

    with patch(
        "agentic_eval.core.embeddings.embedding_manager.EmbeddingManager"
    ) as mock_manager_cls:
        mock_manager = mock_manager_cls.return_value
        mock_manager.retrieve_avg_rag_based_examples.return_value = raw_groups
        rows = GroundTruthService.retrieve_few_shot(
            gt=gt, inputs={"q": "hi"}, max_results=5
        )

    assert rows == [{"question": "hi", "verdict": "Pass"}]


def test_retrieve_few_shot_dedups_same_item_id_across_groups():
    gt = _FakeGT(
        embedding_status=EvalGroundTruth.EmbeddingStatus.COMPLETED,
        variable_mapping={"q": "question", "ctx": "context"},
        data=[{"item_id": "abc123", "question": "hi", "context": "polite", "verdict": "Pass"}],
    )

    raw_groups = [
        [{"item_id": "abc123", "column_name": "question", "input_type": "text"}],
        [{"item_id": "abc123", "column_name": "context", "input_type": "text"}],
    ]

    with patch(
        "agentic_eval.core.embeddings.embedding_manager.EmbeddingManager"
    ) as mock_manager_cls:
        mock_manager = mock_manager_cls.return_value
        mock_manager.retrieve_avg_rag_based_examples.return_value = raw_groups
        rows = GroundTruthService.retrieve_few_shot(
            gt=gt, inputs={"q": "hi"}, max_results=5
        )

    assert rows == [
        {"question": "hi", "context": "polite", "verdict": "Pass"}
    ]


# ─────────────────────────────────────────────────────────────────────
# search (Test Retrieval surface)
# ─────────────────────────────────────────────────────────────────────


def test_search_rejects_when_not_completed():
    gt = _FakeGT(embedding_status=EvalGroundTruth.EmbeddingStatus.PROCESSING, variable_mapping={"q": "q"})
    result = GroundTruthService.search(
        gt=gt, inputs={"q": "hi"}, query=None, max_results=3
    )
    assert isinstance(result, ServiceError)
    assert result.code == "EMBEDDINGS_NOT_READY"


def test_search_rejects_empty_input():
    gt = _FakeGT(embedding_status=EvalGroundTruth.EmbeddingStatus.COMPLETED, variable_mapping={"q": "q"})
    result = GroundTruthService.search(
        gt=gt, inputs=None, query="   ", max_results=3
    )
    assert isinstance(result, ServiceError)
    assert result.code == "EMPTY_INPUT"


def test_search_dispatches_to_helper_with_inputs():
    gt = _FakeGT(
        embedding_status=EvalGroundTruth.EmbeddingStatus.COMPLETED,
        variable_mapping={"q": "question"},
    )
    with patch.object(
        GroundTruthService, "retrieve_few_shot", return_value=[{"q": "hi"}]
    ) as mock:
        result = GroundTruthService.search(
            gt=gt, inputs={"q": "hi"}, query=None, max_results=2
        )
    mock.assert_called_once()
    assert result["total"] == 1
    assert result["inputs"] == {"q": "hi"}


def test_search_legacy_query_fans_out_to_all_mapped_vars():
    gt = _FakeGT(
        embedding_status=EvalGroundTruth.EmbeddingStatus.COMPLETED,
        variable_mapping={"q": "question", "a": "answer"},
    )
    with patch.object(
        GroundTruthService, "retrieve_few_shot", return_value=[]
    ) as mock:
        result = GroundTruthService.search(
            gt=gt, inputs=None, query="hi", max_results=2
        )
    mock.assert_called_once()
    call_inputs = mock.call_args.kwargs["inputs"]
    assert call_inputs == {"q": "hi", "a": "hi"}
    assert result["query"] == "hi"


# ─────────────────────────────────────────────────────────────────────
# resolve_preview_examples: pure helper, no DB / DRF / Temporal.
# ─────────────────────────────────────────────────────────────────────


def test_resolve_preview_examples_returns_none_when_gt_config_missing():
    """No GT config: returns None so the FE panel hides."""
    template = _FakeTemplate()
    with patch(
        "model_hub.services.ground_truth_service.GroundTruthService.load_config",
        return_value=None,
    ):
        result = GroundTruthService.resolve_preview_examples(
            eval_template=template, eval_inputs={"q": "hi"}
        )
    assert result is None


def test_resolve_preview_examples_returns_none_when_inputs_blank():
    """No usable inputs: returns None so the FE panel hides."""
    template = _FakeTemplate()
    with patch(
        "model_hub.services.ground_truth_service.GroundTruthService.load_config",
        return_value={"ground_truth_id": "gt-fake", "enabled": True},
    ):
        result = GroundTruthService.resolve_preview_examples(
            eval_template=template, eval_inputs={}
        )
    assert result is None


def test_resolve_preview_examples_returns_empty_list_when_enabled_but_no_matches():
    """GT enabled with zero matches: returns [] so the FE panel
    renders the empty-state row."""
    template = _FakeTemplate()

    class _FakeQuerySet:
        def only(self, *_a, **_kw):
            return self

        def first(self):
            return type(
                "GT", (), {"variable_mapping": {"q": "question"}, "role_mapping": {}}
            )()

    class _FakeManager:
        def filter(self, **_kw):
            return _FakeQuerySet()

    with patch(
        "model_hub.services.ground_truth_service.GroundTruthService.load_config",
        return_value={"ground_truth_id": "gt-fake", "enabled": True},
    ), patch(
        "model_hub.services.ground_truth_service.GroundTruthService.retrieve_few_shot",
        return_value=[],
    ), patch(
        "model_hub.services.ground_truth_service.EvalGroundTruth"
    ) as mock_gt_model:
        mock_gt_model.objects = _FakeManager()
        result = GroundTruthService.resolve_preview_examples(
            eval_template=template, eval_inputs={"q": "hi"}
        )
    assert result == []


def test_resolve_preview_examples_swallows_exceptions_returning_none():
    """Retrieval failures do not bubble up to the eval verdict."""
    template = _FakeTemplate()
    with patch(
        "model_hub.services.ground_truth_service.GroundTruthService.load_config",
        side_effect=RuntimeError("boom"),
    ):
        result = GroundTruthService.resolve_preview_examples(
            eval_template=template, eval_inputs={"q": "hi"}
        )
    assert result is None


def test_resolve_preview_examples_enriches_rows_with_mappings():
    """Happy path: each retrieved row is enriched with the GT's
    variable_mapping and role_mapping so the FE can render the card
    without a follow-up fetch."""
    template = _FakeTemplate()
    retrieved_rows = [
        {"question": "What is 2+2?", "answer": "4", "notes": "trivial"},
        {"question": "Capital of France?", "answer": "Paris", "notes": "easy"},
    ]
    fake_gt = type(
        "FakeGTRow",
        (),
        {
            "variable_mapping": {"q": "question"},
            "role_mapping": {"output": "answer", "explanation": "notes"},
        },
    )()

    class _FakeQuerySet:
        def only(self, *_args, **_kwargs):
            return self

        def first(self):
            return fake_gt

    class _FakeManager:
        def filter(self, **_kwargs):
            return _FakeQuerySet()

    with patch(
        "model_hub.services.ground_truth_service.GroundTruthService.load_config",
        return_value={"ground_truth_id": "gt-fake", "enabled": True},
    ), patch(
        "model_hub.services.ground_truth_service.GroundTruthService.retrieve_few_shot",
        return_value=retrieved_rows,
    ), patch(
        "model_hub.services.ground_truth_service.EvalGroundTruth"
    ) as mock_gt_model:
        mock_gt_model.objects = _FakeManager()
        result = GroundTruthService.resolve_preview_examples(
            eval_template=template, eval_inputs={"q": "hi"}
        )

    assert len(result) == 2
    for enriched, original in zip(result, retrieved_rows, strict=True):
        assert enriched["row"] == original
        assert enriched["variable_mapping"] == {"q": "question"}
        assert enriched["role_mapping"] == {
            "output": "answer",
            "explanation": "notes",
        }
