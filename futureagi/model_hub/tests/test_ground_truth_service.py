"""Unit tests for GroundTruthService with a mocked EmbeddingManager."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import patch

import pytest

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
    embedding_status: str = "pending"
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
# update_variable_mapping
# ─────────────────────────────────────────────────────────────────────


def test_update_variable_mapping_persists_with_no_stale_when_never_embedded():
    gt = _FakeGT(columns=["question", "answer"], embedding_status="pending")

    result = GroundTruthService.update_variable_mapping(
        gt=gt, variable_mapping={"q": "question"}
    )

    assert result == {
        "id": "gt-fake",
        "variable_mapping": {"q": "question"},
        "embedding_status": "pending",
        "embeddings_stale": False,
    }
    assert gt.variable_mapping == {"q": "question"}
    # The single save call must not include embedding_status — the
    # status flip only fires when an already-completed dataset gets
    # remapped.
    assert "embedding_status" not in gt.save_calls[0]


def test_update_variable_mapping_flips_completed_to_pending_on_change():
    gt = _FakeGT(
        columns=["a", "b"],
        variable_mapping={"x": "a"},
        embedding_status="completed",
    )

    result = GroundTruthService.update_variable_mapping(
        gt=gt, variable_mapping={"x": "b"}
    )

    assert result["embeddings_stale"] is True
    assert gt.embedding_status == "pending"
    assert "embedding_status" in gt.save_calls[0]


def test_update_variable_mapping_idempotent_save_keeps_status():
    gt = _FakeGT(
        columns=["a"],
        variable_mapping={"x": "a"},
        embedding_status="completed",
    )

    result = GroundTruthService.update_variable_mapping(
        gt=gt, variable_mapping={"x": "a"}
    )

    assert result["embeddings_stale"] is False
    assert gt.embedding_status == "completed"


def test_update_variable_mapping_rejects_unknown_column():
    gt = _FakeGT(columns=["question", "answer"])

    result = GroundTruthService.update_variable_mapping(
        gt=gt, variable_mapping={"q": "nope"}
    )

    assert isinstance(result, ServiceError)
    assert "nope" in result.message
    assert result.code == "INVALID_COLUMN"
    # Nothing must persist on the failure path — no save calls.
    assert not gt.save_calls


def test_update_variable_mapping_supports_list_of_columns():
    gt = _FakeGT(columns=["a", "b", "c"])
    result = GroundTruthService.update_variable_mapping(
        gt=gt, variable_mapping={"x": ["a", "b"]}
    )
    assert result["variable_mapping"] == {"x": ["a", "b"]}


def test_update_variable_mapping_rejects_missing_inside_list():
    gt = _FakeGT(columns=["a"])
    result = GroundTruthService.update_variable_mapping(
        gt=gt, variable_mapping={"x": ["a", "missing"]}
    )
    assert isinstance(result, ServiceError)
    assert "missing" in result.message


# ─────────────────────────────────────────────────────────────────────
# embed_dataset
# ─────────────────────────────────────────────────────────────────────


def test_embed_dataset_fails_when_no_rows():
    gt = _FakeGT(data=[], variable_mapping={"q": "question"})
    result = GroundTruthService.embed_dataset(gt=gt)
    assert isinstance(result, EmbedDatasetResult)
    assert result.status == "failed"
    assert "no rows" in (result.error or "").lower()
    assert gt.embedding_status == "failed"


def test_embed_dataset_fails_when_no_mapped_columns():
    gt = _FakeGT(
        data=[{"q": "hi"}],
        variable_mapping={},
    )
    result = GroundTruthService.embed_dataset(gt=gt)
    assert result.status == "failed"
    assert "mapping" in (result.error or "").lower()


def test_embed_dataset_marks_failed_when_writer_raises():
    gt = _FakeGT(
        data=[{"q": "hi"}],
        variable_mapping={"q": "q"},
    )

    with patch(
        "model_hub.services.ground_truth_service._soft_delete_prior_vectors"
    ), patch(
        "agentic_eval.core.embeddings.embedding_manager.EmbeddingManager.parallel_process_metadata",
        side_effect=RuntimeError("ch unreachable"),
    ):
        result = GroundTruthService.embed_dataset(gt=gt)

    assert result.status == "failed"
    assert "ch unreachable" in (result.error or "")
    assert gt.embedding_status == "failed"


def test_embed_dataset_marks_completed_on_success():
    gt = _FakeGT(
        data=[{"q": "hi"}, {"q": "yo"}],
        variable_mapping={"q": "q"},
    )

    with patch(
        "model_hub.services.ground_truth_service._soft_delete_prior_vectors"
    ), patch(
        "agentic_eval.core.embeddings.embedding_manager.EmbeddingManager.parallel_process_metadata"
    ):
        result = GroundTruthService.embed_dataset(gt=gt)

    assert result.status == "completed"
    assert result.rows_embedded == 2
    assert gt.embedded_row_count == 2
    assert gt.embedding_status == "completed"


# ─────────────────────────────────────────────────────────────────────
# retrieve_few_shot
# ─────────────────────────────────────────────────────────────────────


def test_retrieve_few_shot_short_circuits_when_not_completed():
    gt = _FakeGT(embedding_status="pending", variable_mapping={"q": "q"})
    rows = GroundTruthService.retrieve_few_shot(
        gt=gt, inputs={"q": "hi"}, max_results=3
    )
    assert rows == []


def test_retrieve_few_shot_short_circuits_when_mapping_missing():
    gt = _FakeGT(embedding_status="completed", variable_mapping={})
    rows = GroundTruthService.retrieve_few_shot(
        gt=gt, inputs={"q": "hi"}, max_results=3
    )
    assert rows == []


def test_retrieve_few_shot_delegates_to_helper():
    gt = _FakeGT(
        embedding_status="completed",
        variable_mapping={"q": "question"},
    )

    sentinel = [{"item_id": "1", "question": "hi", "verdict": "Pass"}]

    with patch(
        "agentic_eval.core.embeddings.ground_truth_fewshots.retrieve_ground_truth_fewshots"
    ) as mock_retrieve:
        mock_retrieve.return_value = [
            type(
                "M",
                (),
                {"row": sentinel[0], "item_id": "1", "per_column_input_types": {}},
            )()
        ]
        rows = GroundTruthService.retrieve_few_shot(
            gt=gt, inputs={"q": "hi"}, max_results=2
        )

    mock_retrieve.assert_called_once()
    assert rows == sentinel


# ─────────────────────────────────────────────────────────────────────
# search (Test Retrieval surface)
# ─────────────────────────────────────────────────────────────────────


def test_search_rejects_when_not_completed():
    gt = _FakeGT(embedding_status="processing", variable_mapping={"q": "q"})
    result = GroundTruthService.search(
        gt=gt, inputs={"q": "hi"}, query=None, max_results=3
    )
    assert isinstance(result, ServiceError)
    assert result.code == "EMBEDDINGS_NOT_READY"


def test_search_rejects_empty_input():
    gt = _FakeGT(embedding_status="completed", variable_mapping={"q": "q"})
    result = GroundTruthService.search(
        gt=gt, inputs=None, query="   ", max_results=3
    )
    assert isinstance(result, ServiceError)
    assert result.code == "EMPTY_INPUT"


def test_search_dispatches_to_helper_with_inputs():
    gt = _FakeGT(
        embedding_status="completed",
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
        embedding_status="completed",
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
        "model_hub.utils.ground_truth_retrieval.load_ground_truth_config",
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
        "model_hub.utils.ground_truth_retrieval.load_ground_truth_config",
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
        "model_hub.utils.ground_truth_retrieval.load_ground_truth_config",
        return_value={"ground_truth_id": "gt-fake", "enabled": True},
    ), patch(
        "model_hub.utils.ground_truth_retrieval.get_ground_truth_few_shot_examples",
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
        "model_hub.utils.ground_truth_retrieval.load_ground_truth_config",
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
        "model_hub.utils.ground_truth_retrieval.load_ground_truth_config",
        return_value={"ground_truth_id": "gt-fake", "enabled": True},
    ), patch(
        "model_hub.utils.ground_truth_retrieval.get_ground_truth_few_shot_examples",
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
