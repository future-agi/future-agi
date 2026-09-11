"""Check PeerDB's existing Temporal namespace and MirrorName TEXT attribute.

Default invocation is read-only. Local --apply adds only a missing MirrorName,
once, then waits for read visibility. Never creates a namespace, changes an
attribute, or retries an uncertain write. This is not CDC readiness proof.
"""

from __future__ import annotations

import argparse
import asyncio
import ipaddress
import json
import math
import os
import re
import sys
from datetime import timedelta

from temporalio.api.enums.v1 import IndexedValueType, NamespaceState
from temporalio.api.operatorservice.v1 import (
    AddSearchAttributesRequest,
    ListSearchAttributesRequest,
)
from temporalio.api.workflowservice.v1 import DescribeNamespaceRequest
from temporalio.client import Client
from temporalio.service import RPCError, RPCStatusCode

ADDRESS = "peerdb-temporal:7233"
NAMESPACE = "default"
ATTRIBUTE = "MirrorName"
TEXT = IndexedValueType.INDEXED_VALUE_TYPE_TEXT
_POLL_INTERVAL = 0.5
_TRANSIENT_READS = {RPCStatusCode.UNAVAILABLE, RPCStatusCode.NOT_FOUND}


class TemporalSetupError(ValueError):
    """Safe to display; never contains transport payloads or credentials."""


def _address(value):
    try:
        if not isinstance(value, str) or any(
            c.isspace() or ord(c) < 32 or c in "/\\@?#,;%" for c in value
        ):
            raise ValueError
        host, separator, port = value.rpartition(":")
        if not separator or not port.isascii() or not port.isdecimal():
            raise ValueError
        if not 0 < int(port) < 65536:
            raise ValueError
        if host.startswith("[") and host.endswith("]"):
            ipaddress.IPv6Address(host[1:-1])
        elif re.fullmatch(r"[0-9.]+", host):
            ipaddress.IPv4Address(host)
        elif len(host) > 253 or not all(
            re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
            for label in host.removesuffix(".").split(".")
        ):
            raise ValueError
    except ValueError:
        raise TemporalSetupError(
            "TEMPORAL_CLI_ADDRESS must be one hostname or IP and a valid explicit port"
        ) from None
    return value


def _validate(env, apply, timeout):
    if type(apply) is not bool:
        raise TemporalSetupError("apply must be explicitly true or false")
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
        raise TemporalSetupError("timeout must be a finite positive number of seconds")
    address = _address(env.get("TEMPORAL_CLI_ADDRESS", ADDRESS))
    namespace = env.get("PEERDB_TEMPORAL_NAMESPACE", NAMESPACE)
    if (
        not isinstance(namespace, str)
        or not namespace
        or len(namespace) > 255
        or namespace != namespace.strip()
        or any(ord(c) < 32 or ord(c) == 127 for c in namespace)
    ):
        raise TemporalSetupError(
            "PEERDB_TEMPORAL_NAMESPACE must be an explicit namespace name"
        )
    # Match schema_topology's ENV/CLOUD guard without importing v2.__init__,
    # which loads Django. Replicated ClickHouse also disallows local apply.
    hosted = str(env.get("ENV_TYPE", "")).strip().lower() in {
        "prod",
        "production",
    } and str(env.get("CLOUD_DEPLOYMENT", "")).strip().upper() in {"US", "EU"}
    replicated = str(env.get("CH_USE_REPLICATED_ENGINES", "false")).strip().lower()
    if apply and (hosted or replicated in {"1", "true", "yes", "on"}):
        raise TemporalSetupError("hosted/replicated Temporal apply is not supported")
    return address, namespace


class _Deadline:
    def __init__(self, timeout):
        self.loop = asyncio.get_running_loop()
        self.end = self.loop.time() + timeout

    def remaining(self):
        remaining = self.end - self.loop.time()
        if remaining <= 0:
            raise TimeoutError
        return remaining

    def rpc_timeout(self):
        return timedelta(seconds=min(10, self.remaining()))

    async def pause(self):
        await asyncio.sleep(min(_POLL_INTERVAL, self.remaining()))


async def _read(rpc, request, deadline):
    while True:
        try:
            return await rpc(request, retry=False, timeout=deadline.rpc_timeout())
        except RPCError as error:
            if error.status not in _TRANSIENT_READS:
                raise TemporalSetupError("Temporal prerequisite read failed") from None
        await deadline.pause()


async def _namespace(client, namespace, deadline):
    response = await _read(
        client.workflow_service.describe_namespace,
        DescribeNamespaceRequest(namespace=namespace),
        deadline,
    )
    info = response.namespace_info
    if (
        info.name != namespace
        or info.state != NamespaceState.NAMESPACE_STATE_REGISTERED
    ):
        raise TemporalSetupError("configured existing namespace must be REGISTERED")


async def _inspect(client, namespace, deadline):
    await _namespace(client, namespace, deadline)
    response = await _read(
        client.operator_service.list_search_attributes,
        ListSearchAttributesRequest(namespace=namespace),
        deadline,
    )
    if ATTRIBUTE in response.system_attributes:
        raise TemporalSetupError("MirrorName must be a custom TEXT search attribute")
    if ATTRIBUTE not in response.custom_attributes:
        return False
    if response.custom_attributes[ATTRIBUTE] != TEXT:
        raise TemporalSetupError("existing MirrorName has an incompatible type")
    return True


async def run(*, apply=False, timeout=300, env=None):
    """Bound connection, read waits, the single write, and verification together."""
    try:
        address, namespace = _validate(
            os.environ if env is None else env, apply, timeout
        )
        deadline = _Deadline(timeout)
        async with asyncio.timeout_at(deadline.end):
            # Lazy connection puts availability checks in explicit bounded reads.
            # No credentials, interceptors, or application/Django client wrappers.
            client = await Client.connect(address, namespace=namespace, lazy=True)
            if await _inspect(client, namespace, deadline):
                return {"ready": True, "applied": False}
            if not apply:
                return {"ready": False, "applied": False}
            # Recheck namespace state immediately before the only allowed write.
            await _namespace(client, namespace, deadline)
            try:
                await client.operator_service.add_search_attributes(
                    AddSearchAttributesRequest(
                        namespace=namespace, search_attributes={ATTRIBUTE: TEXT}
                    ),
                    retry=False,
                    timeout=deadline.rpc_timeout(),
                )
            except Exception:
                raise TemporalSetupError(
                    "Temporal attribute registration failed; write outcome uncertain. "
                    "Inspect existing state before another invocation; no automatic retry."
                ) from None
            while not await _inspect(client, namespace, deadline):
                await deadline.pause()
            return {"ready": True, "applied": True}
    except TemporalSetupError:
        raise
    except TimeoutError:
        raise TemporalSetupError(
            "Temporal setup deadline exceeded; inspect existing state before another invocation"
        ) from None
    except Exception:
        raise TemporalSetupError(
            "Temporal setup failed; no automatic write retry"
        ) from None


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise TemporalSetupError("invalid Temporal setup arguments")


def main(argv=None):
    parser = _Parser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true", help="Add missing MirrorName locally"
    )
    parser.add_argument(
        "--timeout", type=float, default=300, help="Whole-job deadline in seconds"
    )
    try:
        args = parser.parse_args(argv)
        result = asyncio.run(run(apply=args.apply, timeout=args.timeout))
        print(json.dumps(result))
        return 0 if result["ready"] else 1
    except TemporalSetupError as error:
        print(str(error), file=sys.stderr)
    except Exception:
        print("Temporal setup failed; no automatic write retry", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
