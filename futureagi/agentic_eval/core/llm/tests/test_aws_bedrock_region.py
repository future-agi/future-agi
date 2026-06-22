"""Region resolution for the aws_bedrock provider."""

from unittest.mock import patch


def _make_bedrock_llm():
    from agentic_eval.core.llm.llm import LLM

    return LLM(provider="aws_bedrock", model_name="anthropic.claude-3-sonnet")


@patch("agentic_eval.core.llm.llm.boto3")
def test_aws_bedrock_region_from_env(mock_boto3, monkeypatch):
    monkeypatch.setenv("AWS_BEDROCK_REGION", "eu-central-1")

    llm = _make_bedrock_llm()

    assert llm.provider_api_keys["aws_bedrock"]["aws_region"] == "eu-central-1"
    _, kwargs = mock_boto3.Session.call_args
    assert kwargs["region_name"] == "eu-central-1"


@patch("agentic_eval.core.llm.llm.boto3")
def test_aws_bedrock_region_defaults_to_us_west_2(mock_boto3, monkeypatch):
    monkeypatch.delenv("AWS_BEDROCK_REGION", raising=False)

    llm = _make_bedrock_llm()

    assert llm.provider_api_keys["aws_bedrock"]["aws_region"] == "us-west-2"
