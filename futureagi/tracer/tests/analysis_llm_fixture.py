"""Actual retry definitions with synthetic, socket-free provider boundaries."""

import ast
import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[2]
LLM_PATH = ROOT / "agentic_eval/core/llm/llm.py"
ADAPTER_PATH = ROOT / "ee/agenthub/traceerroragent/background_llm.py"


def compile_definitions(path, namespace, class_name=None, names=None):
    tree = ast.parse(path.read_text())
    if class_name:
        tree.body = next(
            node.body
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == class_name
        )
    tree.body = [
        node
        for node in tree.body
        if not isinstance(node, (ast.Import, ast.ImportFrom))
        and (names is None or getattr(node, "name", None) in names)
    ]
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            node.decorator_list = [
                decorator
                for decorator in node.decorator_list
                if not (
                    isinstance(decorator, ast.Name)
                    and decorator.id == "async_log_llm_usage"
                )
            ]
    tree.body.insert(
        0,
        ast.ImportFrom(
            module="__future__", names=[ast.alias(name="annotations")], level=0
        ),
    )
    exec(compile(ast.fix_missing_locations(tree), str(path), "exec"), namespace)
    return namespace


def response(content="complete", reason="stop"):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason=reason, message=SimpleNamespace(content=content)
            )
        ]
    )


def clients(send):
    """Base uses real retries; the optional adapter inherits that exact base."""
    transport = SimpleNamespace(completion=lambda **kwargs: send("direct", kwargs))
    namespace = {
        "asyncio": asyncio,
        "SimpleNamespace": SimpleNamespace,
        "time": SimpleNamespace(monotonic=time.monotonic, sleep=lambda _: None),
        "logger": Mock(),
        "litellm": transport,
        "MAX_RETRIES": 3,
        "RETRY_DELAY": 0,
        "LITELLM_REQUEST_TIMEOUT": 1,
        "LITELLM_NUM_RETRIES": 0,
        "LITELLM_RETRY_STRATEGY": "constant",
        "preprocess_messages_for_provider": lambda messages, provider: messages,
    }
    names = {
        "_get_completion_with_tools",
        "_get_completion_content",
        "_try_gateway_completion",
        "_try_gateway_completion_async",
        "_get_completion_content_async",
        "_handle_final_fallback",
        "_handle_final_fallback_async",
        "_set_last_finish_reason_from_response",
        "_build_gateway_request_kwargs",
        "_to_openai_like_response",
        "_update_token_usage",
    }
    methods = compile_definitions(LLM_PATH, namespace, "LLM", names)
    base = type("LLM", (), {name: methods[name] for name in names})

    def initialize(self, **kwargs):
        self.__dict__.update(kwargs)
        self.api_key = None
        self.org_id = None
        self.last_finish_reason = None
        self.token_usage = {
            "total_tokens": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
        }
        self.cost = {}
        self.GATEWAY_MAX_ATTEMPTS = 3
        self.GATEWAY_RETRY_BACKOFF = (0, 0)
        self._gateway_client = None
        self._async_gateway_client = None

    base.__init__ = initialize
    base._requires_managed_transport = lambda *args: False
    base._is_license_denial = lambda *args: False
    base._require_retained_gateway_access = lambda *args: None
    base._should_try_alternate_format = lambda *args: False
    base._try_managed_ai_completion = lambda *args, **kwargs: None
    base._prepare_completion_payload = lambda self, messages, *args, **kwargs: {
        "model": self.model_name,
        "messages": messages,
        "temperature": self.temperature,
        "max_tokens": self.max_tokens,
    }
    base._update_cost = lambda *args: None
    witness_ns = compile_definitions(
        ROOT / "tracer/services/analysis_result_completion.py", {}
    )

    def direct_completion(*, provider, payload, **kwargs):
        result = send("direct", payload)
        if provider == "openai":
            return result
        choices = result.choices
        if len(choices) != 1:
            return {}
        choice = choices[0]
        blocks = [
            {"type": "text", "text": getattr(choice.message, "content", None) or ""}
        ]
        for tool in getattr(choice.message, "tool_calls", None) or []:
            blocks.append(
                {
                    "type": "tool_use",
                    "id": tool.id,
                    "name": tool.function.name,
                    "input": json.loads(tool.function.arguments),
                }
            )
        return {
            "stop_reason": {"stop": "end_turn", "tool_calls": "tool_use"}.get(
                choice.finish_reason, choice.finish_reason
            ),
            "content": blocks,
        }

    provider_ns = (
        compile_definitions(
            ROOT / "ee/agenthub/traceerroragent/background_provider.py",
            {
                "json": json,
                "SimpleNamespace": SimpleNamespace,
                "IncompleteAnalysis": witness_ns["IncompleteAnalysis"],
            },
        )
        if (ROOT / "ee/agenthub/traceerroragent/background_provider.py").exists()
        else {}
    )
    adapter_ns = dict(
        namespace,
        direct_completion=direct_completion,
        normalize_response=provider_ns.get("normalize_response"),
        provider_dialect=provider_ns.get("provider_dialect"),
        response_object=provider_ns.get("response_object"),
        LLM=base,
        **{
            name: witness_ns[name]
            for name in (
                "IncompleteAnalysis",
                "NORMAL_FINISHES",
                "INTERMEDIATE_FINISHES",
                "_field",
            )
        },
    )
    if ADAPTER_PATH.exists():
        compile_definitions(ADAPTER_PATH, adapter_ns)
    return base, adapter_ns, witness_ns


def bedrock_double(**methods):
    """Old parser-only seam; new wire suites run the installed HTTP stack."""
    from botocore.hooks import HierarchicalEmitter
    from botocore.httpsession import URLLib3Session

    session = URLLib3Session()
    return SimpleNamespace(
        **methods,
        _endpoint=SimpleNamespace(http_session=session),
        meta=SimpleNamespace(events=HierarchicalEmitter()),
        close=session.close,
    )


class ScopedGateway(SimpleNamespace):
    def with_options(self, **kwargs):
        return ScopedGateway(
            **{
                **vars(self),
                "_client": kwargs.get("http_client"),
                "max_retries": kwargs.get("max_retries", 0),
            }
        )


class Gateway:
    """SDK-boundary double: retry disabling must be explicit and instance-local."""

    def __init__(self, send):
        self.send = send
        self.options = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def with_options(self, **kwargs):
        self.options.append(kwargs)
        return ScopedGateway(
            chat=SimpleNamespace(
                completions=SimpleNamespace(
                    with_raw_response=SimpleNamespace(create=self.raw_create)
                )
            )
        )

    def raw_create(self, **kwargs):
        import httpx

        def encode(value):
            return {k: v for k, v in vars(value).items() if not callable(v)}

        wire = json.dumps(self.send("gateway", kwargs), default=encode)
        return SimpleNamespace(
            http_response=httpx.Response(
                200, text=wire, request=httpx.Request("POST", "https://offline.invalid")
            )
        )

    def create(self, **kwargs):
        return self.send("gateway", kwargs)
