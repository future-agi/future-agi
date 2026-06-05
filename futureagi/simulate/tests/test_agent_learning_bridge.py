"""Tests for the agent-learning-kit red-team bridge (TH-5642).

Two layers:
- Pure mapping tests that run with the kit mocked (no install needed).
- A real end-to-end test that builds a manifest and dry-runs it against the actually
  installed ``agent_learning`` kit in this runtime — the genuine integration check.
"""

import pytest

from simulate.services import agent_learning_bridge as bridge


# --------------------------------------------------------------------------- #
# Pure mapping (kit mocked)                                                    #
# --------------------------------------------------------------------------- #
class _FakeRedteam:
    def __init__(self):
        self.calls = []

    def build_redteam_manifest(self, **kwargs):
        self.calls.append(kwargs)
        return {"version": "redteam/v1", **kwargs}


@pytest.mark.unit
def test_category_to_attack_mapping(monkeypatch):
    fake = _FakeRedteam()
    monkeypatch.setattr(bridge, "_redteam_module", lambda: fake)

    man = bridge.build_redteam_manifest_for_agent(
        name="acme-support",
        agent_description="A banking support agent",
        categories=["pii_extraction", "jailbreak", "hallucination_pressure"],
        channel="chat",
    )
    attacks = fake.calls[0]["attacks"]
    # pii_extraction -> data_exfiltration (canonical), others mapped/passed through.
    assert attacks == ["data_exfiltration", "jailbreak", "hallucination"]
    assert man["version"] == "redteam/v1"
    # Scenario carries our personas + safety objectives, not kit defaults.
    scenario = fake.calls[0]["scenario"]
    assert len(scenario["adversary_personas"]) == 3
    assert scenario["objectives"]
    assert all("banking" in p["prompt"] for p in scenario["adversary_personas"])


@pytest.mark.unit
def test_voice_channel_uses_more_surfaces(monkeypatch):
    fake = _FakeRedteam()
    monkeypatch.setattr(bridge, "_redteam_module", lambda: fake)
    bridge.build_redteam_manifest_for_agent(
        name="v", agent_description="voice agent",
        categories=["jailbreak"], channel="voice",
    )
    assert "memory" in fake.calls[0]["surfaces"]


@pytest.mark.unit
def test_scripted_agent_is_credential_free_path(monkeypatch):
    fake = _FakeRedteam()
    monkeypatch.setattr(bridge, "_redteam_module", lambda: fake)
    bridge.build_redteam_manifest_for_agent(
        name="v", agent_description="agent", categories=["jailbreak"],
        scripted_responses=["I can't help with that."],
    )
    agent = fake.calls[0]["agent"]
    assert agent["type"] == "scripted"
    assert agent["responses"][0]["content"] == "I can't help with that."


@pytest.mark.unit
def test_explicit_agent_spec_passthrough(monkeypatch):
    fake = _FakeRedteam()
    monkeypatch.setattr(bridge, "_redteam_module", lambda: fake)
    bridge.build_redteam_manifest_for_agent(
        name="v", agent_description="agent", categories=["jailbreak"],
        agent_spec={"type": "command", "command": ["python", "agent.py"]},
    )
    assert fake.calls[0]["agent"] == {"type": "command", "command": ["python", "agent.py"]}


@pytest.mark.unit
def test_llm_agent_default_from_instructions(monkeypatch):
    fake = _FakeRedteam()
    monkeypatch.setattr(bridge, "_redteam_module", lambda: fake)
    bridge.build_redteam_manifest_for_agent(
        name="v", agent_description="agent", categories=["jailbreak"],
        instructions="You are a strict policy bot.",
    )
    agent = fake.calls[0]["agent"]
    assert agent["type"] == "llm"
    assert agent["instructions"] == "You are a strict policy bot."


@pytest.mark.unit
def test_unknown_categories_raise(monkeypatch):
    fake = _FakeRedteam()
    monkeypatch.setattr(bridge, "_redteam_module", lambda: fake)
    with pytest.raises(ValueError):
        bridge.build_redteam_manifest_for_agent(
            name="v", agent_description="agent", categories=["nope"],
        )


@pytest.mark.unit
def test_run_in_event_loop_raises(monkeypatch):
    import asyncio

    async def main():
        with pytest.raises(RuntimeError):
            bridge._run_async(_noop())

    async def _noop():
        return None

    asyncio.run(main())


# --------------------------------------------------------------------------- #
# Real kit, end-to-end (skips cleanly if the kit is not installed)            #
# --------------------------------------------------------------------------- #
@pytest.mark.integration
@pytest.mark.skipif(not bridge.is_available(), reason="agent-learning-kit not installed")
def test_real_redteam_dry_run_end_to_end():
    """Build from our adversarial catalogue and dry-run via the real kit."""
    result = bridge.run_redteam_for_agent(
        name="acme-support-redteam",
        agent_description="A banking support agent",
        categories=["prompt_injection", "jailbreak", "pii_extraction"],
        channel="chat",
        scripted_responses=["I can only help with your own account after verification."],
        min_turns=1,
        max_turns=1,
        dry_run=True,
    )
    assert result["status"] in {"passed", "failed"}
    assert result["name"] == "acme-support-redteam"
    assert "redteam" in result
    assert result.get("dry_run") is True
