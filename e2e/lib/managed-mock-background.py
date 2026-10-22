"""Read-only, stdin-fed attestation invoked ONLY by the opted-in managed fixture.

No Django startup, model invocation, source writes or Temporal dispatch. Raw
environment stays private in this process. A constructor probe is not inspection
of the live worker's client cache; fresh owned processes/source are still required.
"""
import ast
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import socket
import sys
import time
from types import SimpleNamespace


class AttestationError(RuntimeError):
    pass


def require(ok, reason):
    if not ok:
        raise AttestationError("STOP: managed mock background " + reason)


def validate_process_environment(actual, expected):
    require(all(actual.get(key) == value for key, value in expected.items()), "process environment drift")
    # Entry-point exports (PWD, ENV_PROJECT_ROOT, etc.) are benign. A security
    # override absent from Docker Config.Env is not: never silently normalize it.
    for key, value in actual.items():
        if re.search(r"proxy|preload|python|agentcc|serving|temporal|api_key|credential|license|telemetry|mail|slack|sentry|webhook|mix_panel", key, re.I):
            require(not value or expected.get(key) == value, "unexpected process override")


def construct_gateway(source):
    """Compile only the real five factory/config functions, not the app module.

    No Django settings configured. Actual installed OpenAI must construct/cache
    the exact local client without any socket/DNS/bind attempt, even a caught one.
    """
    names = {"_get_setting", "_deployment_mode", "_resolve_gateway_config", "_get_ee_service_token", "get_gateway_client"}
    nodes = [node for node in ast.parse(source).body if isinstance(node, ast.FunctionDef) and node.name in names]
    require({node.name for node in nodes} == names, "gateway factory source changed")
    attempts = []
    active = True

    def deny_network(event, _args):
        if active and event in {"socket.connect", "socket.bind", "socket.getaddrinfo", "socket.gethostbyname",
                                "socket.gethostbyaddr", "socket.sendto"}:
            attempts.append(event)
            raise RuntimeError("constructor network forbidden")

    sys.addaudithook(deny_network)
    noop = lambda *args, **kwargs: None
    scope = {"os": os, "_client": None, "logger": SimpleNamespace(info=noop, warning=noop, exception=noop)}
    client = None
    try:
        exec(compile(ast.Module(body=nodes, type_ignores=[]), "gateway-factory", "exec"), scope)
        client = scope["get_gateway_client"]()
        require(client is not None and str(client.base_url) == "http://agentcc-gateway:8080/v1/", "gateway constructor failed")
        require(client.api_key == os.environ["AGENTCC_INTERNAL_API_KEY"], "gateway constructor credential mismatch")
        require(scope["get_gateway_client"]() is client and not attempts, "gateway construction/cache unsafe")
    finally:
        if client is not None:
            client.close()
        active = False
    require(not attempts, "gateway constructor attempted network")


def validate_pollers(pollers, identity, now):
    require(len(pollers) == 1 and pollers[0].identity == identity, "agent_compass poller ownership mismatch")
    age = now - pollers[0].last_access_time.seconds
    require(-5 <= age <= 120, "agent_compass poller is stale")


def describe_worker(address, identity):
    import grpc
    from temporalio.api.taskqueue.v1 import TaskQueue
    from temporalio.api.workflowservice.v1 import DescribeNamespaceRequest, DescribeTaskQueueRequest
    from temporalio.api.workflowservice.v1.service_pb2_grpc import WorkflowServiceStub

    # This short-lived observer needs three Describe RPCs, not an SDK Runtime.
    # Its global native thread pool can race Python finalization. Use the shipped
    # protobuf stub and explicitly close the channel before returning a receipt.
    with grpc.insecure_channel(address + ":7233", options=(
        ("grpc.enable_retries", 0), ("grpc.enable_http_proxy", 0),
    )) as channel:
        service = WorkflowServiceStub(channel)
        namespace = service.DescribeNamespace(
            DescribeNamespaceRequest(namespace="default"), timeout=5, wait_for_ready=False)
        require(namespace.namespace_info.name == "default" and namespace.namespace_info.state == 1
                and bool(namespace.namespace_info.id), "Temporal namespace not registered")
        for queue_type in (1, 2):  # TaskQueueType WORKFLOW / ACTIVITY.
            response = service.DescribeTaskQueue(
                DescribeTaskQueueRequest(namespace="default", task_queue=TaskQueue(name="agent_compass"),
                                         task_queue_type=queue_type), timeout=5, wait_for_ready=False)
            validate_pollers(response.pollers, identity, time.time())
    return {"namespaceId": namespace.namespace_info.id, "queue": "agent_compass", "pollerIdentity": identity}


def main(payload):
    root = Path("/app/backend")
    for name, expected in payload["sourceHashes"].items():
        require(hashlib.sha256((root / name).read_bytes()).hexdigest() == expected, "packaged source mismatch: " + name)
    processes = [Path("/proc/1")]
    if payload["service"] == "worker":
        processes = []
        for process in Path("/proc").glob("[0-9]*"):
            try:
                argv = process.joinpath("cmdline").read_bytes().decode().split("\0")
            except (FileNotFoundError, ProcessLookupError):
                continue
            if len(argv) > 3 and argv[1:3] == ["manage.py", "start_temporal_worker"]:
                require(argv[3:6] == ["--task-queue", "default", "--all-queues"], "unexpected worker command")
                require(process.joinpath("cwd").resolve() == root, "worker cwd mismatch")
                processes.append(process)
    require(len(processes) == 1, "missing or ambiguous owned process")
    process = processes[0]
    environment = dict(item.split("=", 1) for item in process.joinpath("environ").read_bytes().decode().split("\0") if item)
    validate_process_environment(environment, payload["environment"])
    require(environment.get("HOSTNAME") == payload["containerId"][:12], "custom process hostname")
    identity = process.name + "@" + environment["HOSTNAME"]
    # Reproduce the checked app environment in this fresh, non-Django process.
    os.environ.clear()
    os.environ.update(environment)
    os.environ.pop("DJANGO_SETTINGS_MODULE", None)
    construct_gateway(root.joinpath("ee/usage/services/gateway_llm_client.py").read_text())
    for name, address in payload["addresses"].items():
        require(bool(address) and {row[4][0] for row in socket.getaddrinfo(name, None, socket.AF_INET)} == {address},
                "service DNS does not resolve to owned container")
    connection = http.client.HTTPConnection(payload["addresses"]["mock-llm"], 8080, timeout=5)
    try:
        connection.request("GET", "/model/v1/models", headers={"Host": "mock-llm:8080"})
        response = connection.getresponse()
        require(response.status == 200 and json.loads(response.read(1025)) == {"models": ["text_embedding"]},
                "serving health contract missing")
    finally:
        connection.close()
    temporal = describe_worker(payload["addresses"]["temporal"], identity) if payload["service"] == "worker" else None
    return {"service": payload["service"], "processIdentity": identity,
            "startTicks": process.joinpath("stat").read_text().rsplit(")", 1)[1].split()[19],
            "environmentMatches": True, "gatewayConstructor": True, "servingHealth": True, "temporal": temporal}


if __name__ == "__main__":
    try:
        print(json.dumps(main(json.load(sys.stdin))))
    except AttestationError as error:
        # Only our bounded, non-secret prerequisite messages may be emitted.
        raise SystemExit(str(error)) from None
    except Exception:
        # Never emit exception repr/tracebacks that might carry credentials.
        raise SystemExit("STOP: managed mock background attestation failed") from None
