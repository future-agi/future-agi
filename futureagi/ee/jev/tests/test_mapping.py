"""R-03, R-07..R-12: field compatibility C1..C17 and C20."""

import copy
from types import SimpleNamespace

import pytest

from ee.jev.mapping import (
    JevMappingError,
    build_question,
    build_state,
    check_input_size,
    mapping_revision,
    validate_jev_mapping,
)

pytestmark = pytest.mark.requires_ee


def mapping(output="Pass/Fail", scores=None, multi=False, **config):
    config.setdefault("eval_type_id", "CustomPromptEvaluator")
    return validate_jev_mapping(
        SimpleNamespace(config=config),
        output=output,
        choice_scores=scores,
        multi_choice=multi,
        config=config,
    )


def test_r07_c1_noul_and_criteria():
    result = mapping(jev_mapping={"pass": {"criteria_true": "grounded"}})
    assert build_question(result, "Judge") == (
        "q1",
        {"type": "noul", "instructions": "Judge", "criteria": {"true": "grounded"}},
    )
    assert result.to_dict()["revision"] == "jev-map-v1"


def test_r08_c2_exact_choice_labels_and_descriptions():
    result = mapping(
        "choices",
        {"Yes": 1, "No": 0},
        jev_mapping={
            "choice": {"labels": ["Yes", "No"], "descriptions": {"Yes": "correct"}}
        },
    )
    assert build_question(result, "Judge")[1]["criteria"] == {
        "Yes": "correct",
        "No": None,
    }


@pytest.mark.parametrize(
    "scores",
    [{}, {"": 1}, {"Yes": 1, " yes ": 0}, {1: 1}, {str(i): 0 for i in range(256)}],
)
def test_r08_c2_reject_labels(scores):
    with pytest.raises(JevMappingError, match="JEV_CHOICE_LABELS_INVALID"):
        mapping("choices", scores)


@pytest.mark.parametrize(
    "levels",
    [[], ["one"], [str(i) for i in range(11)], ["", "ok"], ["ok", "ok"], [1, "ok"]],
)
def test_r09_c3_reject_levels(levels):
    with pytest.raises(JevMappingError, match="JEV_SCORE_LEVELS_INVALID"):
        mapping("score", jev_mapping={"score": {"levels": levels}})


def test_r09_c3_score_levels_verbatim():
    levels = ["bad", "medium", "good"]
    assert (
        build_question(
            mapping("score", jev_mapping={"score": {"levels": levels}}), "Judge"
        )[1]["criteria"]
        == levels
    )


def test_r09_c4_c20_numeric_requires_explicit_levels():
    with pytest.raises(JevMappingError, match="JEV_SCORE_LEVELS_REQUIRED"):
        mapping("score")


@pytest.mark.parametrize(
    "config,code",
    [
        ({"multi_choice": True}, "JEV_MULTI_CHOICE_UNSUPPORTED"),
        (
            {"messages": [{"role": "user", "content": "hello"}]},
            "JEV_MESSAGES_REQUIRE_CONFIRMATION",
        ),
        ({"few_shot_examples": [{"input": "x"}]}, "JEV_FEW_SHOT_UNSUPPORTED"),
        ({"ground_truth_few_shot": "x"}, "JEV_FEW_SHOT_UNSUPPORTED"),
        (
            {"eval_type_id": "AgentEvaluator", "agent_mode": "agent"},
            "JEV_AGENT_MODE_UNSUPPORTED",
        ),
        (
            {"eval_type_id": "AgentEvaluator", "agent_mode": "auto"},
            "JEV_AGENT_MODE_UNSUPPORTED",
        ),
        ({"tools": {"search": True}}, "JEV_TOOLS_UNSUPPORTED"),
        ({"knowledge_bases": ["kb"]}, "JEV_KNOWLEDGE_BASE_UNSUPPORTED"),
        ({"knowledge_base_id": "kb"}, "JEV_KNOWLEDGE_BASE_UNSUPPORTED"),
        ({"check_internet": True}, "JEV_INTERNET_UNSUPPORTED"),
        ({"data_injection": {"full_row": True}}, "JEV_DATA_INJECTION_UNSUPPORTED"),
        (
            {"data_injection": {"variablesOnly": False}},
            "JEV_DATA_INJECTION_UNSUPPORTED",
        ),
        ({"input_data_types": {"x": "pdf"}}, "JEV_INPUT_UNSUPPORTED"),
        ({"eval_type_id": "DeterministicEvaluator"}, "JEV_EVAL_TYPE_UNSUPPORTED"),
        ({"eval_type_id": "RankingEvaluator"}, "JEV_EVAL_TYPE_UNSUPPORTED"),
        ({"eval_type_id": "CustomCodeEval"}, "JEV_EVAL_TYPE_UNSUPPORTED"),
        ({"function_eval": True}, "JEV_EVAL_TYPE_UNSUPPORTED"),
    ],
)
def test_r10_c5_to_c17_incompatible_fields(config, code):
    with pytest.raises(JevMappingError, match=code):
        mapping(**config)


def test_r10_c6_c8_c9_c13_compatible_reductions():
    result = mapping(
        eval_type_id="AgentEvaluator",
        agent_mode="quick",
        summary={"type": "verbose"},
        data_injection={"variables_only": True},
        system_prompt="judge",
        messages=[{"role": "user", "content": "context"}],
        jev_mapping={"include_messages": True},
    )
    assert result.question_type == "noul"


@pytest.mark.parametrize(
    "raw",
    [
        {"extra": True},
        {"revision": "v2"},
        {"question_type": "choice"},
        {"pass": {"threshold_source": "template"}},
        {"include_messages": "yes"},
    ],
)
def test_r03_schema_rejects_unknown_or_inconsistent_fields(raw):
    with pytest.raises(JevMappingError, match="JEV_MAPPING_INVALID"):
        mapping(jev_mapping=raw)


def test_r03_revision_stable_and_content_sensitive():
    a = mapping(
        jev_mapping={"pass": {"criteria_true": "good", "criteria_false": "bad"}}
    )
    b = mapping(
        jev_mapping={"pass": {"criteria_false": "bad", "criteria_true": "good"}}
    )
    assert mapping_revision(a) == mapping_revision(b)
    assert mapping_revision(a).startswith("jev-map-v1:")
    assert mapping_revision(a) != mapping_revision(mapping())
    assert mapping_revision(a) == mapping_revision(mapping(jev_mapping=a.to_dict()))


@pytest.mark.parametrize(
    "values,expected",
    [
        ({"x": "plain"}, "plain"),
        ({"x": '[{"role":"user"}]'}, [{"role": "user"}]),
        ({"x": {"a": 1}}, {"a": 1}),
        ({"x": "one", "y": "two"}, {"x": "one", "y": "two"}),
    ],
)
def test_r11_c15_state_shapes(values, expected):
    before = copy.deepcopy(values)
    assert build_state(values) == expected
    assert values == before


def test_r11_size_caps_and_no_truncation(settings):
    payload = {"state": "x" * 120001, "questions": {}}
    with pytest.raises(JevMappingError, match="JEV_INPUT_TOO_LARGE"):
        check_input_size(payload)
    settings.JEV_MAX_INPUT_BYTES = 50
    with pytest.raises(JevMappingError, match="JEV_INPUT_TOO_LARGE"):
        check_input_size({"state": "x" * 40})
    small = {"state": "small", "questions": {}}
    before = copy.deepcopy(small)
    check_input_size(small)
    assert small == before


def test_r10_c8_system_message_alone_needs_no_confirmation():
    assert (
        mapping(messages=[{"role": "system", "content": "Judge"}]).question_type
        == "noul"
    )


def test_r10_c11_reports_all_incompatible_features():
    with pytest.raises(JevMappingError) as error:
        mapping(tools={"search": True}, knowledge_bases=["kb"], check_internet=True)
    assert error.value.details == {
        "tools": ["JEV_TOOLS_UNSUPPORTED"],
        "knowledge_bases": ["JEV_KNOWLEDGE_BASE_UNSUPPORTED"],
        "check_internet": ["JEV_INTERNET_UNSUPPORTED"],
    }
