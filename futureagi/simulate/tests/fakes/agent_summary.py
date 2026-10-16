from __future__ import annotations

import uuid
from dataclasses import replace
from typing import Any

from simulate.services.agent_setup import AgentSummary


def make_agent_summary(**overrides: Any) -> AgentSummary:
    summary = AgentSummary(
        agent_definition_id=uuid.uuid4(),
        agent_version_id=uuid.uuid4(),
        agent_stored=True,
        setup_stale=False,
        provider="vapi",
        provider_agent_id="asst_test",
        credential_id=uuid.uuid4(),
        key_present=True,
        type="voice",
        kind="hosted",
        unit_ids=("main",),
        setup_hash="sh1:" + "0" * 64,
        provider_info_complete=True,
        missing=(),
    )
    if "missing" in overrides and "provider_info_complete" not in overrides:
        overrides["provider_info_complete"] = not overrides["missing"]
    return replace(summary, **overrides)
