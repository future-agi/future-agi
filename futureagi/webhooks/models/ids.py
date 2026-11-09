"""Prefixed ULID defaults for the webhooks tables (DESIGN.md section 4). Module-level functions, so migrations
reference them by name (``webhooks.models.new_endpoint_id`` and so on)."""

from __future__ import annotations

import time

from webhooks.core import ids


def _new(prefix: str) -> str:
    return ids.new_id(prefix, int(time.time() * 1000))


# Module-level functions, so migrations can reference them by name.
def new_endpoint_id() -> str:
    return _new("whep")


def new_secret_id() -> str:
    return _new("whk")


def new_subscription_id() -> str:
    return _new("whsub")


def new_event_id() -> str:
    return _new("evt")


def new_payload_id() -> str:
    return _new("whpl")


def new_delivery_id() -> str:
    return _new("dlv")


def new_attempt_id() -> str:
    return _new("att")


def new_policy_id() -> str:
    return _new("whpol")


ID = {"primary_key": True, "max_length": 40, "editable": False}
