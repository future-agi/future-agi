"""Regression tests for AWS Bedrock region resolution in LLM._init_client.

The ``aws_bedrock`` provider must honor the ``AWS_BEDROCK_REGION`` environment
variable the same way its sibling ``aws_bedrock_anthropic`` already does,
instead of always pinning the client to ``us-west-2``.
"""

import os
from unittest.mock import MagicMock, patch

from agentic_eval.core.llm import llm as llm_module
from agentic_eval.core.llm.llm import LLM


def _build_bedrock_llm():
    """Run the real _init_client so provider_api_keys/region resolution runs."""
    llm = LLM.__new__(LLM)
    LLM.__init__(llm, provider="aws_bedrock", model_name="anthropic.claude-3-5-sonnet")
    return llm


class TestBedrockRegionResolution:
    def test_aws_bedrock_honors_region_env(self):
        """A user-set AWS_BEDROCK_REGION must reach the bedrock-runtime client."""
        with patch.dict(os.environ, {"AWS_BEDROCK_REGION": "eu-central-1"}):
            with patch.object(llm_module.boto3, "Session") as mock_session:
                mock_session.return_value.client.return_value = MagicMock()
                llm = _build_bedrock_llm()

        assert llm.provider_api_keys["aws_bedrock"]["aws_region"] == "eu-central-1"
        # the resolved region is what actually configures the boto3 session
        assert mock_session.call_args.kwargs["region_name"] == "eu-central-1"

    def test_aws_bedrock_defaults_to_us_west_2(self):
        """Without the env var the historical us-west-2 default is preserved."""
        env_without_region = {
            k: v for k, v in os.environ.items() if k != "AWS_BEDROCK_REGION"
        }
        with patch.dict(os.environ, env_without_region, clear=True):
            with patch.object(llm_module.boto3, "Session") as mock_session:
                mock_session.return_value.client.return_value = MagicMock()
                llm = _build_bedrock_llm()

        assert llm.provider_api_keys["aws_bedrock"]["aws_region"] == "us-west-2"

    def test_aws_bedrock_matches_anthropic_sibling(self):
        """Both Bedrock providers resolve region from the same env var."""
        with patch.dict(os.environ, {"AWS_BEDROCK_REGION": "ap-northeast-2"}):
            with patch.object(llm_module.boto3, "Session") as mock_session:
                mock_session.return_value.client.return_value = MagicMock()
                llm = _build_bedrock_llm()

        assert (
            llm.provider_api_keys["aws_bedrock"]["aws_region"]
            == llm.provider_api_keys["aws_bedrock_anthropic"]["aws_region"]
            == "ap-northeast-2"
        )
