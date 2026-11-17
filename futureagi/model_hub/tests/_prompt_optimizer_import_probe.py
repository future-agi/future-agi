"""Fresh-process import observer; never replace LLM or licensing on this path.

Only synthetic test configuration may be supplied by the test runner. The local
mode refuses configuration files instead of loading a developer's settings.
Python socket operations and child creation are observed; this is not a claim of
coverage for native transports bypassing Python (remote runs also deny egress).
"""
import argparse
import importlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import traceback
from unittest.mock import patch


TARGET = "ee.agenthub.prompt_optimizer_agent.agent_task_v2"
BACKEND = Path(__file__).resolve().parents[2]


class AttemptDenied(RuntimeError):
    pass


class Observer:
    def __init__(self):
        self.attempts = []
        self.constructions = []
        self.activations = []
        self.children = []
        self.patches = []
        self.active = False

    @staticmethod
    def frames():
        return [
            {"file": frame.filename, "line": frame.lineno, "function": frame.name}
            for frame in traceback.extract_stack()[:-2]
        ]

    def deny(self, name):
        def refused(*args, **kwargs):
            self.attempts.append({"operation": name, "frames": self.frames()})
            raise AttemptDenied(name)
        return refused

    def audit(self, event, args):
        if not self.active:
            return
        if event in {"subprocess.Popen", "os.system", "os.fork", "os.posix_spawn"}:
            self.children.append(event)
            raise AttemptDenied("child creation refused")
        if event in {"socket.getaddrinfo", "socket.gethostbyname", "socket.gethostbyaddr",
                     "socket.connect", "socket.sendto", "socket.sendmsg"}:
            self.attempts.append({"operation": event, "frames": self.frames()})
            raise AttemptDenied(event)

    def profile(self, frame, event, arg):
        if event != "call":
            return
        filename = frame.f_code.co_filename.replace("\\", "/")
        name = frame.f_code.co_name
        if filename.endswith("/agentic_eval/core/llm/llm.py") and name == "__init__":
            self.constructions.append({"kind": "LLM", "frames": self.frames()})
        if filename.endswith("/ee/usage/services/gateway_llm_client.py") and name in {
            "get_gateway_client", "get_async_gateway_client"
        }:
            self.constructions.append({"kind": name, "frames": self.frames()})
        if filename.endswith("/ee/licensing/activation_client.py") and name in {
            "get_service_token", "_activate"
        }:
            self.activations.append({"kind": name, "frames": self.frames()})

    def __enter__(self):
        for owner, names in (
            (socket, ("getaddrinfo", "gethostbyname", "gethostbyname_ex", "gethostbyaddr")),
            (socket.socket, ("connect", "connect_ex", "send", "sendall", "sendto", "sendmsg")),
        ):
            for name in names:
                if hasattr(owner, name):
                    item = patch.object(owner, name, self.deny(name))
                    item.start()
                    self.patches.append(item)
        sys.addaudithook(self.audit)
        self.previous_profile = sys.getprofile()
        self.previous_thread_profile = threading.getprofile()
        sys.setprofile(self.profile)
        threading.setprofile(self.profile)
        self.active = True
        return self

    def __exit__(self, *exc):
        self.active = False
        sys.setprofile(self.previous_profile)
        threading.setprofile(self.previous_thread_profile)
        for item in reversed(self.patches):
            item.stop()

    def evidence(self):
        return {"attempts": self.attempts, "constructions": self.constructions,
                "activations": self.activations, "children": self.children}


def calibrate():
    with Observer() as observer:
        with socket.socket() as stream, socket.socket(type=socket.SOCK_DGRAM) as datagram:
            operations = [
                lambda: socket.getaddrinfo("calibration.invalid", 443),
                lambda: stream.connect(("127.0.0.1", 9)),
                lambda: stream.connect_ex(("127.0.0.1", 9)),
                lambda: stream.send(b"fixture"),
                lambda: stream.sendall(b"fixture"),
                lambda: datagram.sendto(b"fixture", ("127.0.0.1", 9)),
            ]
            for operation in operations:
                try:
                    operation()
                except AttemptDenied:
                    pass
                else:
                    raise AssertionError("observer did not refuse calibration")
            worker = threading.Thread(target=lambda: refused_thread(stream))
            worker.start()
            worker.join(timeout=5)
            assert not worker.is_alive(), "calibration thread timed out"
        try:
            subprocess.run([sys.executable, "-I", "-B", "-c", "pass"], check=True)
        except AttemptDenied:
            pass
        else:
            raise AssertionError("child escaped observation")
    assert len(observer.attempts) == 7, "attempt oracle missed a calibration"
    assert len(observer.children) == 1, "child refusal not recorded"
    return {"status": "PASS", **observer.evidence()}


def refused_thread(stream):
    try:
        stream.connect(("127.0.0.1", 9))
    except AttemptDenied:
        return
    raise AssertionError("worker escaped observation")


def refuse_local_configuration(event, args):
    if event != "open" or not isinstance(args[0], (str, bytes)):
        return
    path = os.fsdecode(args[0]).replace("\\", "/").lower()
    parts = path.split("/")
    # Django's own conf/global_settings.py contains library defaults, not local
    # deployment configuration. Project settings and credential files are denied.
    project_settings = str(BACKEND).lower() in path and any("settings" in p for p in parts)
    sensitive = any(p.startswith(".env") or p.startswith("values") or "compose" in p
                    or "credentials" in p or "secrets" in p for p in parts)
    if project_settings or sensitive or path.endswith((".bru", ".pem", ".key")):
        raise PermissionError("local configuration access refused")


def probe(target, django_settings=None):
    sys.path.insert(0, str(BACKEND))
    if django_settings:
        os.environ["DJANGO_SETTINGS_MODULE"] = django_settings
    else:
        os.environ.pop("DJANGO_SETTINGS_MODULE", None)
        sys.addaudithook(refuse_local_configuration)
    result = {"target": target, "status": "SETUP_FAILURE", "importCompleted": False}
    with Observer() as observer:
        try:
            if django_settings:
                import django
                django.setup()
            module = importlib.import_module(target)
            optimizer = importlib.import_module(TARGET)
            assert optimizer.PromptOptimizer.__module__ == TARGET, "EE fallback is not proof"
            assert optimizer.LLM.__module__ == "agentic_eval.core.llm.llm", "LLM was replaced"
            if target == "model_hub.tasks":
                assert callable(module.optimization_runner)
                assert callable(module.process_optimization)
                consumer = importlib.import_module("model_hub.views.develop_optimiser")
                assert consumer.PromptOptimizer is optimizer.PromptOptimizer
            result["importCompleted"] = True
        except Exception as error:
            # Stack frames are complete, but omit exception messages/source text:
            # import errors can otherwise echo configuration or destination values.
            result["errorType"] = type(error).__name__
            result["traceback"] = [
                {"file": f.filename, "line": f.lineno, "function": f.name}
                for f in traceback.extract_tb(error.__traceback__)
            ]
    result.update(observer.evidence())
    eager = any(
        row["kind"] == "LLM" and any(
            f["file"].replace("\\", "/").endswith("/prompt_optimizer_agent/agent_task_v2.py")
            and f["function"] == "<module>" for f in row["frames"]
        ) for row in observer.constructions
    )
    if eager:
        result["status"] = "ASSERTION_RED"
    elif result["importCompleted"]:
        result["status"] = "PASS" if not any((observer.attempts, observer.constructions,
                                               observer.activations, observer.children)) else "FAIL"
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--calibrate", action="store_true")
    parser.add_argument("--target", choices=(TARGET, "model_hub.tasks"), default=TARGET)
    parser.add_argument("--django-settings")
    args = parser.parse_args()
    result = calibrate() if args.calibrate else probe(args.target, args.django_settings)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
