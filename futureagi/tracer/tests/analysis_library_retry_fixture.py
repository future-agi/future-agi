"""Installed LiteLLM retry policy, executed verbatim with offline boundaries."""

import ast
import asyncio
import contextvars
import datetime
import functools
import sys
import traceback
import uuid
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

SITE = (
    Path(__file__).resolve().parents[2] / ".venv/lib/python3.12/site-packages/litellm"
)


def extract(path, name, ns):
    node = next(
        n for n in ast.parse(path.read_text()).body if getattr(n, "name", None) == name
    )
    node.decorator_list = []
    tree = ast.Module(
        body=[
            ast.ImportFrom(
                module="__future__", names=[ast.alias(name="annotations")], level=0
            ),
            node,
        ],
        type_ignores=[],
    )
    exec(compile(ast.fix_missing_locations(tree), str(path), "exec"), ns)
    return ns[name]


def real_retry_wrapper(boundary, transport, retry_count):
    # This is a private double, NOT the process-global LiteLLM module.
    module = ModuleType("offline_litellm_utils")
    module.Rules = lambda: object()
    module.get_coroutine_checker = lambda: SimpleNamespace(
        is_async_callable=lambda _: False
    )
    module.executor = SimpleNamespace(submit=lambda *a, **k: None)
    module.update_response_metadata = lambda **k: None
    sys.modules[module.__name__] = module
    transport.num_retries = retry_count
    transport.num_retries_per_request = None
    transport.max_budget = None
    transport.cache = None
    transport.modify_params = False
    transport.exceptions = SimpleNamespace(
        ContextWindowExceededError=type("ContextError", (Exception,), {})
    )
    ns = {
        "__name__": module.__name__,
        "sys": sys,
        "wraps": functools.wraps,
        "litellm": transport,
        "asyncio": asyncio,
        "datetime": datetime,
        "uuid": uuid,
        "traceback": traceback,
        "contextvars": contextvars,
        "openai": SimpleNamespace(
            APIError=RuntimeError,
            Timeout=TimeoutError,
            APIConnectionError=ConnectionError,
        ),
        "CallTypes": SimpleNamespace(
            completion=SimpleNamespace(value="completion"),
            responses=SimpleNamespace(value="responses"),
        ),
        "_is_async_request": lambda k: False,
        "_is_streaming_request": lambda **k: False,
        "print_args_passed_to_litellm": lambda *a: None,
        "function_setup": lambda *a, **k: (Mock(), k),
        "load_credentials_from_list": lambda k: None,
        "_get_cached_llm_caching_handler": lambda: lambda **k: Mock(),
        "print_verbose": lambda *a: None,
        "verbose_logger": Mock(),
        "post_call_processing": lambda **k: None,
    }
    decorate = extract(SITE / "utils.py", "client", ns)

    def completion(**kwargs):
        return boundary(**kwargs)

    wrapped = decorate(completion)
    transport.completion_with_retries = extract(
        SITE / "main.py", "completion_with_retries", {"completion": wrapped}
    )
    return wrapped
