# ruff: noqa: E402
"""TH-5467 batch-A verification: TH-5376, TH-5373, TH-5399, TH-5403, TH-5387, TH-5406.

Static/headless checks that each fix is wired correctly. Full end-to-end for
TH-5376 (attach eval configs to a run test) needs live simulation data and is
verified separately. Run in ws1-backend:
    python -m ai_tools.tests.verify_batch_a
"""

import os

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tfc.settings.settings")
django.setup()

from ai_tools.registry import registry


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    return bool(cond)


results = []

# ---- TH-5406: alert monitor metric_type enum + docs ----
mt = registry.get("create_alert_monitor").input_schema["properties"]["metric_type"]
results.append(check("TH-5406 metric_type enum present", len(mt.get("enum", [])) == 11))
results.append(
    check(
        "TH-5406 metric_type documented",
        "count_of_errors" in mt.get("description", ""),
        mt.get("description", "")[:60],
    )
)

# ---- TH-5399: run_test_id surfaced in outputs ----
import inspect

from ai_tools.tools.agents.get_test_execution import GetTestExecutionTool
from ai_tools.tools.agents.list_test_executions import ListTestExecutionsTool
from ai_tools.tools.simulation.get_test_execution_analytics import (
    GetTestExecutionAnalyticsTool,
)

for cls in (GetTestExecutionTool, ListTestExecutionsTool, GetTestExecutionAnalyticsTool):
    src = inspect.getsource(cls)
    results.append(check(f"TH-5399 run_test_id in {cls.__name__}", "run_test_id" in src))

# ---- TH-5403: session_id resolves gen_ai.conversation.id ----
from tracer.utils.semantic_conventions import get_attribute

r1 = get_attribute({"gen_ai.conversation.id": "conv-1"}, "session_id")
r2 = get_attribute({"session.id": "sess-1"}, "session_id")
results.append(check("TH-5403 gen_ai.conversation.id -> session", r1 == "conv-1", str(r1)))
results.append(check("TH-5403 session.id still works", r2 == "sess-1", str(r2)))

# ---- TH-5387: list_personas exposes type/simulation_type filters ----
lp = registry.get("list_personas").input_schema["properties"]
results.append(check("TH-5387 list_personas has 'type'", "type" in lp))
results.append(check("TH-5387 list_personas has 'simulation_type'", "simulation_type" in lp))

# ---- TH-5373: phone number with space accepted ----
from simulate.serializers.agent_definition import AgentDefinitionSerializer
from simulate.serializers.requests.agent_definition import (
    AgentDefinitionCreateRequestSerializer,
)

def phone_ok(serializer_cls, data):
    s = serializer_cls(data=data)
    s.is_valid()
    # fix is correct if contact_number is NOT among the errors
    return "contact_number" not in s.errors

base = {
    "agent_name": "TH5373 Agent",
    "agent_type": "voice",
    "provider": "vapi",
    "inbound": True,
    "description": "x",
    "languages": ["en"],
    "contact_number": "+1 5598887142",
}
results.append(
    check(
        "TH-5373 create-request serializer accepts spaced phone",
        phone_ok(AgentDefinitionCreateRequestSerializer, dict(base)),
    )
)
results.append(
    check(
        "TH-5373 model serializer accepts spaced phone",
        phone_ok(AgentDefinitionSerializer, dict(base)),
    )
)

# ---- TH-5376: code path now attaches eval configs (static check) ----
import inspect as _inspect

from simulate.views import run_test as _rt

src = _inspect.getsource(_rt.CreateRunTestView)
results.append(
    check(
        "TH-5376 eval_config_ids attached (.update present)",
        "id__in=eval_config_ids" in src
        and ".update(run_test=run_test)" in src.split("eval_config_ids", 2)[-1][:400],
    )
)

print("\nSUMMARY:", sum(results), "/", len(results), "checks passed")
