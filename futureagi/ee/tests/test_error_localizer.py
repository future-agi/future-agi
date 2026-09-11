import json
from unittest.mock import MagicMock, patch

import pytest
from ee.evals.localizer.error_localizer import (
    ErrorLocalizer,
    _enforce_verdict,
    _is_chunkable,
    _normalise_images,
)


def _make_localizer(input_data, input_type, **overrides):
    el = ErrorLocalizer(
        eval_name=overrides.get("eval_name", "test_eval"),
        rule_prompt=overrides.get("rule_prompt", "Check the {{response}}"),
        input=input_data,
        input_type=input_type,
        evaluation_result=overrides.get("evaluation_result", "Failed"),
        evaluation_explanation=overrides.get("evaluation_explanation", "wrong answer"),
        choices=overrides.get("choices", []),
    )
    el._llm = MagicMock()
    el._llm.provider = "vertex_ai"
    el._llm.model_name = "test-model"
    el._llm.max_tokens = 1024
    el._llm.temperature = 0.0
    el._llm._get_completion_content.return_value = (
        "<selected_input_key>response</selected_input_key>"
    )
    return el


def _agent_response(entries):
    return {"content": json.dumps({"entries": entries})}


@pytest.mark.unit
@pytest.mark.parametrize(
    ("input_type", "input_data", "expected"),
    [
        ("text", "hi", True),
        ("image", "http://x.jpg", True),
        ("audio", b"\x00\x01", True),
        ("images", ["http://x.jpg"], True),
        ("images", ["a.jpg", "b.jpg"], False),
        ("images", [], False),
        ("images", "not-a-list", False),
        ("pdf", "x.pdf", False),
        ("file", "x.bin", False),
        ("number", 42, False),
        ("unknown_type", None, False),
    ],
)
def test_is_chunkable(input_type, input_data, expected):
    assert _is_chunkable(input_type, input_data) is expected


@pytest.mark.unit
@pytest.mark.parametrize(
    ("input_type", "input_data", "expected"),
    [
        ("images", ["url-only"], ("image", "url-only")),
        ("images", ["a", "b"], ("images", ["a", "b"])),
        ("images", [], ("images", [])),
        ("images", "not-a-list", ("images", "not-a-list")),
        ("text", "hi", ("text", "hi")),
        ("audio", b"raw", ("audio", b"raw")),
    ],
)
def test_normalise_images(input_type, input_data, expected):
    assert _normalise_images(input_type, input_data) == expected


@pytest.mark.unit
@patch("ee.evals.localizer.error_localizer.asyncio.run")
@patch("ee.evals.localizer.error_localizer.AgentEvaluator.build_eval_input_blocks")
def test_invoke_llm_includes_sibling_inputs(mock_build, mock_run):
    mock_build.return_value = (
        "rendered rule",
        [
            {"type": "text", "text": "<expected>the answer is 91</expected>"},
            {"type": "text", "text": "<context>math</context>"},
        ],
    )
    mock_run.return_value = _agent_response([])

    el = _make_localizer(
        input_data={
            "response": "the answer is 42",
            "expected": "91",
            "context": "math",
        },
        input_type={"response": "text", "expected": "text", "context": "text"},
    )
    el._invoke_llm(
        "text",
        "response",
        [{"type": "text", "text": "<sentence_1>x</sentence_1>"}],
        "rid",
    )

    assert mock_run.called
    # build_eval_input_blocks called without excluding selected input
    call_kwargs = mock_build.call_args.kwargs
    assert (
        "exclude_keys" not in call_kwargs
        or call_kwargs.get("exclude_keys") is None
        or call_kwargs.get("exclude_keys") == set()
    )


@pytest.mark.unit
@patch("ee.evals.localizer.error_localizer.asyncio.run")
def test_invoke_llm_agentloop_failure_returns_empty(mock_run):
    mock_run.side_effect = RuntimeError("upstream gone")

    el = _make_localizer(
        input_data={"response": "x"},
        input_type={"response": "text"},
    )

    with pytest.raises(RuntimeError):
        el._invoke_llm("text", "response", [], "rid")


@pytest.mark.unit
@patch("ee.evals.localizer.error_localizer.asyncio.run")
@patch("ee.evals.localizer.error_localizer.AgentEvaluator.build_eval_input_blocks")
def test_invoke_llm_input_resolution_failure_degrades(mock_build, mock_run):
    mock_build.side_effect = RuntimeError("input fetch failed")
    mock_run.return_value = _agent_response([])

    el = _make_localizer(
        input_data={"response": "x"},
        input_type={"response": "text"},
    )
    entries = el._invoke_llm("text", "response", [], "rid")

    assert entries == []
    assert mock_run.called


@pytest.mark.unit
@patch("ee.evals.localizer.error_localizer.asyncio.run")
def test_invoke_llm_captures_cost(mock_run):
    mock_run.return_value = _agent_response([])

    el = _make_localizer(input_data={"r": "x"}, input_type={"r": "text"})

    with patch(
        "ee.evals.llm.agent_evaluator.context.client.EvalLLMClient"
    ) as mock_client_cls:
        mock_client = MagicMock()
        mock_client._gateway_cost = 0.005
        mock_client_cls.return_value = mock_client
        el._invoke_llm("text", "r", [], "rid")

    assert el.cost["total_cost"] == 0.005


@pytest.mark.unit
@patch("ee.evals.localizer.error_localizer.asyncio.run")
@patch("ee.evals.localizer.error_localizer._split_into_sentences")
def test_localize_text_attaches_orgSen(mock_split, mock_run):
    mock_split.return_value = {
        "sentence_1": {"text": "First sentence", "start_idx": 0, "end_idx": 14},
        "sentence_2": {"text": "Second sentence", "start_idx": 16, "end_idx": 31},
    }
    mock_run.return_value = _agent_response(
        [
            {
                "unit_key": "sentence_2",
                "rank": "1",
                "reason": "wrong fact",
                "improvement": "fix it",
                "rank_reason": "most severe",
            },
        ]
    )

    el = _make_localizer(
        input_data={"response": "First sentence. Second sentence."},
        input_type={"response": "text"},
    )

    result = el._localize("First sentence. Second sentence.", "response", "text")
    analysis, key = result.analysis, result.selected_key

    assert key == "response"
    entries = analysis["input_1"]
    assert len(entries) == 1
    assert entries[0]["unit_key"] == "sentence_2"
    assert entries[0]["orgSen"]["text"] == "Second sentence"


@pytest.mark.unit
@patch("ee.evals.localizer.error_localizer.asyncio.run")
@patch("ee.evals.localizer.error_localizer._split_into_sentences")
def test_localize_text_strips_angle_brackets_from_unit_key(mock_split, mock_run):
    mock_split.return_value = {
        "sentence_1": {"text": "Only sentence", "start_idx": 0, "end_idx": 13},
    }
    mock_run.return_value = _agent_response(
        [
            {"unit_key": "<sentence_1>", "rank": "1", "reason": "r"},
        ]
    )

    el = _make_localizer(input_data={"r": "Only sentence."}, input_type={"r": "text"})
    analysis = el._localize("Only sentence.", "r", "text").analysis

    assert analysis["input_1"][0]["unit_key"] == "sentence_1"
    assert analysis["input_1"][0]["orgSen"]["text"] == "Only sentence"


@pytest.mark.unit
@patch("ee.evals.localizer.error_localizer.asyncio.run")
@patch("ee.evals.localizer.error_localizer._split_into_sentences")
def test_localize_text_empty_response_falls_back_to_verdict(mock_split, mock_run):
    mock_split.return_value = {
        "sentence_1": {"text": "x", "start_idx": 0, "end_idx": 1}
    }
    mock_run.return_value = _agent_response([])

    el = _make_localizer(
        input_data={"r": "x"},
        input_type={"r": "text"},
        evaluation_explanation="The response is wrong.",
    )
    analysis = el._localize("x", "r", "text").analysis

    entries = analysis["input_1"]
    assert len(entries) == 1
    assert entries[0]["unit_key"] == "whole_input"
    assert "wrong" in entries[0]["reason"]


@pytest.mark.unit
@patch("ee.evals.localizer.error_localizer.asyncio.run")
@patch("ee.evals.localizer.error_localizer._create_audio_segments")
def test_localize_audio_attaches_orgSegment(mock_segs, mock_run):
    mock_segs.return_value = {
        "segment_1": {
            "url": "s3://b/1.mp3",
            "duration": 5.0,
            "start_time": 0.0,
            "end_time": 5.0,
            "audio_bytes": "b64",
        },
        "segment_2": {
            "url": "s3://b/2.mp3",
            "duration": 5.0,
            "start_time": 5.0,
            "end_time": 10.0,
            "audio_bytes": "b64",
        },
    }
    mock_run.return_value = _agent_response(
        [
            {
                "unit_key": "segment_2",
                "rank": "1",
                "reason": "noisy",
                "improvement": "denoise",
                "rank_reason": "loud",
            },
        ]
    )

    el = _make_localizer(
        input_data={"clip": "http://x.mp3"}, input_type={"clip": "audio"}
    )
    result = el._localize("http://x.mp3", "clip", "audio")
    analysis, key = result.analysis, result.selected_key

    assert key == "clip"
    entries = analysis["input_1"]
    assert entries[0]["orgSegment"] == {
        "url": "s3://b/2.mp3",
        "duration": 5.0,
        "start_time": 5.0,
        "end_time": 10.0,
    }


@pytest.mark.unit
@patch("ee.evals.localizer.error_localizer.asyncio.run")
@patch("ee.evals.localizer.error_localizer._create_audio_segments")
def test_localize_audio_empty_segments_skips_without_calling_llm(mock_segs, mock_run):
    mock_segs.return_value = {}

    el = _make_localizer(
        input_data={"clip": "http://x.mp3"},
        input_type={"clip": "audio"},
        evaluation_explanation="audio is silent",
    )
    result = el._localize("http://x.mp3", "clip", "audio")
    analysis, key = result.analysis, result.selected_key

    assert analysis == {}
    assert key == "clip"
    assert result.skip_reason is not None
    assert "audio" in result.skip_reason
    mock_run.assert_not_called()


@pytest.mark.unit
@patch("ee.evals.localizer.error_localizer.asyncio.run")
@patch("ee.evals.localizer.error_localizer._build_full_image_block")
@patch("ee.evals.localizer.error_localizer._create_overlapping_patches")
def test_localize_image_attaches_orgPatch(mock_patches, mock_thumb, mock_run):
    mock_patches.return_value = {
        "patch_1": {
            "url": "s3://b/1.jpg",
            "image_b64": "b64a",
            "coordinates": {"top_left": (0, 0), "bottom_right": (100, 100)},
        },
        "patch_2": {
            "url": "s3://b/2.jpg",
            "image_b64": "b64b",
            "coordinates": {"top_left": (100, 0), "bottom_right": (200, 100)},
        },
    }
    mock_thumb.return_value = ([{"type": "text", "text": "<full_image>"}], 200, 100)
    mock_run.return_value = _agent_response(
        [
            {
                "unit_key": "patch_2",
                "rank": "1",
                "reason": "wrong colour",
                "improvement": "fix it",
                "rank_reason": "most severe",
            },
        ]
    )

    el = _make_localizer(
        input_data={"pic": "http://x.jpg"}, input_type={"pic": "image"}
    )
    result = el._localize("http://x.jpg", "pic", "image")
    analysis, key = result.analysis, result.selected_key

    assert key == "pic"
    entries = analysis["input_1"]
    # orgPatch stores coordinates only — url and image_b64 are not persisted
    assert "url" not in entries[0]["orgPatch"]
    assert "image_b64" not in entries[0]["orgPatch"]
    assert entries[0]["orgPatch"]["coordinates"]["top_left"] == [100, 0]


@pytest.mark.unit
@patch("ee.evals.localizer.error_localizer.asyncio.run")
@patch("ee.evals.localizer.error_localizer._build_full_image_block")
@patch("ee.evals.localizer.error_localizer._create_overlapping_patches")
def test_localize_image_empty_llm_synthesises_whole_image(
    mock_patches, mock_thumb, mock_run
):
    mock_patches.return_value = {
        "patch_1": {
            "url": "s3://b/1.jpg",
            "image_b64": "b64a",
            "coordinates": {"top_left": (0, 0), "bottom_right": (200, 100)},
        },
    }
    mock_thumb.return_value = ([{"type": "text", "text": "<full_image>"}], 200, 100)
    mock_run.return_value = _agent_response([])

    el = _make_localizer(
        input_data={"pic": "http://x.jpg"},
        input_type={"pic": "image"},
        evaluation_explanation="scene contradicts prompt",
    )
    analysis = el._localize("http://x.jpg", "pic", "image").analysis

    entries = analysis["input_1"]
    assert entries[0]["unit_key"] == "whole_image"
    assert entries[0]["orgPatch"]["coordinates"]["bottom_right"] == [200, 100]


@pytest.mark.unit
@patch("ee.evals.localizer.error_localizer.asyncio.run")
@patch("ee.evals.localizer.error_localizer._create_overlapping_patches")
def test_localize_image_thumbnail_failure_degrades_gracefully(mock_patches, mock_run):
    mock_patches.return_value = {
        "patch_1": {
            "url": "s3://b/1.jpg",
            "image_b64": "b64a",
            "coordinates": {"top_left": (0, 0), "bottom_right": (100, 100)},
        },
    }
    mock_run.return_value = _agent_response([])

    el = _make_localizer(
        input_data={"pic": "http://broken.jpg"}, input_type={"pic": "image"}
    )

    with patch(
        "ee.evals.localizer.error_localizer._build_full_image_block",
        side_effect=RuntimeError("failed"),
    ):
        analysis = el._localize("http://broken.jpg", "pic", "image").analysis

    assert analysis["input_1"][0]["unit_key"] == "whole_image"


@pytest.mark.unit
def test_select_target_single_input_skips_llm():
    el = _make_localizer(input_data={"output": "x"}, input_type={"output": "text"})
    el._llm._get_completion_content.reset_mock()

    assert el._select_target_input() == "output"
    el._llm._get_completion_content.assert_not_called()


@pytest.mark.unit
def test_select_target_empty_inputs_returns_none():
    el = _make_localizer(input_data={}, input_type={})
    assert el._select_target_input() is None


@pytest.mark.unit
def test_select_target_multi_input_calls_llm():
    el = _make_localizer(
        input_data={"a": "x", "b": "y"},
        input_type={"a": "text", "b": "text"},
    )
    el._llm._get_completion_content.return_value = (
        "<selected_input_key>b</selected_input_key>"
    )

    picked = el._select_target_input()

    assert picked == "b"
    el._llm._get_completion_content.assert_called()


@pytest.mark.unit
def test_select_target_fallback_to_output_key():
    el = _make_localizer(
        input_data={"output": "x", "context": "y"},
        input_type={"output": "text", "context": "text"},
    )
    # LLM returns garbage — should fall back to "output"
    el._llm._get_completion_content.return_value = "invalid response"

    picked = el._select_target_input()
    assert picked == "output"


@pytest.mark.unit
@patch("ee.evals.localizer.error_localizer.asyncio.run")
@patch("ee.evals.localizer.error_localizer._split_into_sentences")
def test_localize_text_whole_text_from_llm_synthesises_orgSen(mock_split, mock_run):
    mock_split.return_value = {
        "sentence_1": {"text": "x", "start_idx": 0, "end_idx": 1}
    }
    mock_run.return_value = _agent_response(
        [
            {
                "unit_key": "whole_text",
                "rank": "1",
                "reason": "all of it is bad",
                "improvement": "rewrite",
                "rank_reason": "global failure",
            },
        ]
    )

    el = _make_localizer(
        input_data={"r": "Full response text."}, input_type={"r": "text"}
    )
    analysis = el._localize("Full response text.", "r", "text").analysis

    entry = analysis["input_1"][0]
    assert entry["unit_key"] == "whole_text"
    assert entry["orgSen"] == {
        "text": "Full response text.",
        "start_idx": 0,
        "end_idx": 19,
    }


@pytest.mark.unit
@patch("ee.evals.localizer.error_localizer.asyncio.run")
@patch("ee.evals.localizer.error_localizer._create_audio_segments")
def test_localize_audio_whole_audio_from_llm_synthesises_orgSegment(
    mock_segs, mock_run
):
    mock_segs.return_value = {
        "segment_1": {
            "url": "s3://b/1.mp3",
            "duration": 5.0,
            "start_time": 0.0,
            "end_time": 5.0,
            "audio_bytes": "b",
        },
        "segment_2": {
            "url": "s3://b/2.mp3",
            "duration": 7.5,
            "start_time": 5.0,
            "end_time": 12.5,
            "audio_bytes": "b",
        },
    }
    mock_run.return_value = _agent_response(
        [
            {
                "unit_key": "whole_audio",
                "rank": "1",
                "reason": "whole call failed",
                "improvement": "redo",
                "rank_reason": "global failure",
            },
        ]
    )

    el = _make_localizer(
        input_data={"clip": "http://x.mp3"}, input_type={"clip": "audio"}
    )
    analysis = el._localize("http://x.mp3", "clip", "audio").analysis

    entry = analysis["input_1"][0]
    assert entry["unit_key"] == "whole_audio"
    assert entry["orgSegment"] == {
        "url": "http://x.mp3",
        "duration": 12.5,
        "start_time": 0,
        "end_time": 12.5,
    }


@pytest.mark.unit
@patch("ee.evals.localizer.error_localizer.asyncio.run")
@patch("ee.evals.localizer.error_localizer._build_full_image_block")
@patch("ee.evals.localizer.error_localizer._create_overlapping_patches")
def test_localize_image_whole_image_from_llm_synthesises_orgPatch(
    mock_patches, mock_thumb, mock_run
):
    mock_patches.return_value = {
        "patch_1": {
            "url": "s3://b/1.jpg",
            "image_b64": "b",
            "coordinates": {"top_left": (0, 0), "bottom_right": (100, 100)},
        },
    }
    mock_thumb.return_value = ([{"type": "text", "text": "<full_image>"}], 200, 150)
    mock_run.return_value = _agent_response(
        [
            {
                "unit_key": "whole_image",
                "rank": "1",
                "reason": "wrong style",
                "improvement": "regenerate",
                "rank_reason": "global failure",
            },
        ]
    )

    el = _make_localizer(
        input_data={"pic": "http://x.jpg"}, input_type={"pic": "image"}
    )
    analysis = el._localize("http://x.jpg", "pic", "image").analysis

    entry = analysis["input_1"][0]
    assert entry["unit_key"] == "whole_image"
    assert entry["orgPatch"]["coordinates"] == {
        "top_left": [0, 0],
        "top_right": [200, 0],
        "bottom_left": [0, 150],
        "bottom_right": [200, 150],
    }


@pytest.mark.unit
@patch("ee.evals.localizer.error_localizer.asyncio.run")
@patch("ee.evals.localizer.error_localizer._create_audio_segments")
def test_localize_audio_whole_audio_skips_synth_when_input_not_string(
    mock_segs, mock_run
):
    """If input_data isn't a usable URL, do not emit a broken orgSegment (would
    crash the FE audio player). Leave the entry without orgSegment instead."""
    mock_segs.return_value = {
        "segment_1": {
            "url": "s3://b/1.mp3",
            "duration": 5.0,
            "start_time": 0.0,
            "end_time": 5.0,
            "audio_bytes": "b",
        },
    }
    mock_run.return_value = _agent_response(
        [
            {
                "unit_key": "whole_audio",
                "rank": "1",
                "reason": "bad",
                "improvement": "fix",
                "rank_reason": "global",
            },
        ]
    )

    el = _make_localizer(
        input_data={"clip": b"raw bytes"}, input_type={"clip": "audio"}
    )
    analysis = el._localize(b"raw bytes", "clip", "audio").analysis

    entry = analysis["input_1"][0]
    assert entry["unit_key"] == "whole_audio"
    assert "orgSegment" not in entry  # silently degrades — no broken player URL


@pytest.mark.unit
@patch("ee.evals.localizer.error_localizer.asyncio.run")
@patch("ee.evals.localizer.error_localizer._split_into_sentences")
def test_localize_whole_text_and_specific_sentence_in_same_response(
    mock_split, mock_run
):
    """When LLM returns both whole_text AND a specific sentence_N, both should
    end up with their respective orgX so FE can render whichever it prefers."""
    mock_split.return_value = {
        "sentence_1": {"text": "First", "start_idx": 0, "end_idx": 5},
        "sentence_2": {"text": "Second", "start_idx": 7, "end_idx": 13},
    }
    mock_run.return_value = _agent_response(
        [
            {
                "unit_key": "whole_text",
                "rank": "1",
                "reason": "context",
                "improvement": "global",
                "rank_reason": "g",
            },
            {
                "unit_key": "sentence_2",
                "rank": "2",
                "reason": "fact wrong",
                "improvement": "fix",
                "rank_reason": "specific",
            },
        ]
    )

    el = _make_localizer(input_data={"r": "First. Second."}, input_type={"r": "text"})
    analysis = el._localize("First. Second.", "r", "text").analysis

    entries = analysis["input_1"]
    assert len(entries) == 2
    by_key = {e["unit_key"]: e for e in entries}
    assert by_key["whole_text"]["orgSen"] == {
        "text": "First. Second.",
        "start_idx": 0,
        "end_idx": 14,
    }
    assert by_key["sentence_2"]["orgSen"]["text"] == "Second"


def test_enforce_verdict_text_passthrough_when_entries_present():
    entries = [{"rank": "1", "unit_key": "sentence_1", "reason": "x"}]
    assert _enforce_verdict(entries, "text", "Hello.", "explain", None) == entries


def test_enforce_verdict_text_empty_falls_back_to_whole_input():
    out = _enforce_verdict([], "text", "Hello.", "explain", None)
    assert len(out) == 1
    assert out[0]["unit_key"] == "whole_input"
    assert out[0]["reason"] == "explain"


def test_enforce_verdict_image_drops_malformed_entries():
    entries = [
        "not a dict",
        {"orgPatch": "not a dict"},
        {"orgPatch": {"coordinates": "not a dict"}},
        {"orgPatch": {"coordinates": {"top_left": "x", "bottom_right": [10, 10]}}},
        {"orgPatch": {"coordinates": {"top_left": [0], "bottom_right": [10, 10]}}},
        {"orgPatch": {"coordinates": {"top_left": [10, 10], "bottom_right": [5, 5]}}},
    ]
    out = _enforce_verdict(entries, "image", b"img", "explain", (100, 100))
    assert len(out) == 1
    assert out[0]["unit_key"] == "whole_image"


def test_enforce_verdict_image_clamps_coords_to_dims():
    entries = [
        {
            "rank": "1",
            "unit_key": "p",
            "orgPatch": {
                "coordinates": {
                    "top_left": [-5, -5],
                    "top_right": [0, 0],
                    "bottom_left": [0, 0],
                    "bottom_right": [200, 200],
                }
            },
        }
    ]
    out = _enforce_verdict(entries, "image", b"img", "explain", (100, 100))
    assert len(out) == 1
    coords = out[0]["orgPatch"]["coordinates"]
    assert coords["top_left"] == [0, 0]
    assert coords["bottom_right"] == [100, 100]


def test_enforce_verdict_image_empty_returns_whole_image_with_dims():
    out = _enforce_verdict(None, "image", b"img", None, (640, 480))
    assert len(out) == 1
    assert out[0]["unit_key"] == "whole_image"
    coords = out[0]["orgPatch"]["coordinates"]
    assert coords["bottom_right"] == [640, 480]
