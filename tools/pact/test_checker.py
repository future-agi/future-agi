"""
Integration tests for the check_codebase() production path.

test_z3_engine.py covers PactEngine (z3_engine.py).
These tests cover the checker.py → failure_mode.py → encoder.py pipeline,
which is what cli.py actually calls.
"""

import textwrap
from pathlib import Path

import pytest

from .checker import check_codebase


def _write_src(tmp_path: Path, filename: str, source: str) -> Path:
    p = tmp_path / filename
    p.write_text(textwrap.dedent(source))
    return p


# ---------------------------------------------------------------------------
# model_constraint violations (REQUIRED_FIELD_MISSING mode)
# ---------------------------------------------------------------------------

def test_clean_create_produces_no_violation(tmp_path):
    _write_src(tmp_path, "models.py", """
        from django.db import models
        class Widget(models.Model):
            name = models.CharField(max_length=64)
            class Meta: app_label = 'x'
    """)
    _write_src(tmp_path, "views.py", """
        from .models import Widget
        def create(org):
            Widget.objects.create(name="foo")
    """)
    violations = check_codebase(tmp_path)
    assert not any(v.call == "Widget.objects.create" for v in violations)


def test_missing_required_field_flagged(tmp_path):
    _write_src(tmp_path, "models.py", """
        from django.db import models
        class Widget(models.Model):
            name = models.CharField(max_length=64)
            class Meta: app_label = 'x'
    """)
    _write_src(tmp_path, "views.py", """
        def create(org):
            Widget.objects.create()
    """)
    violations = check_codebase(tmp_path)
    widget_v = [v for v in violations if v.call == "Widget.objects.create"]
    assert widget_v, "expected model_constraint violation for Widget"
    assert any("name" in m for m in widget_v[0].missing)


def test_pre_extracted_skips_double_parse(tmp_path):
    """Passing _extracted avoids a second extract_from_codebase call."""
    from .extractor import extract_from_codebase

    _write_src(tmp_path, "models.py", """
        from django.db import models
        class Gadget(models.Model):
            sku = models.CharField(max_length=32)
            class Meta: app_label = 'x'
    """)
    _write_src(tmp_path, "factory.py", """
        def make():
            Gadget.objects.create()
    """)
    extracted = extract_from_codebase(tmp_path)
    violations = check_codebase(tmp_path, _extracted=extracted)
    gadget_v = [v for v in violations if v.call == "Gadget.objects.create"]
    assert gadget_v, "pre-extracted path should still find violation"


def test_optional_field_not_flagged(tmp_path):
    _write_src(tmp_path, "models.py", """
        from django.db import models
        class Note(models.Model):
            body = models.TextField(blank=True, null=True)
            class Meta: app_label = 'x'
    """)
    _write_src(tmp_path, "factory.py", """
        def make():
            Note.objects.create()
    """)
    violations = check_codebase(tmp_path)
    assert not any(v.call == "Note.objects.create" for v in violations)


# ---------------------------------------------------------------------------
# required_arg_missing mode
# ---------------------------------------------------------------------------

def test_top_level_function_missing_arg_flagged(tmp_path):
    """Top-level functions (no dot in name) must be checked — regression for
    the removed '.' not in callee_name guard."""
    _write_src(tmp_path, "lib.py", """
        def send_email(to, subject, body):
            pass
    """)
    _write_src(tmp_path, "usage.py", """
        from lib import send_email
        def run():
            send_email("a@b.com", "hello")
    """)
    violations = check_codebase(tmp_path)
    # The call `send_email("a@b.com", "hello")` has 2 positional args but
    # send_email requires 3 — body is missing.
    missing_arg_v = [
        v for v in violations
        if v.context == "required_arg_missing" and "send_email" in v.call
    ]
    assert missing_arg_v, "top-level function call missing required arg should be flagged"


def test_kwonly_required_arg_flagged(tmp_path):
    """Keyword-only required args (after *) must be in FunctionManifest."""
    _write_src(tmp_path, "lib.py", """
        def create_user(name, *, role):
            pass
    """)
    _write_src(tmp_path, "usage.py", """
        from lib import create_user
        def run():
            create_user("Alice")
    """)
    violations = check_codebase(tmp_path)
    kwonly_v = [
        v for v in violations
        if v.context == "required_arg_missing" and "create_user" in v.call
    ]
    assert kwonly_v, "missing required kwarg-only arg should be flagged"
    assert "role" in kwonly_v[0].missing


# ---------------------------------------------------------------------------
# bare_except mode
# ---------------------------------------------------------------------------

def test_bare_except_flagged(tmp_path):
    _write_src(tmp_path, "handler.py", """
        def process(data):
            try:
                do_work(data)
            except:
                pass
    """)
    violations = check_codebase(tmp_path)
    bare_v = [v for v in violations if v.context == "bare_except"]
    assert bare_v, "bare except: should be flagged"
    assert any("except:" in v.call for v in bare_v)


def test_silent_except_exception_flagged(tmp_path):
    _write_src(tmp_path, "handler.py", """
        def process(data):
            try:
                do_work(data)
            except Exception:
                pass
    """)
    violations = check_codebase(tmp_path)
    bare_v = [v for v in violations if v.context == "bare_except"]
    assert bare_v, "silent except Exception: pass should be flagged"


def test_except_exception_with_logging_not_flagged(tmp_path):
    _write_src(tmp_path, "handler.py", """
        import logging
        logger = logging.getLogger(__name__)
        def process(data):
            try:
                do_work(data)
            except Exception as exc:
                logger.exception("failed", error=str(exc))
    """)
    violations = check_codebase(tmp_path)
    bare_v = [v for v in violations if v.context == "bare_except"]
    assert not bare_v, "except with logging body should not be flagged"


def test_specific_exception_not_flagged(tmp_path):
    _write_src(tmp_path, "handler.py", """
        def process(data):
            try:
                do_work(data)
            except ValueError:
                pass
    """)
    violations = check_codebase(tmp_path)
    bare_v = [v for v in violations if v.context == "bare_except"]
    assert not bare_v, "specific exception type should not be flagged"


# ---------------------------------------------------------------------------
# save_without_update_fields mode
# ---------------------------------------------------------------------------

def test_save_without_update_fields_flagged(tmp_path):
    _write_src(tmp_path, "views.py", """
        def update(obj):
            obj.name = "new"
            obj.save()
    """)
    violations = check_codebase(tmp_path)
    save_v = [v for v in violations if v.context == "save_without_update_fields"]
    assert save_v, "save() without update_fields should be flagged"


def test_save_with_update_fields_not_flagged(tmp_path):
    _write_src(tmp_path, "views.py", """
        def update(obj):
            obj.name = "new"
            obj.save(update_fields=["name"])
    """)
    violations = check_codebase(tmp_path)
    save_v = [v for v in violations if v.context == "save_without_update_fields"]
    assert not save_v, "save(update_fields=[...]) should not be flagged"


def test_form_save_not_flagged(tmp_path):
    _write_src(tmp_path, "views.py", """
        def handle(request):
            form = MyForm(request.POST)
            if form.is_valid():
                form.save()
    """)
    violations = check_codebase(tmp_path)
    save_v = [v for v in violations if v.context == "save_without_update_fields"]
    assert not save_v, "form.save() should not be flagged"


def test_compound_serializer_save_not_flagged(tmp_path):
    _write_src(tmp_path, "views.py", """
        def update(request, pk):
            user_serializer = UserSerializer(data=request.data)
            if user_serializer.is_valid():
                user_serializer.save()
    """)
    violations = check_codebase(tmp_path)
    save_v = [v for v in violations if v.context == "save_without_update_fields"]
    assert not save_v, "compound *_serializer.save() should not be flagged"


def test_profile_save_is_flagged(tmp_path):
    """'profile'.endswith('file') is True — must NOT be whitelisted by the suffix check."""
    _write_src(tmp_path, "views.py", """
        def update_profile(user, name):
            profile = user.profile
            profile.name = name
            profile.save()
    """)
    violations = check_codebase(tmp_path)
    save_v = [v for v in violations if v.context == "save_without_update_fields"]
    assert save_v, "profile.save() should be flagged — 'profile' ends with 'file' but is not a file object"


# ---------------------------------------------------------------------------
# mutable_default_arg mode
# ---------------------------------------------------------------------------

def test_list_default_flagged(tmp_path):
    _write_src(tmp_path, "lib.py", """
        def append_item(item, items=[]):
            items.append(item)
            return items
    """)
    violations = check_codebase(tmp_path)
    v = [v for v in violations if v.context == "mutable_default_arg"]
    assert v, "list default should be flagged"
    assert any("list" in m for m in v[0].missing)


def test_dict_default_flagged(tmp_path):
    _write_src(tmp_path, "lib.py", """
        def update_cache(key, cache={}):
            cache[key] = True
            return cache
    """)
    violations = check_codebase(tmp_path)
    v = [v for v in violations if v.context == "mutable_default_arg"]
    assert v, "dict default should be flagged"
    assert any("dict" in m for m in v[0].missing)


def test_none_default_not_flagged(tmp_path):
    _write_src(tmp_path, "lib.py", """
        def append_item(item, items=None):
            if items is None:
                items = []
            items.append(item)
            return items
    """)
    violations = check_codebase(tmp_path)
    v = [v for v in violations if v.context == "mutable_default_arg"]
    assert not v, "None default is the correct pattern — should not be flagged"


def test_immutable_default_not_flagged(tmp_path):
    _write_src(tmp_path, "lib.py", """
        def greet(name="world", count=0, flag=True):
            return f"Hello {name}"
    """)
    violations = check_codebase(tmp_path)
    v = [v for v in violations if v.context == "mutable_default_arg"]
    assert not v, "str/int/bool defaults should not be flagged"


# ---------------------------------------------------------------------------
# missing_await mode
# ---------------------------------------------------------------------------

def test_missing_await_flagged(tmp_path):
    _write_src(tmp_path, "tasks.py", """
        import asyncio

        async def fetch_data(url):
            return url

        def start():
            fetch_data("http://example.com")  # missing await
    """)
    violations = check_codebase(tmp_path)
    v = [v for v in violations if v.context == "missing_await"]
    assert v, "unawaited coroutine call should be flagged"
    assert v[0].call == "fetch_data"


def test_awaited_call_not_flagged(tmp_path):
    _write_src(tmp_path, "tasks.py", """
        async def fetch_data(url):
            return url

        async def start():
            result = await fetch_data("http://example.com")
    """)
    violations = check_codebase(tmp_path)
    v = [v for v in violations if v.context == "missing_await"]
    assert not v, "properly awaited call should not be flagged"


def test_sync_function_not_flagged_as_missing_await(tmp_path):
    _write_src(tmp_path, "lib.py", """
        def compute(x):
            return x * 2

        def run():
            result = compute(5)
    """)
    violations = check_codebase(tmp_path)
    v = [v for v in violations if v.context == "missing_await"]
    assert not v, "sync function call should not be flagged as missing await"


# ---------------------------------------------------------------------------
# format_arg_mismatch mode
# ---------------------------------------------------------------------------

def test_positional_format_mismatch_flagged(tmp_path):
    _write_src(tmp_path, "lib.py", """
        def greet(name):
            msg = "{} {} {}".format(name)
    """)
    violations = check_codebase(tmp_path)
    v = [v for v in violations if v.context == "format_arg_mismatch"]
    assert v, "too few positional args should be flagged"
    assert any("3" in m and "1" in m for m in v[0].missing)


def test_named_format_missing_kwarg_flagged(tmp_path):
    _write_src(tmp_path, "lib.py", """
        def greet():
            msg = "Hello {name}, you are {age} years old".format(name="Alice")
    """)
    violations = check_codebase(tmp_path)
    v = [v for v in violations if v.context == "format_arg_mismatch"]
    assert v, "missing named kwarg should be flagged"
    assert any("age" in m for m in v[0].missing)


def test_correct_format_not_flagged(tmp_path):
    _write_src(tmp_path, "lib.py", """
        def greet(name, age):
            msg = "Hello {}, you are {} years old".format(name, age)
    """)
    violations = check_codebase(tmp_path)
    v = [v for v in violations if v.context == "format_arg_mismatch"]
    assert not v, "correct positional format should not be flagged"


def test_format_with_star_args_not_flagged(tmp_path):
    _write_src(tmp_path, "lib.py", """
        def greet(args):
            msg = "{} {}".format(*args)
    """)
    violations = check_codebase(tmp_path)
    v = [v for v in violations if v.context == "format_arg_mismatch"]
    assert not v, "format with *args splice cannot be statically counted — should not be flagged"


# ---------------------------------------------------------------------------
# llm_response_unguarded mode
# ---------------------------------------------------------------------------

def test_llm_choices_unguarded_flagged(tmp_path):
    _write_src(tmp_path, "handler.py", """
        import openai

        def get_reply(prompt):
            response = openai.chat.completions.create(
                model="gpt-4",
                messages=[{"role": "user", "content": prompt}],
            )
            return response.choices[0].message.content
    """)
    violations = check_codebase(tmp_path)
    v = [v for v in violations if v.context == "llm_response_unguarded"]
    assert v, "unguarded response.choices[0] should be flagged"
    assert "choices" in v[0].call


def test_llm_choices_guarded_not_flagged(tmp_path):
    _write_src(tmp_path, "handler.py", """
        import openai

        def get_reply(prompt):
            response = openai.chat.completions.create(
                model="gpt-4",
                messages=[{"role": "user", "content": prompt}],
            )
            if not response.choices:
                return None
            return response.choices[0].message.content
    """)
    violations = check_codebase(tmp_path)
    v = [v for v in violations if v.context == "llm_response_unguarded"]
    assert not v, "guarded choices access should not be flagged"
