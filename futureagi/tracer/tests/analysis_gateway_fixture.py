"""Real HTTP bodies and legacy parsers, synthetic auth and no network."""

import json
import runpy
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import httpx

ROOT = Path(__file__).parents[2]
f = runpy.run_path(str(Path(__file__).with_name("analysis_llm_fixture.py")))


def module(name, **values):
    result = ModuleType(name)
    result.__dict__.update(values)
    return result


def jsonable(value):
    if isinstance(value, SimpleNamespace):
        value = vars(value)
    if isinstance(value, dict):
        return {k: jsonable(v) for k, v in value.items() if not callable(v)}
    if isinstance(value, list):
        return [jsonable(v) for v in value]
    return value


def envelope(value, variant):
    data = jsonable(value)
    data.update(id="offline", object="chat.completion", created=1, model="offline")
    for index, choice in enumerate(data["choices"]):
        choice["index"] = index
        choice["message"]["role"] = "assistant"
        for tool in choice["message"].get("tool_calls") or []:
            tool["type"] = "function"
    wire = json.dumps(data)
    if variant == "terminal":
        # Refuse BEFORE even the first draft tool executes, not just at finalization.
        wire = wire.replace(
            '"finish_reason": ', '"finish_reason": "length", "finish_reason": '
        )
    elif variant == "final-terminal":
        wire = wire.replace(
            '"finish_reason": "stop"',
            '"finish_reason": "length", "finish_reason": "stop"',
        )
    elif variant == "arguments":
        wire = wire.replace('"arguments": ', '"arguments": "{}", "arguments": ')
    elif variant == "constant":
        wire = '{"bad":NaN,' + wire[1:]
    elif variant == "trailing":
        wire += "{}"
    return wire


def managed_modules(post, *, cloud=True):
    """Execute real routing/auth checks with only credentials and HTTP replaced."""
    activation = f["compile_definitions"](
        ROOT / "ee/licensing/activation_client.py",
        {"logger": Mock()},
        names={
            "ManagedServiceError",
            "_raise_for_managed_status",
            "dispatch_managed_request",
            "call_managed_service",
        },
    )
    activation.update(
        get_service_token=Mock(
            return_value=SimpleNamespace(
                scope="ee",
                access_token="synthetic",
                gateway_url="https://offline.invalid",
            )
        ),
        require_configured_license_key=Mock(return_value="synthetic"),
        invalidate_token=Mock(),
    )
    managed = f["compile_definitions"](
        ROOT / "ee/licensing/managed_ai.py",
        activation.copy(),
        names={"is_managed_model", "chat_completion", "_cloud_chat_completion"},
    )
    managed["_cloud_gateway_credentials"] = Mock(
        return_value=("https://offline.invalid", "synthetic")
    )
    # Expose real httpx.Response parsing; no library or global transport patch.
    http = module(
        "httpx", **{k: v for k, v in vars(httpx).items() if not k.startswith("__")}
    )
    http.post = post
    if post is not httpx.post:

        class OwnedHTTP:
            # Legacy response-parser fixture only; actual wire tests use httpcore.
            def __init__(self, **options):
                self.options = options

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def post(self, url, **kwargs):
                # Observe the effective request policy, including client defaults.
                kwargs.setdefault(
                    "follow_redirects", self.options.get("follow_redirects", False)
                )
                return post(url, **kwargs)

        http.Client = OwnedHTTP
    import sys

    transport_name = "tracer.services.background_transport"
    transport = sys.modules.get(transport_name)
    if transport is None:
        transport = module(
            "background_transport",
            **runpy.run_path(str(ROOT / "tracer/services/background_transport.py")),
        )
    return {
        transport_name: transport,
        "ee.licensing.activation_client": module("activation_client", **activation),
        "ee.licensing.managed_ai": module("managed_ai", **managed),
        "ee.usage.deployment": module(
            "deployment", DeploymentMode=SimpleNamespace(is_cloud=lambda: cloud)
        ),
        "httpx": http,
    }


def load_gateway(modules):
    """Absent on RED; load actual owned module on GREEN, never emulate it."""
    path = ROOT / "ee/agenthub/traceerroragent/background_gateway.py"
    if path.exists():
        modules["ee.agenthub.traceerroragent.background_gateway"] = module(
            "background_gateway", **runpy.run_path(str(path))
        )
    return modules
