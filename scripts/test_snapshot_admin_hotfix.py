"""Offline admin regression: actual Django forms, no project settings or DB.

Extract the registered admin class and model field declarations; only replace
its conversation FK with a text field so validation cannot query a database.
Mounted tests separately exercise the real models, registry and HTTP boundary.
"""

import ast
import copy
import json
import socket
import sys
import types
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "futureagi"


def deny_io(*args, **kwargs):
    raise AssertionError("offline admin test forbids network/database access")


socket.socket.connect = deny_io
socket.socket.connect_ex = deny_io
socket.socket.sendto = deny_io
socket.create_connection = deny_io
socket.getaddrinfo = deny_io
if hasattr(socket.socket, "sendmsg"):
    socket.socket.sendmsg = deny_io

import django  # noqa: E402
from django.conf import settings  # noqa: E402

settings.configure(INSTALLED_APPS=[], DATABASES={}, USE_I18N=False)
django.setup()

from django.contrib import admin  # noqa: E402
from django.contrib.admin.helpers import AdminReadonlyField  # noqa: E402
from django.db import models  # noqa: E402
from django.db.backends.base.base import BaseDatabaseWrapper  # noqa: E402
from django.utils.html import conditional_escape  # noqa: E402

BaseDatabaseWrapper.ensure_connection = deny_io


def node(path, name):
    return next(
        n
        for n in ast.parse((ROOT / path).read_text()).body
        if getattr(n, "name", None) == name
    )


def execute(nodes, scope):
    module = ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[]))
    exec(compile(module, "<admin-offline-source>", "exec"), scope)


helper = types.ModuleType("simulate.utils.snapshot_projection")
exec((ROOT / "simulate/utils/snapshot_projection.py").read_text(), helper.__dict__)
sys.modules[helper.__name__] = helper
scope = {
    "__name__": "admin_offline",
    "models": models,
    "uuid": uuid,
    "admin": admin,
    "json": json,
}
result_helper = ROOT / "ee/falcon_ai/snapshot_results.py"
if result_helper.exists():
    exec(result_helper.read_text(), scope)
base = node("tfc/utils/base_model.py", "BaseModel")
base.bases = [ast.parse("models.Model", mode="eval").body]
base.body = [
    n
    for n in base.body
    if isinstance(n, ast.Assign)
    and isinstance(n.value, ast.Call)
    and isinstance(n.value.func, ast.Attribute)
    and n.value.func.attr.endswith("Field")
]
base.body += ast.parse("class Meta:\n    abstract = True\n").body
execute([base], scope)
message = node("ee/falcon_ai/models.py", "Message")
for item in message.body:
    if isinstance(item, ast.Assign) and any(
        isinstance(t, ast.Name) and t.id == "conversation" for t in item.targets
    ):
        item.value = ast.parse("models.CharField(max_length=40)", mode="eval").body
    if isinstance(item, ast.ClassDef) and item.name == "Meta":
        item.body += ast.parse('app_label = "offline_admin"').body
execute([message], scope)
Message = scope["Message"]
admin_node = node("ee/falcon_ai/admin.py", "MessageAdmin")
admin_node.decorator_list = []
execute([admin_node], scope)
model_admin = scope["MessageAdmin"](Message, admin.AdminSite(name="offline"))
request = types.SimpleNamespace(
    user=types.SimpleNamespace(has_perm=lambda *args: True), GET={}
)
KEYS = helper.SNAPSHOT_SECRET_KEYS
SNAPSHOT_TOOLS = (
    "list_agents",
    "get_agent",
    "create_agent",
    "update_agent",
    "create_agent_version",
    "list_simulation_tests",
    "get_simulation_test",
    "create_simulation_test",
)
TOOLS = SNAPSHOT_TOOLS


def fixture(tool="get_simulation_test", broken=False):
    snapshot = {
        **dict.fromkeys(KEYS, "stored-snapshot-fixture"),
        "keep": [1, None, "<safe>"],
    }
    text = json.dumps({"configuration_snapshot": snapshot})
    calls = [{"tool_name": tool, "result_full": text[:-2] if broken else text}]
    return Message(
        conversation="fixture",
        role="assistant",
        content="keep",
        thoughts=["keep"],
        tool_calls=calls,
    )


def rendered(obj):
    form = model_admin.get_form(request, obj)(instance=obj)
    # The standalone renderer knows forms templates but no installed apps.
    form.renderer.engine.engine.dirs.append(
        str(Path(admin.__file__).parent / "templates")
    )
    parts = [str(field) for field in form]
    for name in model_admin.get_readonly_fields(request, obj):
        parts.append(str(AdminReadonlyField(form, name, False, model_admin).contents()))
    return " ".join(parts)


class AdminProjectionTests(unittest.TestCase):
    def test_every_producer_in_form_and_readonly_output(self):
        for tool in TOOLS:
            for broken in (False, True):
                with self.subTest(tool=tool, broken=broken):
                    obj = fixture(tool, broken)
                    before = copy.deepcopy(obj.tool_calls)
                    html = rendered(obj)
                    self.assertTrue(
                        "stored-snapshot-fixture" not in html,
                        "stored credential rendered",
                    )
                    self.assertEqual(obj.tool_calls, before)
                    self.assertNotIn(
                        "tool_calls", model_admin.get_form(request, obj).base_fields
                    )

    def test_explicit_allowlist_and_no_alternate_output(self):
        self.assertIsInstance(model_admin.fields, (tuple, list))
        self.assertNotIn("tool_calls", model_admin.fields or ())
        self.assertIn("display_tool_calls", model_admin.readonly_fields)
        self.assertNotIn("tool_calls", model_admin.get_list_display(request))
        self.assertNotIn("tool_calls", model_admin.get_search_fields(request))
        self.assertEqual(model_admin.inlines, ())
        self.assertEqual(model_admin.actions, ())
        self.assertIsNone(model_admin.fieldsets)
        expected = {field.name for field in Message._meta.concrete_fields} - {
            "tool_calls"
        }
        self.assertEqual(set(model_admin.fields) - {"display_tool_calls"}, expected)
        editable = {
            field.name for field in Message._meta.concrete_fields if field.editable
        } - {"tool_calls"}
        self.assertEqual(set(model_admin.get_form(request).base_fields), editable)

    def test_submitted_raw_field_is_ignored_and_save_preserves_json(self):
        obj = fixture()
        before = json.dumps(obj.tool_calls)
        form_class = model_admin.get_form(request, obj)
        data = {
            "conversation": "fixture",
            "role": "assistant",
            "content": "edited",
            "thoughts": '["keep"]',
            "tool_calls": '[{"replacement": true}]',
            "files": "[]",
            "token_count": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "latency_ms": 0,
        }
        form = form_class(data=data, instance=obj)
        self.assertTrue(
            form.is_valid(), "fixture form invalid: " + ",".join(form.errors)
        )
        saved = form.save(commit=False)
        self.assertEqual(saved.content, "edited")
        self.assertTrue(json.dumps(saved.tool_calls) == before, "stored JSON rewritten")

    def test_safe_projection_parity_and_html_escaping(self):
        display = getattr(model_admin, "display_tool_calls", None)
        self.assertTrue(callable(display), "missing projected display")
        for calls in (
            [],
            None,
            [{"tool_name": "other", "result_full": "<script>safe</script>"}],
            [{"tool_name": "get_agent", "result_full": '{ "keep": "café" }'}],
        ):
            obj = fixture()
            obj.tool_calls = copy.deepcopy(calls)
            before = copy.deepcopy(obj.tool_calls)
            value = display(obj)
            self.assertEqual(json.loads(value), calls)
            self.assertEqual(obj.tool_calls, before)
            self.assertNotIn("<script>", str(conditional_escape(value)))
        self.assertEqual(
            display.short_description,
            Message._meta.get_field("tool_calls").verbose_name,
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
