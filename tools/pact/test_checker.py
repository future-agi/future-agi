"""
Integration tests for the check_codebase() production path.

test_z3_engine.py covers PactEngine (z3_engine.py).
These tests cover the checker.py → failure_mode.py → encoder.py pipeline,
which is what cli.py actually calls.
"""

import textwrap
from pathlib import Path

from .checker import check_codebase


def _write_src(tmp_path: Path, filename: str, source: str) -> Path:
    p = tmp_path / filename
    p.write_text(textwrap.dedent(source))
    return p


# ---------------------------------------------------------------------------
# model_constraint violations (REQUIRED_FIELD_MISSING mode)
# ---------------------------------------------------------------------------


def test_clean_create_produces_no_violation(tmp_path):
    _write_src(
        tmp_path,
        "models.py",
        """
        from django.db import models
        class Widget(models.Model):
            name = models.CharField(max_length=64)
            class Meta: app_label = 'x'
    """,
    )
    _write_src(
        tmp_path,
        "views.py",
        """
        from .models import Widget
        def create(org):
            Widget.objects.create(name="foo")
    """,
    )
    violations = check_codebase(tmp_path)
    assert not any(v.call == "Widget.objects.create" for v in violations)


def test_missing_required_field_flagged(tmp_path):
    _write_src(
        tmp_path,
        "models.py",
        """
        from django.db import models
        class Widget(models.Model):
            name = models.CharField(max_length=64)
            class Meta: app_label = 'x'
    """,
    )
    _write_src(
        tmp_path,
        "views.py",
        """
        def create(org):
            Widget.objects.create()
    """,
    )
    violations = check_codebase(tmp_path)
    widget_v = [v for v in violations if v.call == "Widget.objects.create"]
    assert widget_v, "expected model_constraint violation for Widget"
    assert any("name" in m for m in widget_v[0].missing)


def test_pre_extracted_skips_double_parse(tmp_path):
    """Passing _extracted avoids a second extract_from_codebase call."""
    from .extractor import extract_from_codebase

    _write_src(
        tmp_path,
        "models.py",
        """
        from django.db import models
        class Gadget(models.Model):
            sku = models.CharField(max_length=32)
            class Meta: app_label = 'x'
    """,
    )
    _write_src(
        tmp_path,
        "factory.py",
        """
        def make():
            Gadget.objects.create()
    """,
    )
    extracted = extract_from_codebase(tmp_path)
    violations = check_codebase(tmp_path, _extracted=extracted)
    gadget_v = [v for v in violations if v.call == "Gadget.objects.create"]
    assert gadget_v, "pre-extracted path should still find violation"


def test_optional_field_not_flagged(tmp_path):
    _write_src(
        tmp_path,
        "models.py",
        """
        from django.db import models
        class Note(models.Model):
            body = models.TextField(blank=True, null=True)
            class Meta: app_label = 'x'
    """,
    )
    _write_src(
        tmp_path,
        "factory.py",
        """
        def make():
            Note.objects.create()
    """,
    )
    violations = check_codebase(tmp_path)
    assert not any(v.call == "Note.objects.create" for v in violations)


# ---------------------------------------------------------------------------
# required_arg_missing mode
# ---------------------------------------------------------------------------


def test_top_level_function_missing_arg_flagged(tmp_path):
    """Top-level functions (no dot in name) must be checked — regression for
    the removed '.' not in callee_name guard."""
    _write_src(
        tmp_path,
        "lib.py",
        """
        def send_email(to, subject, body):
            pass
    """,
    )
    _write_src(
        tmp_path,
        "usage.py",
        """
        from lib import send_email
        def run():
            send_email("a@b.com", "hello")
    """,
    )
    violations = check_codebase(tmp_path)
    # The call `send_email("a@b.com", "hello")` has 2 positional args but
    # send_email requires 3 — body is missing.
    missing_arg_v = [
        v
        for v in violations
        if v.context == "required_arg_missing" and "send_email" in v.call
    ]
    assert (
        missing_arg_v
    ), "top-level function call missing required arg should be flagged"


def test_kwonly_required_arg_flagged(tmp_path):
    """Keyword-only required args (after *) must be in FunctionManifest."""
    _write_src(
        tmp_path,
        "lib.py",
        """
        def create_user(name, *, role):
            pass
    """,
    )
    _write_src(
        tmp_path,
        "usage.py",
        """
        from lib import create_user
        def run():
            create_user("Alice")
    """,
    )
    violations = check_codebase(tmp_path)
    kwonly_v = [
        v
        for v in violations
        if v.context == "required_arg_missing" and "create_user" in v.call
    ]
    assert kwonly_v, "missing required kwarg-only arg should be flagged"
    assert "role" in kwonly_v[0].missing


# ---------------------------------------------------------------------------
# bare_except mode
# ---------------------------------------------------------------------------


def test_bare_except_flagged(tmp_path):
    _write_src(
        tmp_path,
        "handler.py",
        """
        def process(data):
            try:
                do_work(data)
            except:
                pass
    """,
    )
    violations = check_codebase(tmp_path)
    bare_v = [v for v in violations if v.context == "bare_except"]
    assert bare_v, "bare except: should be flagged"
    assert any("except:" in v.call for v in bare_v)


def test_bare_except_in_file_without_calls_is_flagged(tmp_path):
    _write_src(
        tmp_path,
        "constants.py",
        """
        try:
            value = 1 / 0
        except:
            value = None
    """,
    )
    violations = check_codebase(tmp_path)
    bare_v = [v for v in violations if v.context == "bare_except"]
    assert bare_v, "file-level bare except scan must not depend on extracted calls"


def test_silent_except_exception_flagged(tmp_path):
    _write_src(
        tmp_path,
        "handler.py",
        """
        def process(data):
            try:
                do_work(data)
            except Exception:
                pass
    """,
    )
    violations = check_codebase(tmp_path)
    bare_v = [v for v in violations if v.context == "bare_except"]
    assert bare_v, "silent except Exception: pass should be flagged"


def test_except_exception_with_logging_not_flagged(tmp_path):
    _write_src(
        tmp_path,
        "handler.py",
        """
        import logging
        logger = logging.getLogger(__name__)
        def process(data):
            try:
                do_work(data)
            except Exception as exc:
                logger.exception("failed", error=str(exc))
    """,
    )
    violations = check_codebase(tmp_path)
    bare_v = [v for v in violations if v.context == "bare_except"]
    assert not bare_v, "except with logging body should not be flagged"


def test_specific_exception_not_flagged(tmp_path):
    _write_src(
        tmp_path,
        "handler.py",
        """
        def process(data):
            try:
                do_work(data)
            except ValueError:
                pass
    """,
    )
    violations = check_codebase(tmp_path)
    bare_v = [v for v in violations if v.context == "bare_except"]
    assert not bare_v, "specific exception type should not be flagged"


# ---------------------------------------------------------------------------
# save_without_update_fields mode
# ---------------------------------------------------------------------------


def test_save_without_update_fields_flagged(tmp_path):
    _write_src(
        tmp_path,
        "views.py",
        """
        def update(obj):
            obj.name = "new"
            obj.save()
    """,
    )
    violations = check_codebase(tmp_path)
    save_v = [v for v in violations if v.context == "save_without_update_fields"]
    assert save_v, "save() without update_fields should be flagged"


def test_save_with_update_fields_not_flagged(tmp_path):
    _write_src(
        tmp_path,
        "views.py",
        """
        def update(obj):
            obj.name = "new"
            obj.save(update_fields=["name"])
    """,
    )
    violations = check_codebase(tmp_path)
    save_v = [v for v in violations if v.context == "save_without_update_fields"]
    assert not save_v, "save(update_fields=[...]) should not be flagged"


def test_form_save_not_flagged(tmp_path):
    _write_src(
        tmp_path,
        "views.py",
        """
        def handle(request):
            form = MyForm(request.POST)
            if form.is_valid():
                form.save()
    """,
    )
    violations = check_codebase(tmp_path)
    save_v = [v for v in violations if v.context == "save_without_update_fields"]
    assert not save_v, "form.save() should not be flagged"


def test_profile_save_without_update_fields_flagged(tmp_path):
    _write_src(
        tmp_path,
        "views.py",
        """
        def update(profile):
            profile.name = "new"
            profile.save()
    """,
    )
    violations = check_codebase(tmp_path)
    save_v = [v for v in violations if v.context == "save_without_update_fields"]
    assert save_v, "profile.save() is a model save, not a safe file receiver"
