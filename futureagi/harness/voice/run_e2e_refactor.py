#!/usr/bin/env python
"""End-to-end demo: refactored gym-model SDK -> live FutureAGI platform.

Exercises the post-refactor path (GAP A canon mirror) on real infrastructure:

  1. CONVERSATION sim  - normal chat, real Vertex LLM synthetic-user loop.
  2. TOOL_API sim      - tool-mocking world (config `mock_tools`), a real Vertex
                         model deciding to call the mocked tools.

Both run through the SAME `SimulationRunner` + `WorldKinds` enums, post to the
platform via `FutureAGIResultSink` -> ALK ingestion, and we verify the
TestExecutions land.

RunTest provisioning: a RunTest is a platform object (agent-definition +
scenarios + org/entitlements) — the SDK posts executions into it, it does not
own its creation. `ensure_run_test` resolve-or-creates one by reusing an
existing text agent-definition + its scenarios, then binds a fresh RunTest.

Run (creds loaded before the SDK import — `fi.alk.config` binds FI_BASE_URL at
import time):
    ACCEPTANCE_ENV_FILE=../.env.acceptance \
      .venv/bin/python oss/simulation-acceptance/run_e2e_refactor.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

_ENV = Path(os.environ.get(
    "ACCEPTANCE_ENV_FILE",
    Path(__file__).resolve().parents[2] / ".env.acceptance",
)).expanduser()
for _line in _ENV.read_text().splitlines() if _ENV.exists() else []:
    _line = _line.strip()
    if not _line or _line.startswith("#") or "=" not in _line:
        continue
    _k, _v = _line.split("=", 1)
    os.environ.setdefault(_k.strip(), _v.strip().strip('"'))

import httpx  # noqa: E402
import litellm  # noqa: E402
import fi.alk.simulate as S  # noqa: E402
from fi.simulate.agent.wrapper import AgentInput, AgentResponse  # noqa: E402
from fi.simulate.results import FutureAGIResultSink  # noqa: E402

BASE = os.environ["FI_BASE_URL"].rstrip("/")
HEADERS = {"x-api-key": os.environ["FI_API_KEY"], "x-secret-key": os.environ["FI_SECRET_KEY"]}
MODEL = os.environ.get("DEMO_LLM_MODEL", "vertex_ai/gemini-2.5-flash")


def _unwrap(body):
    if isinstance(body, dict):
        if isinstance(body.get("result"), dict):  # gm.success_response wrapper
            return body["result"]
        return body.get("data", body)
    return body


def _api(client, method, path, **kw):
    r = client.request(method, path, **kw)
    r.raise_for_status()
    return _unwrap(r.json())


def ensure_run_test(name: str, persona: dict) -> str:
    """Provision a chat RunTest + scenario-of-record straight from the SDK
    persona via the ALK ingestion affordance — no pre-existing scenario, no
    async generation. Self-contained on a fresh platform."""
    with httpx.Client(base_url=BASE, headers=HEADERS, timeout=30) as c:
        result = _api(c, "POST", "/simulate/api/alk-simulate/run-tests/provision/",
                      json={"name": name, "personas": [persona]})
        print(f"  run_test: {result['run_test_id']}  "
              f"scenario: {result['scenario_ids']}  "
              f"agent_def: {result['agent_definition_id']}")
        return result["run_test_id"]


def _history(ai: AgentInput, system: str):
    msgs = [{"role": "system", "content": system}]
    for m in ai.messages:
        role = m.get("role")
        if role in ("assistant", "agent"):
            msgs.append({"role": "assistant", "content": m.get("content") or ""})
        elif role == "tool":
            msgs.append({"role": "user", "content": f"[tool result] {m.get('content')}"})
        else:
            msgs.append({"role": "user", "content": m.get("content") or ""})
    return msgs


class LiteLLMAgent:
    def __init__(self, system):
        self.system = system

    async def call(self, ai: AgentInput) -> AgentResponse:
        r = await litellm.acompletion(model=MODEL, messages=_history(ai, self.system),
                                      temperature=0.3, max_tokens=800)
        return AgentResponse(content=r["choices"][0]["message"]["content"])


class ToolLLMAgent:
    def __init__(self, system):
        self.system = system

    async def call(self, ai: AgentInput) -> AgentResponse:
        r = await litellm.acompletion(model=MODEL, messages=_history(ai, self.system),
                                      tools=ai.tools or None, tool_choice="auto", max_tokens=800)
        msg = r["choices"][0]["message"]
        calls = []
        for tc in msg.get("tool_calls") or []:
            fn = tc["function"]
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except Exception:
                args = {}
            calls.append({"id": tc.get("id") or fn["name"], "name": fn["name"], "arguments": args})
        return AgentResponse(content=msg.get("content") or "", tool_calls=calls or None)


def _sink(run_test_id):
    return FutureAGIResultSink(root="/tmp/fagi-e2e-runs", run_test_id=run_test_id)


async def conversation_sim(run_test_id):
    spec = S.SimulationSpec(
        run_id="e2e_conversation",
        environment=S.EnvironmentSpec(adapter=S.EnvironmentAdapters.CHAT,
                                      world_kind=S.WorldKinds.CONVERSATION,
                                      config={"max_turns": 4, "min_turns": 2, "modality": "text"}),
        target=S.AgentEndpointSpec(adapter=S.TargetAdapters.CALLABLE),
        simulator=S.SimulatorPolicySpec(adapter=S.SimulatorAdapters.SYNTHETIC_USER),
        scenario=S.Scenario(name="late-delivery", dataset=[
            S.Persona(persona={"name": "Morgan", "role": "customer"},
                      situation="A delivery is 3 days late; ask for status and a concrete ETA.",
                      outcome="Get a clear status and a next step.")]),
    )
    agent = LiteLLMAgent("You are a concise delivery-support agent. Acknowledge, give status, offer next step.")
    return await S.SimulationRunner().run(spec, target=agent, result_sink=_sink(run_test_id))


async def tool_api_sim(run_test_id):
    tool_schemas = [
        {"type": "function", "function": {"name": "lookup_order",
            "description": "Look up an order and its refund eligibility by id.",
            "parameters": {"type": "object", "properties": {"order_id": {"type": "string"}},
                           "required": ["order_id"]}}},
        {"type": "function", "function": {"name": "approve_refund",
            "description": "Approve a refund for an order id.",
            "parameters": {"type": "object", "properties": {"order_id": {"type": "string"}},
                           "required": ["order_id"]}}},
    ]
    spec = S.SimulationSpec(
        run_id="e2e_tool_api",
        environment=S.EnvironmentSpec(adapter=S.EnvironmentAdapters.CHAT,
                                      world_kind=S.WorldKinds.TOOL_API,
                                      config={
                                          "max_turns": 5, "min_turns": 2, "modality": "text",
                                          "tool_schemas": tool_schemas,
                                          "mock_tools": {
                                              "lookup_order": {"content": "order A1: eligible for refund, amount $42"},
                                              "approve_refund": {"content": "refund approved",
                                                                 "state_updates": {"refund": {"status": "approved"}}},
                                          },
                                      }),
        target=S.AgentEndpointSpec(adapter=S.TargetAdapters.CALLABLE),
        simulator=S.SimulatorPolicySpec(adapter=S.SimulatorAdapters.SYNTHETIC_USER),
        scenario=S.Scenario(name="refund", dataset=[
            S.Persona(persona={"name": "Sam", "role": "customer"},
                      situation="My order A1 arrived damaged. I want a refund.",
                      outcome="The refund is approved via the tools.")]),
    )
    agent = ToolLLMAgent("You are a refund agent. Use lookup_order to check eligibility, then approve_refund. "
                         "Do not claim a refund is done until approve_refund has been called.")
    return await S.SimulationRunner().run(spec, target=agent, result_sink=_sink(run_test_id))


def verify(run_test_id, timeout_s=120):
    """Poll for the two Completed TestExecutions, then best-effort CSAT from the
    eval-summary endpoint (CSAT is recomputed async by the platform)."""
    deadline = time.time() + timeout_s
    with httpx.Client(base_url=BASE, headers=HEADERS, timeout=30) as c:
        execs = []
        while time.time() < deadline:
            body = _api(c, "GET", f"/simulate/run-tests/{run_test_id}/executions/")
            execs = body.get("results", body) if isinstance(body, dict) else body
            if sum(str(e.get("status")).lower() == "completed" for e in execs) >= 2:
                break
            time.sleep(6)
        try:
            csat = _api(c, "GET", f"/simulate/run-tests/{run_test_id}/eval-summary/")
        except Exception:
            csat = None
        return execs, csat


async def _run_both(run_test_id):
    print("\n[1/2] CONVERSATION sim -> platform")
    r1 = await conversation_sim(run_test_id)
    print("     status:", r1.status)
    print("[2/2] TOOL_API sim (mock tools) -> platform")
    r2 = await tool_api_sim(run_test_id)
    tr = r2.test_cases[0].result.transcript
    print("     status:", r2.status, " tool mock hit:",
          ("refund approved" in tr or "eligible for refund" in tr))
    return r1, r2


def main() -> int:
    print("MODEL:", MODEL, " BASE:", BASE)
    print("provisioning run test...")
    persona = {"name": "Morgan",
               "situation": "A delivery is 3 days late; ask for status and a concrete ETA.",
               "outcome": "Get a clear status and a next step."}
    run_test_id = ensure_run_test(f"e2e-refactor-{int(time.time())}", persona)

    r1, r2 = asyncio.run(_run_both(run_test_id))

    print("\nverifying TestExecutions landed...")
    execs, csat = verify(run_test_id)
    for e in execs:
        print(f"  TE {e.get('id')}  status={e.get('status')}  "
              f"chats={e.get('total_chats')}  turns={e.get('total_number_of_fagi_agent_turns')}  "
              f"success_rate={e.get('success_rate')}")
    if csat is not None:
        print("  eval-summary:", json.dumps(csat)[:300])

    ok = (r1.status.value == "completed" and r2.status.value == "completed"
          and sum(str(e.get("status")).lower() == "completed" for e in execs) >= 2)
    print("\n" + json.dumps({
        "status": "passed" if ok else "failed",
        "run_test_id": run_test_id,
        "conversation": r1.status.value,
        "tool_api": r2.status.value,
        "test_executions_completed": sum(
            str(e.get("status")).lower() == "completed" for e in execs),
        "ui": f"{BASE}/simulate/run-tests/{run_test_id}",
    }, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
