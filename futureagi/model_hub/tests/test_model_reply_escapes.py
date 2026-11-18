"""Escaped text survives the two response paths that extract embedded objects."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agentic_eval.core.llm.llm import LLM
from agentic_eval.core_evals.fi_evals.llm.custom_prompt_evaluator.evaluator import (
    CustomPromptEvaluator,
)
from agentic_eval.core_evals.run_prompt.litellm_response import RunPrompt


@pytest.mark.parametrize(
    ("prefix", "indent", "suffix"),
    [
        ("", "", ""),
        ("", r"\r", ""),
        ("Using {rubric}, here's the verdict: ", r"\t", ""),
        ("Using {reviewer's notes}: ", r"\r", " Evidence: {}"),
        ("", "", " Evidence: {}"),
        ('Template: {{"result":"Fail","explanation":"example"}}. Answer: ', "", ""),
        ('Using {rubric: {"scale":"binary"}}, here is the answer: ', "", ""),
        ("Using {'reviewer's notes'} to grade: ", r"\t", ""),
        ("Using {'scale':'binary'} to grade: ", "", ""),
        ("""Using {"rubric": reviewer's scale} to grade: """, r"\t", ""),
    ],
    ids=[
        "plain",
        "indent",
        "apostrophe-before-object",
        "quoted-prose",
        "another-object-after",
        "wrapped-example",
        "nested-rubric",
        "quoted-placeholder",
        "python-style-rubric",
        "apostrophe-in-rubric-value",
    ],
)
def test_custom_judge_preserves_explanation(monkeypatch, prefix, indent, suffix):
    reason = "left\tright\r\n" + r"C:\temp\report.txt"
    obj = json.dumps({"result": "Pass", "explanation": reason})
    reply = prefix + "{" + indent + obj[1:] + suffix
    monkeypatch.setattr(LLM, "_init_client", lambda *_: None)
    judge = CustomPromptEvaluator(
        rule_prompt="Evaluate {{output}}", model="gpt-4o", provider="openai"
    )
    monkeypatch.setattr(judge, "call_llm", Mock(return_value=reply))

    result = judge.run(required_keys=["output"], output="sample").eval_results[0]

    assert result["data"]["result"] == "Pass"
    assert result["reason"] == reason
    assert json.loads(result["metadata"])["explanation"] == reason


@pytest.mark.parametrize(
    ("prefix", "suffix"),
    [
        ("Result: ", ""),
        ("Result: ", ' Evidence: {"notes": []}'),
        ('Template: {{"notes":["example"]}}. Answer: ', ""),
        ('Using {rubric: {"scale":"binary"}}, here is the answer: ', ""),
    ],
    ids=["plain", "evidence", "wrapped-example", "nested-rubric"],
)
def test_legacy_prompt_object_preserves_nested_strings(prefix, suffix):
    expected = {"notes": ["left\tright", r"\text{answer}", 'say "}"']}
    message = SimpleNamespace(content=f"{prefix}{json.dumps(expected)}{suffix}")
    response = SimpleNamespace(choices=[SimpleNamespace(message=message)])
    prompt = RunPrompt(
        model="gpt-4o",
        messages=[],
        organization_id=None,
        output_format="object",
        temperature=None,
        frequency_penalty=None,
        presence_penalty=None,
        max_tokens=None,
        top_p=None,
        response_format=None,
        tool_choice=None,
        tools=None,
    )

    assert prompt.get_formatted_output(response) == expected
