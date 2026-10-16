from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

from asgiref.sync import sync_to_async
from django.db import close_old_connections

T = TypeVar("T")


async def run_in_worker_thread(fn: Callable[..., T], /, *args: Any, **kwargs: Any) -> T:
    def call() -> T:
        close_old_connections()
        try:
            return fn(*args, **kwargs)
        finally:
            close_old_connections()

    return await sync_to_async(call, thread_sensitive=False)()
