"""Django Channels layer selection, and a process-local layer that is safe to
call from any thread.

Which layer
-----------
Every ``group_send`` runs in the web (Granian) process:

* ``tfc/views/socket.py`` CallWebsocketView. Temporal workers relay to it over
  HTTP via ``model_hub.utils.call_websocket`` -> ``WEBSOCKET_ENDPOINT``;
* ``model_hub/views/annotation_queues.py`` discussion broadcasts;
* ``sockets/consumer.py`` DataConsumer.handle_message.

Simulation updates bypass the layer (raw Redis pub/sub,
``sockets/simulation_consumer.py``). A process-local layer is therefore correct
whenever exactly one web process serves WebSockets, and no broker is needed.
``CHANNEL_LAYER_BACKEND`` picks the layer; see ``channel_layer_settings``.

Why the in-memory layer is subclassed
-------------------------------------
``channels.layers.InMemoryChannelLayer`` keeps ``asyncio.Queue`` objects and
plain dicts that may only be touched from the event loop that awaits
``receive()``. The backend reaches ``group_send`` from three places:

* WebSocket consumers, on Granian's worker loop;
* sync DRF views, through ``async_to_sync``, which asgiref routes back onto
  that same loop;
* plain ``threading.Thread`` callbacks (the annotation discussion
  broadcaster), through ``async_to_sync``, which runs on a *new* loop in a pool
  thread. Calling ``Queue.put_nowait`` from there wakes the consumer's future
  from the wrong thread, which is not thread-safe and can lose the wake-up.

The subclass pins all state to a "home" loop. The home loop is the first loop
that creates or receives on a channel, which is Granian's worker loop. Calls
made from any other loop are sent to the home loop with
``run_coroutine_threadsafe``. If no consumer has connected yet, there is no
home loop and no receivers either, so the call runs locally and does nothing.
"""

from __future__ import annotations

import asyncio
import importlib.util
import threading
from collections.abc import Mapping
from urllib.parse import urlsplit, urlunsplit

from channels.layers import InMemoryChannelLayer
from django.core.exceptions import ImproperlyConfigured

# A send from a foreign thread waits at most this long for the home loop.
_CROSS_LOOP_TIMEOUT_S = 10.0


class ThreadSafeInMemoryChannelLayer(InMemoryChannelLayer):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._home_loop: asyncio.AbstractEventLoop | None = None
        self._home_lock = threading.Lock()

    # -- loop pinning ---------------------------------------------------------

    def _claim_home(self) -> None:
        loop = asyncio.get_running_loop()
        home = self._home_loop
        if home is loop:
            return
        with self._home_lock:
            if self._home_loop is None or self._home_loop.is_closed():
                self._home_loop = loop

    async def _on_home(self, fn, *args):
        home = self._home_loop
        loop = asyncio.get_running_loop()
        if home is None or home is loop or home.is_closed() or not home.is_running():
            return await fn(*args)
        fut = asyncio.run_coroutine_threadsafe(fn(*args), home)
        return await asyncio.wait_for(
            asyncio.wrap_future(fut), timeout=_CROSS_LOOP_TIMEOUT_S
        )

    # -- channel layer API ----------------------------------------------------

    async def new_channel(self, prefix="specific."):
        self._claim_home()
        return await super().new_channel(prefix)

    async def receive(self, channel):
        self._claim_home()
        return await super().receive(channel)

    async def send(self, channel, message):
        return await self._on_home(super().send, channel, message)

    async def group_add(self, group, channel):
        return await self._on_home(super().group_add, group, channel)

    async def group_discard(self, group, channel):
        return await self._on_home(super().group_discard, group, channel)

    async def group_send(self, group, message):
        return await self._on_home(super().group_send, group, message)

    async def flush(self):
        return await self._on_home(super().flush)


# -- settings -----------------------------------------------------------------


def _env_int(env: Mapping[str, str], name: str, default: int) -> int:
    try:
        return int(env.get(name) or default)
    except ValueError:
        return default


def _channels_rabbitmq_installed() -> bool:
    return importlib.util.find_spec("channels_rabbitmq") is not None


def _carehare_amqp_url(url: str) -> str:
    """kombu writes the default vhost as "//". carehare, which channels_rabbitmq
    uses, raises ValueError on that path, the layer's reconnect task dies, and
    every group_add() waits forever. Rewrite it to the "/%2F" form carehare
    accepts."""
    parts = urlsplit(url)
    if parts.path in ("//", "/"):
        parts = parts._replace(path="/%2F")
    return urlunsplit(parts)


def channel_layer_settings(
    env: Mapping[str, str], *, redis_url: str, cloud: bool
) -> tuple[str, dict]:
    """Resolve ``CHANNEL_LAYER_BACKEND`` into ``(backend, CHANNEL_LAYERS)``.

    ``memory``    one web process (``GRANIAN_WORKERS=1``); no broker.
    ``redis``     several web processes or pods; ``CHANNEL_REDIS_URL``, else
                  ``REDIS_URL``.
    ``rabbitmq``  legacy AMQP at ``CHANNEL_LAYER_AMQP_URL``; needs the
                  ``rabbitmq`` extra (channels-rabbitmq).
    ``auto``      the default, also when unset or empty: ``rabbitmq`` when
                  ``CHANNEL_LAYER_AMQP_URL`` is set; ``redis`` on Future AGI
                  Cloud, whose backend runs as several pods, or when
                  ``GRANIAN_WORKERS`` > 1; otherwise ``memory``.

    RabbitMQ is never inferred from ``CELERY_BROKER_URL``: existing ``.env``
    files still carry ``amqp://...@rabbitmq``, a host that no longer ships, and
    every ``group_add()`` would wait on it forever.
    """
    workers = _env_int(env, "GRANIAN_WORKERS", 1)
    amqp_url = env.get("CHANNEL_LAYER_AMQP_URL", "")
    backend = (env.get("CHANNEL_LAYER_BACKEND") or "").strip().lower() or "auto"
    if backend == "inmemory":
        backend = "memory"
    if backend == "auto":
        if amqp_url:
            backend = "rabbitmq"
        elif cloud or workers > 1:
            backend = "redis"
        else:
            backend = "memory"

    capacity = _env_int(env, "CHANNEL_LAYER_CAPACITY", 1500)
    expiry = _env_int(env, "CHANNEL_LAYER_EXPIRY_SECONDS", 300)

    if backend == "memory":
        # Only the web process holds WebSockets. Workers and bootstrap read the
        # same .env, so a GRANIAN_WORKERS meant for the backend must not stop
        # them from starting.
        if workers > 1 and env.get("SERVICE_TYPE", "backend") == "backend":
            raise ImproperlyConfigured(
                "CHANNEL_LAYER_BACKEND=memory keeps WebSocket groups inside "
                f"one process, but GRANIAN_WORKERS={workers}: live updates "
                "would reach only the clients on the worker that received "
                "them. Set CHANNEL_LAYER_BACKEND=redis (or auto)."
            )
        layer = {
            "BACKEND": "tfc.channel_layers.ThreadSafeInMemoryChannelLayer",
            "CONFIG": {"expiry": expiry, "capacity": capacity},
        }
    elif backend == "redis":
        layer = {
            "BACKEND": "channels_redis.core.RedisChannelLayer",
            "CONFIG": {
                "hosts": [env.get("CHANNEL_REDIS_URL") or redis_url],
                "prefix": "fi-ws",
                "expiry": expiry,
                "capacity": capacity,
            },
        }
    elif backend == "rabbitmq":
        if not _channels_rabbitmq_installed():
            raise ImproperlyConfigured(
                "CHANNEL_LAYER_BACKEND=rabbitmq needs channels-rabbitmq "
                "(pip install 'core-backend[rabbitmq]')."
            )
        if not amqp_url:
            raise ImproperlyConfigured(
                "CHANNEL_LAYER_BACKEND=rabbitmq needs CHANNEL_LAYER_AMQP_URL."
            )
        layer = {
            "BACKEND": "channels_rabbitmq.core.RabbitmqChannelLayer",
            "CONFIG": {
                "host": _carehare_amqp_url(amqp_url),
                "ssl_context": None,
                "expiry": expiry,
                "local_capacity": 500,
                "local_expiry": expiry,
                "remote_capacity": 500,
            },
        }
    else:
        raise ImproperlyConfigured(
            f"Unknown CHANNEL_LAYER_BACKEND={backend!r}; expected auto, memory, "
            "redis or rabbitmq."
        )
    return backend, {"default": layer}
