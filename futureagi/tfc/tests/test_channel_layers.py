"""Channels layer: the thread-safe in-memory layer, CHANNEL_LAYER_BACKEND
selection, and the setup-check probe that follows it.

ThreadSafeInMemoryChannelLayer: a group_send from any thread must reach an idle
consumer at once. The stock InMemoryChannelLayer calls Queue.put_nowait from the
sender's thread. That wakes the consumer's future off-loop, so an idle loop
never sees the message (measured: delivered only when an unrelated 3s timer
fired; "Non-thread-safe operation" under asyncio debug). This is the path
model_hub/views/annotation_queues.py takes: threading.Thread ->
send_message_to_channel -> async_to_sync -> a fresh loop in a pool thread.
"""

import asyncio
import threading
import time
from unittest.mock import patch

import pytest
from asgiref.sync import async_to_sync
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings

from tfc import channel_layers
from tfc.channel_layers import ThreadSafeInMemoryChannelLayer, channel_layer_settings
from tfc.settings import settings as settings_module
from tfc.views import setup_checks


async def _park_consumer(layer, group="org_1"):
    channel = await layer.new_channel()
    await layer.group_add(group, channel)
    task = asyncio.ensure_future(layer.receive(channel))
    await asyncio.sleep(0.05)  # consumer is now parked on queue.get()
    return channel, task


async def test_group_send_from_plain_thread_wakes_idle_consumer():
    layer = ThreadSafeInMemoryChannelLayer(expiry=60, capacity=100)
    _, task = await _park_consumer(layer)

    def sender():
        async_to_sync(layer.group_send)("org_1", {"type": "send_data", "n": 1})

    started = time.monotonic()
    thread = threading.Thread(target=sender)
    thread.start()
    message = await asyncio.wait_for(task, timeout=1.0)
    elapsed = time.monotonic() - started
    # Join off-loop: the sender waits for the home loop to finish group_send,
    # so a blocking join() here would stall the very loop it is waiting on.
    await asyncio.to_thread(thread.join, 1.0)

    assert message == {"type": "send_data", "n": 1}
    assert elapsed < 0.5
    assert not thread.is_alive()


async def test_group_send_on_home_loop_is_direct():
    layer = ThreadSafeInMemoryChannelLayer(expiry=60, capacity=100)
    _, task = await _park_consumer(layer)
    await layer.group_send("org_1", {"type": "send_data", "n": 2})
    assert (await asyncio.wait_for(task, timeout=1.0))["n"] == 2


async def test_discarded_channel_gets_nothing():
    layer = ThreadSafeInMemoryChannelLayer(expiry=60, capacity=100)
    channel, task = await _park_consumer(layer)
    await layer.group_discard("org_1", channel)
    await layer.group_send("org_1", {"type": "send_data"})
    done, _ = await asyncio.wait({task}, timeout=0.2)
    assert not done
    task.cancel()


def test_send_before_any_consumer_is_a_noop():
    """Nothing has claimed a home loop yet, e.g. a relay that arrives before the
    first WebSocket connects. The call must run locally and do nothing."""
    layer = ThreadSafeInMemoryChannelLayer(expiry=60, capacity=100)
    async_to_sync(layer.group_send)("org_1", {"type": "send_data"})
    assert layer.groups == {}


# -- CHANNEL_LAYER_BACKEND selection -------------------------------------------

REDIS_URL = "redis://cache:6379/0"


def _select(env, cloud=False):
    return channel_layer_settings(env, redis_url=REDIS_URL, cloud=cloud)


@pytest.fixture
def rabbitmq_installed(monkeypatch):
    monkeypatch.setattr(channel_layers, "_channels_rabbitmq_installed", lambda: True)


@pytest.mark.parametrize(
    "env, cloud, expected",
    [
        ({}, False, "memory"),
        # k8s and compose often pass the variable through empty.
        ({"CHANNEL_LAYER_BACKEND": ""}, False, "memory"),
        ({"CHANNEL_LAYER_BACKEND": " Auto "}, False, "memory"),
        ({"CHANNEL_LAYER_BACKEND": "inmemory"}, False, "memory"),
        ({"GRANIAN_WORKERS": "4"}, False, "redis"),
        # Cloud runs several backend pods, so a process-local layer is wrong.
        ({}, True, "redis"),
        # An old .env still naming the retired rabbitmq service must not select
        # it: group_add() would wait on a host that no longer exists.
        (
            {"CELERY_BROKER_URL": "amqp://user:password@rabbitmq:5672/%2F"},
            True,
            "redis",
        ),
        ({"CELERY_BROKER_URL": "amqp://u:p@rabbitmq:5672//"}, False, "memory"),
        ({"CHANNEL_LAYER_AMQP_URL": "amqp://u:p@mq:5672/%2F"}, True, "rabbitmq"),
    ],
)
def test_auto_picks_the_layer(env, cloud, expected, rabbitmq_installed):
    backend, _ = _select(env, cloud=cloud)
    assert backend == expected


def test_default_is_the_thread_safe_memory_layer():
    backend, layers = _select(
        {"CHANNEL_LAYER_CAPACITY": "10", "CHANNEL_LAYER_EXPIRY_SECONDS": "7"}
    )
    assert backend == "memory"
    assert layers["default"] == {
        "BACKEND": "tfc.channel_layers.ThreadSafeInMemoryChannelLayer",
        "CONFIG": {"expiry": 7, "capacity": 10},
    }


def test_redis_layer_prefers_channel_redis_url():
    _, layers = _select(
        {
            "CHANNEL_LAYER_BACKEND": "redis",
            "CHANNEL_REDIS_URL": "redis://127.0.0.1:6379/3",
        }
    )
    assert layers["default"]["BACKEND"] == "channels_redis.core.RedisChannelLayer"
    assert layers["default"]["CONFIG"]["hosts"] == ["redis://127.0.0.1:6379/3"]

    _, layers = _select({"CHANNEL_LAYER_BACKEND": "redis"})
    assert layers["default"]["CONFIG"]["hosts"] == [REDIS_URL]


@pytest.mark.parametrize("service_type", [None, "backend"])
def test_memory_with_several_web_workers_refuses_to_boot(service_type):
    env = {"CHANNEL_LAYER_BACKEND": "memory", "GRANIAN_WORKERS": "2"}
    if service_type:
        env["SERVICE_TYPE"] = service_type
    with pytest.raises(ImproperlyConfigured, match="GRANIAN_WORKERS=2"):
        _select(env)


@pytest.mark.parametrize("service_type", ["temporal-worker", "bootstrap"])
def test_memory_guard_ignores_processes_without_websockets(service_type):
    """Workers read the same .env as the backend. A GRANIAN_WORKERS meant for
    the backend must not crash-loop them."""
    backend, _ = _select(
        {
            "CHANNEL_LAYER_BACKEND": "memory",
            "GRANIAN_WORKERS": "4",
            "SERVICE_TYPE": service_type,
        }
    )
    assert backend == "memory"


def test_rabbitmq_rewrites_the_kombu_default_vhost(rabbitmq_installed):
    _, layers = _select({"CHANNEL_LAYER_AMQP_URL": "amqp://u:p@mq:5672//"})
    assert layers["default"]["BACKEND"] == (
        "channels_rabbitmq.core.RabbitmqChannelLayer"
    )
    assert layers["default"]["CONFIG"]["host"] == "amqp://u:p@mq:5672/%2F"


def test_rabbitmq_needs_an_explicit_url(rabbitmq_installed):
    with pytest.raises(ImproperlyConfigured, match="CHANNEL_LAYER_AMQP_URL"):
        _select(
            {
                "CHANNEL_LAYER_BACKEND": "rabbitmq",
                "CELERY_BROKER_URL": "amqp://u:p@mq:5672//",
            }
        )


def test_rabbitmq_needs_the_package(monkeypatch):
    monkeypatch.setattr(channel_layers, "_channels_rabbitmq_installed", lambda: False)
    with pytest.raises(ImproperlyConfigured, match="channels-rabbitmq"):
        _select({"CHANNEL_LAYER_AMQP_URL": "amqp://u:p@mq:5672/%2F"})


def test_unknown_backend_refuses_to_boot():
    with pytest.raises(ImproperlyConfigured, match="kafka"):
        _select({"CHANNEL_LAYER_BACKEND": "kafka"})


# -- setup-check probe ---------------------------------------------------------


def _probe_settings(env, endpoint="http://backend/call-websocket/"):
    """Point the probe at the layer ``env`` selects and at a relay endpoint."""
    backend, layers = _select(env)
    return patch.multiple(
        settings_module,
        CHANNEL_LAYER_BACKEND=backend,
        CHANNEL_LAYERS=layers,
        WEBSOCKET_ENDPOINT=endpoint,
    )


def test_memory_layer_probe_needs_no_broker():
    with (
        _probe_settings({}),
        patch.object(setup_checks, "_tcp_up", return_value=True) as tcp_up,
    ):
        assert setup_checks._channel_layer_up()
    # Only the relay endpoint is contacted: no AMQP broker, no Redis.
    tcp_up.assert_called_once_with("backend", 80)


def test_rabbitmq_layer_probe_checks_its_broker(rabbitmq_installed):
    env = {"CHANNEL_LAYER_AMQP_URL": "amqp://u:p@mq:5673/%2F"}
    with (
        _probe_settings(env),
        patch.object(setup_checks, "_tcp_up", return_value=True) as tcp_up,
    ):
        assert setup_checks._channel_layer_up()
    assert tcp_up.call_args_list[0].args == ("mq", 5673)


@override_settings(CLOUD_DEPLOYMENT="")
def test_vendor_relay_endpoint_fails_without_being_contacted():
    endpoint = "https://api.futureagi.com/call-websocket/"
    with (
        _probe_settings({}, endpoint=endpoint),
        patch.object(setup_checks, "_tcp_up", return_value=True) as tcp_up,
    ):
        assert not setup_checks._channel_layer_up()
    tcp_up.assert_not_called()
