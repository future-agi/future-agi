"""Offline snapshot output contracts, using actual source methods and DRF fields.

Run directly; no application settings, model imports, DB or network. Persistence,
permissions and URL routing are covered separately by the cloud integration suite.
Assertions are deliberately value-free, including on the expected RED baseline.
"""

import ast
import copy
import importlib.util
import json
import socket
import sys
import types
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "futureagi"
SECRET_KEYS = frozenset(
    {
        "api_key",
        "api_secret",
        "encrypted_api_key",
        "encrypted_api_secret",
        "livekit_api_key",
        "livekit_api_secret",
        "token",
        "password",
        "credentials",
        "credentials_legacy",
        "public_api_key",
    }
)
DISPLAY_KEYS = frozenset({"livekit_api_key", "livekit_api_secret", "token", "password"})


def deny_io(*args, **kwargs):
    raise AssertionError("offline test forbids database/network access")


socket.socket.connect = deny_io
socket.socket.connect_ex = deny_io
socket.socket.sendto = deny_io
socket.create_connection = deny_io
socket.getaddrinfo = deny_io
if hasattr(socket.socket, "sendmsg"):
    socket.socket.sendmsg = deny_io

import django  # noqa: E402
from django.conf import settings  # noqa: E402

if not settings.configured:
    settings.configure(
        INSTALLED_APPS=[],
        DATABASES={},
        USE_I18N=False,
        REST_FRAMEWORK={"UNAUTHENTICATED_USER": None},
    )
django.setup()
from django.db.backends.base.base import BaseDatabaseWrapper  # noqa: E402
from rest_framework import serializers  # noqa: E402

BaseDatabaseWrapper.ensure_connection = deny_io


def named(path, name):
    return next(
        n
        for n in ast.parse((ROOT / path).read_text()).body
        if getattr(n, "name", None) == name
    )


def execute(nodes, namespace):
    exec(
        compile(
            ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])),
            "<offline-source>",
            "exec",
        ),
        namespace,
    )


mask_ns = {}
execute([named("agentcc/services/credential_manager.py", "mask_key")], mask_ns)
mask_key = mask_ns["mask_key"]
module = types.ModuleType("agentcc.services.credential_manager")
module.mask_key = mask_key
sys.modules[module.__name__] = module
helper_path = ROOT / "simulate/utils/snapshot_projection.py"
if helper_path.exists():
    spec = importlib.util.spec_from_file_location("snapshot_projection", helper_path)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    scrub = helper.scrub_snapshot_secrets
else:
    # Baseline behavior, not an import/setup failure: RED must demonstrate leaks.
    def scrub(value):
        return value


class MissingCredentials(Exception):
    pass


class Version:
    id = "fixture-version"
    version_name = "fixture"

    def __init__(self, snapshot, creds=None):
        self.configuration_snapshot = snapshot
        self.agent_definition = types.SimpleNamespace(observability_provider=None)
        self._creds = creds

    @property
    def credentials(self):
        if self._creds is None:
            raise MissingCredentials
        return self._creds


def boundary(path, class_name, fields):
    source = named(path, class_name)
    method = next(
        n for n in source.body if getattr(n, "name", None) == "to_representation"
    )
    cls = ast.ClassDef(
        name=class_name,
        bases=[
            ast.Attribute(
                value=ast.Name(id="serializers", ctx=ast.Load()),
                attr="Serializer",
                ctx=ast.Load(),
            )
        ],
        keywords=[],
        body=[*ast.parse(fields).body, method],
        decorator_list=[],
    )
    namespace = {
        "serializers": serializers,
        "scrub_snapshot_secrets": scrub,
        "AgentDefinition": object,
        "AgentVersion": types.SimpleNamespace(
            credentials=types.SimpleNamespace(
                RelatedObjectDoesNotExist=MissingCredentials
            )
        ),
        "mask_key": mask_key,
        "logger": types.SimpleNamespace(exception=deny_io),
        "observability_is_on": lambda agent: False,
    }
    execute(
        [named("tracer/services/observability_switch.py", "snapshot_as_shown"), cls],
        namespace,
    )
    return namespace[class_name]


VersionSerializer = boundary(
    "simulate/serializers/response/agent_version.py",
    "AgentVersionResponseSerializer",
    "configuration_snapshot = serializers.JSONField()",
)
RunSerializer = boundary(
    "simulate/serializers/run_test.py",
    "RunTestSerializer",
    "id = serializers.CharField()\nevals_detail = serializers.ListField(default=list)",
)
UpdateSerializer = boundary(
    "simulate/serializers/agent_definition.py",
    "AgentDefinitionUpdateSerializer",
    "knowledge_base = serializers.JSONField(required=False)",
)


def payload():
    # Distinct generated markers, never included in assertion messages.
    secret = "fixture-" + uuid.uuid4().hex
    nested = dict.fromkeys(SECRET_KEYS, secret)
    return {
        **nested,
        "agent_name": "unchanged",
        "agent_type": "VOICE",
        "credential_storage_version": 1,
        "nested": [nested, {"safe": [1, False, None]}],
    }, secret


def expected(value, root=True):
    if isinstance(value, dict):
        return {
            k: (mask_key(v) if isinstance(v, str) else mask_key("****") if v else "")
            if k in DISPLAY_KEYS and root
            else expected(v, False)
            for k, v in value.items()
            if k not in SECRET_KEYS or (root and k in DISPLAY_KEYS)
        }
    if isinstance(value, list):
        return [expected(v, False) for v in value]
    return value


class SnapshotProjectionTests(unittest.TestCase):
    def assert_projection(self, before, actual, marker):
        self.assertTrue(actual == expected(before), "projection differs from contract")
        self.assertTrue(
            marker not in json.dumps(actual, default=str), "stored value leaked"
        )

    def test_helper_recursive_copy(self):
        before, marker = payload()
        saved = copy.deepcopy(before)
        projected = scrub(before)
        self.assert_projection(saved, projected, marker)
        self.assertTrue(before == saved, "stored snapshot mutated")
        projected["nested"].append("output-only")
        self.assertTrue(before == saved, "nested output aliases storage")

    def test_helper_secret_free_byte_identity(self):
        for value in (
            None,
            [],
            {},
            "plain",
            1,
            False,
            {
                "z": [1, {"authorization_method": "api_key", "max_tokens": 12}],
                "api_key_label": "kept",
            },
        ):
            self.assertTrue(
                json.dumps(scrub(value)) == json.dumps(value), "nonsecret bytes changed"
            )

    def test_helper_malformed_secret_values(self):
        for value in (None, "", 12, False, {"secret": "nested"}, ["nested"]):
            before = dict.fromkeys(SECRET_KEYS, value)
            self.assertTrue(
                scrub(before) == expected(before), "malformed credential projection"
            )

    def test_helper_non_dict_root(self):
        before, marker = payload()
        root = [before, {"safe": before}]
        saved = copy.deepcopy(root)
        projected = scrub(root)
        self.assertTrue(projected == expected(root), "non-dict root not scrubbed")
        self.assertTrue(marker not in json.dumps(projected), "nested marker leaked")
        self.assertTrue(root == saved, "list input mutated")

    def test_version_boundary_and_wrappers(self):
        namespace = {
            "serializers": serializers,
            "AgentVersionResponseSerializer": VersionSerializer,
        }
        for name in (
            "AgentVersionCreateResponseSerializer",
            "AgentVersionActivateResponseSerializer",
            "AgentVersionRestoreResponseSerializer",
        ):
            execute(
                [named("simulate/serializers/response/agent_version.py", name)],
                namespace,
            )
        for name in (
            None,
            *[
                k
                for k in namespace
                if k.endswith("ResponseSerializer")
                and k != "AgentVersionResponseSerializer"
            ],
        ):
            with self.subTest(wrapper=name):
                before, marker = payload()
                saved = copy.deepcopy(before)
                version = Version(before)
                if name:
                    actual = namespace[name](
                        {"version": version, "message": "ok", "agent": {}}
                    ).data["version"]["configuration_snapshot"]
                else:
                    actual = VersionSerializer(version).data["configuration_snapshot"]
                self.assert_projection(saved, actual, marker)
                self.assertTrue(before == saved, "version input mutated")

    def test_version_uuid_conversion_does_not_mutate(self):
        before = {"organization": uuid.uuid4(), "nested": [{"safe": 1}]}
        saved = copy.deepcopy(before)
        actual = VersionSerializer(Version(before)).data["configuration_snapshot"]
        self.assertTrue(actual["organization"] == str(saved["organization"]))
        self.assertTrue(before == saved, "UUID normalization mutated storage")

    def test_version_livekit_display_overlay_unchanged(self):
        before, marker = payload()
        creds = types.SimpleNamespace(
            provider_type="livekit",
            api_key=True,
            get_masked_api_key=lambda: "****",
            get_masked_api_secret=lambda: "********",
        )
        actual = VersionSerializer(Version(before, creds)).data[
            "configuration_snapshot"
        ]
        want = expected(before)
        want.update(livekit_api_key="****", livekit_api_secret="********")
        self.assertTrue(actual == want, "current credential display changed")
        self.assertTrue(marker not in json.dumps(actual), "historical value leaked")

    def test_run_explicit_latest_prefetched(self):
        for mode in ("explicit", "latest", "prefetched"):
            for has_type in (False, True):
                with self.subTest(mode=mode, has_type=has_type):
                    before, marker = payload()
                    if not has_type:
                        before.pop("agent_type")
                    saved = copy.deepcopy(before)
                    version = Version(before)
                    agent = types.SimpleNamespace(
                        deleted=False,
                        agent_type="VOICE",
                        latest_version=version,
                        observability_provider=None,
                    )
                    if mode == "prefetched":
                        agent._prefetched_versions = [version]
                    run = types.SimpleNamespace(
                        id="fixture-run",
                        agent_definition=agent,
                        agent_version=version if mode == "explicit" else None,
                    )
                    actual = RunSerializer(run).data["agent_version"][
                        "configuration_snapshot"
                    ]
                    self.assert_projection(
                        {**saved, "agent_type": "VOICE"}, actual, marker
                    )
                    self.assertTrue(before == saved, "run snapshot mutated")

    def test_nonsecret_boundaries_byte_identity(self):
        before = {
            "agent_name": "stable",
            "nested": [{"safe": 3}],
            "observability_enabled": False,
        }
        self.assertTrue(
            json.dumps(
                VersionSerializer(Version(before)).data["configuration_snapshot"]
            )
            == json.dumps(before)
        )

    def test_both_api_key_fields_write_only(self):
        for name in (
            "AgentDefinitionOperationSerializer",
            "AgentDefinitionUpdateSerializer",
        ):
            node = named("simulate/serializers/agent_definition.py", name)
            field = next(
                n
                for n in node.body
                if isinstance(n, ast.Assign)
                and any(
                    isinstance(t, ast.Name) and t.id == "api_key" for t in n.targets
                )
            )
            ns = {"serializers": serializers}
            execute([field], ns)
            cls = type("Input", (serializers.Serializer,), {"api_key": ns["api_key"]})
            instance = cls(data={"api_key": "fixture-input"})
            self.assertTrue(instance.is_valid())
            self.assertTrue("api_key" not in instance.data, "write-only field echoed")

    def test_update_representation_does_not_reinsert_write_only_key(self):
        instance = types.SimpleNamespace(
            active_version=None,
            latest_version=None,
            get_api_key=lambda: "fixture-input",
            knowledge_base=None,
        )
        self.assertTrue(
            "api_key" not in UpdateSerializer(instance).data,
            "write-only field reinserted",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
