"""Run in separately built production/overlay containers; never contacts a provider.

Usage: python prove_skills_boundary.py production|overlay-off|overlay-on
The probe sets E2E_SKILLS_FIXTURES=1 even for production to prove inertness.
"""

import asyncio
import importlib.util
import json
import os
import sys
from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import UUID


def main(mode: str) -> None:
    assert mode in {"production", "overlay-off", "overlay-on"}
    os.environ["DJANGO_SETTINGS_MODULE"] = (
        "tfc.settings.settings" if mode == "production" else "tfc.settings.e2e"
    )
    os.environ["E2E_SKILLS_FIXTURES"] = "0" if mode == "overlay-off" else "1"
    os.environ["SKILLS_VERIFIED_MODELS"] = ""
    os.environ["SKILLS_TESTING_ENABLED"] = "false"
    os.environ["SKILLS_GITHUB_ENABLED"] = "false"
    os.environ["OPENAI_API_BASE"] = "https://unapproved.invalid/v1"
    os.environ["E2E_SKILLS_API_BASE"] = "https://unapproved.invalid/v1"

    import django

    django.setup()

    import litellm
    from django.conf import settings
    from model_hub.services.skills_hub.v11.model_calls import (
        STANDARD_PROVIDER_BASES,
        ResolvedConnection,
        complete_skill_turn,
    )
    from model_hub.services.skills_hub.v11.types import SkillScope

    present = importlib.util.find_spec("tfc.e2e_skills_transport") is not None
    assert present == (mode != "production")
    assert (importlib.util.find_spec("tfc.settings.e2e") is not None) == (
        mode != "production"
    )
    assert settings.SKILLS_TESTING_ENABLED == (mode == "overlay-on")
    assert settings.SKILLS_GITHUB_ENABLED == (mode == "overlay-on")
    assert list(settings.SKILLS_VERIFIED_MODELS) == (
        ["gpt-4o-mini"] if mode == "overlay-on" else []
    )
    assert STANDARD_PROVIDER_BASES["openai"] == "https://api.openai.com/v1"

    transport = AsyncMock(
        return_value=litellm.ModelResponse(
            id="synthetic-boundary",
            model="gpt-4o-mini",
            choices=[
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": "synthetic answer"},
                }
            ],
            usage={"prompt_tokens": 17, "completion_tokens": 9, "total_tokens": 26},
        )
    )
    if mode == "overlay-on":
        from tfc.e2e_skills_transport import fixture_completion

        litellm.acompletion = fixture_completion(transport)
    else:
        litellm.acompletion = transport
    scope = SkillScope(UUID(int=1), UUID(int=2))
    connection = ResolvedConnection(
        scope=scope,
        model_id="gpt-4o-mini",
        provider="openai",
        connection_id=str(UUID(int=3)),
        revision="synthetic",
        context_limit=128000,
        input_price=Decimal("0.00000015"),
        output_price=Decimal("0.0000006"),
        pricing_source="synthetic-boundary",
        api_key="e2e-skills-boundary-0001",
    )
    result = asyncio.run(
        complete_skill_turn(
            connection=connection,
            messages=[{"role": "user", "content": "Synthetic boundary probe"}],
            tools=[],
            parameters={"temperature": 0, "max_output_tokens": 100},
            timeout_ms=5000,
        )
    )
    sent = transport.call_args.kwargs
    assert sent["api_base"] == (
        "http://skills-fakes:8080/v1"
        if mode == "overlay-on"
        else "https://api.openai.com/v1"
    )
    assert sent["num_retries"] == 0
    assert sent["drop_params"] is False
    assert result.assistant_message["content"] == "synthetic answer"
    print(
        json.dumps(
            {
                "mode": mode,
                "adapter_present": present,
                "api_base": sent["api_base"],
                "calls": transport.call_count,
            }
        )
    )


if __name__ == "__main__":
    main(sys.argv[1])
