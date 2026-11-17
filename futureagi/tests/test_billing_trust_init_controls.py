"""Socket-free init integration controls; Linux init qualification is still owed."""

import ast
import importlib.util
import re
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import yaml

ROOT = Path(__file__).resolve().parents[2]
EXECUTOR = ROOT / "futureagi" / "code-executor"


COMMENT_NAMES_THE_GRANT = """\
services:
  backend:
    image: futureagi/backend:latest
  # The sandbox keeps NET_ADMIN while it installs its network policy.
  code-executor:
    image: futureagi/code-executor:latest
    cap_drop: [ALL]
    cap_add: [NET_ADMIN]
"""

BLOCK_FORM_GRANT_ELSEWHERE = """\
services:
  backend:
    image: futureagi/backend:latest
    cap_add:
      - NET_ADMIN
  code-executor:
    image: futureagi/code-executor:latest
    cap_drop: [ALL]
    cap_add: [NET_ADMIN]
"""


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def net_admin_grants(compose_text):
    services = yaml.safe_load(compose_text)["services"]
    return sum(
        str(capability).upper().removeprefix("CAP_") == "NET_ADMIN"
        for service in services.values()
        for capability in (service or {}).get("cap_add") or ()
    )


class InitIntegrationControls(unittest.TestCase):
    def test_container_has_policy_initialization_entrypoint(self):
        text = (EXECUTOR / "Dockerfile").read_text()
        self.assertRegex(text, r"(?m)^ENTRYPOINT ")
        self.assertNotIn("privileged: true", text)

    def test_compose_grants_only_net_admin_to_executor(self):
        for name in ("docker-compose.yml", "docker-compose.distributed.yml"):
            text = (ROOT / name).read_text()
            stanza = re.search(
                r"(?ms)^  code-executor:\n(.*?)(?=^  [a-zA-Z][\w-]*:|\Z)", text
            ).group(1)
            with self.subTest(name=name):
                self.assertNotRegex(stanza, r"(?m)^    privileged: true")
                self.assertRegex(stanza, r"cap_drop:\s*\[.?ALL.?\]")
                self.assertRegex(stanza, r"cap_add:\s*\[.?NET_ADMIN.?\]")
                self.assertNotIn("SYS_ADMIN", stanza)
                # No platform container gets the new network capability.
                self.assertEqual(net_admin_grants(text), 1)

    def test_a_capability_named_only_in_a_comment_is_not_a_grant(self):
        self.assertEqual(net_admin_grants(COMMENT_NAMES_THE_GRANT), 1)

    def test_a_block_form_grant_to_another_service_is_counted(self):
        self.assertEqual(net_admin_grants(BLOCK_FORM_GRANT_ELSEWHERE), 2)

    def test_helm_grants_only_net_admin_to_executor(self):
        text = (
            ROOT / "deploy/helm/futureagi/templates/code-executor/deployment.yaml"
        ).read_text()
        self.assertNotIn("privileged: true", text)
        self.assertIn("capabilities:", text)
        self.assertRegex(text, r"drop:\s*\[.?ALL.?\]")
        self.assertRegex(text, r"add:\s*\[.?NET_ADMIN.?\]")
        self.assertNotIn("SYS_ADMIN", text)

    def test_standalone_default_routes_to_dedicated_executor(self):
        text = (ROOT / "docker-compose.yml").read_text()
        # An init capability may not be added to the app's shared namespace.
        stanza = re.search(
            r"(?ms)^  code-executor:\n(.*?)(?=^  [a-zA-Z][\w-]*:|\Z)", text
        ).group(1)
        self.assertNotIn('profiles: ["sandbox"]', stanza)
        self.assertNotIn('"code-executor:127.0.0.1"', text)


class RuntimeCapabilityControls(unittest.TestCase):
    def setUp(self):
        self.guard = load("init_guard_controls", EXECUTOR / "egress_guard.py")

    def test_capability_retention_refuses_even_under_unprivileged_uid(self):
        validate = getattr(self.guard, "validate_runtime_status", None)
        self.assertIsNotNone(validate, "no post-init capability verification")
        good = {
            "Uid": "65534 65534 65534 65534",
            "Gid": "65534 65534 65534 65534",
            "CapEff": "0000000000000000",
            "CapPrm": "0000000000000000",
            "CapInh": "0000000000000000",
            "CapAmb": "0000000000000000",
            "NoNewPrivs": "1",
        }
        validate(good)
        for key, value in (
            ("CapEff", "0000000000001000"),
            ("CapPrm", "0000000000001000"),
            ("CapInh", "0000000000001000"),
            ("CapAmb", "0000000000001000"),
            ("NoNewPrivs", "0"),
            ("Uid", "0 0 0 0"),
            ("Gid", "0 0 0 0"),
        ):
            with self.subTest(key=key), self.assertRaises(RuntimeError):
                validate({**good, key: value})
        with self.assertRaises(RuntimeError):
            validate({})

    def test_request_preparation_does_not_run_privileged_installer(self):
        # A server with dropped NET_ADMIN cannot run nft again per request.
        tree = ast.parse((EXECUTOR / "server.py").read_text())
        imports = [node for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
        self.assertTrue(
            any(
                alias.name == "verify_egress" and alias.asname == "prepare_egress"
                for node in imports
                for alias in node.names
            ),
            "server still installs the policy at request/health time",
        )

    def test_fallback_dns_tcp_port_is_not_blocked_by_landlock(self):
        with patch.dict(
            sys.modules,
            {"egress_guard": self.guard, "falcon": SimpleNamespace(App=lambda: Mock())},
        ):
            server = load("init_server_controls", EXECUTOR / "server.py")
        server.LANDLOCK_TCP_RULES = True
        with (
            patch.object(server, "prepare_egress"),
            patch.object(server.os, "chown"),
            patch.object(
                server.subprocess,
                "Popen",
                side_effect=RuntimeError("synthetic stop before spawn"),
            ) as popen,
        ):
            server._execute_python_fallback("def evaluate(): return 1", {}, 1)
        argv = popen.call_args.args[0]
        launcher = argv.index("-c") + 1
        self.assertIn("53", argv[launcher + 7].split(","))


def deny_sockets(event, args):
    if event.startswith("socket."):
        raise PermissionError("socket-free control boundary")


if __name__ == "__main__":
    sys.addaudithook(deny_sockets)
    try:
        sys.audit("socket.connect", None, ("synthetic", 443))
    except PermissionError:
        pass
    else:
        raise AssertionError("socket guard did not refuse")
    unittest.main()
