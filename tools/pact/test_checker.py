"""
Integration tests for the check_codebase() production path.

test_z3_engine.py covers PactEngine (z3_engine.py).
These tests cover the checker.py → failure_mode.py → encoder.py pipeline,
which is what cli.py actually calls.
"""

import textwrap
import subprocess
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


def test_save_guard_without_assignment_does_not_make_field_optional(tmp_path):
    _write_src(
        tmp_path,
        "models.py",
        """
        from django.db import models
        class Widget(models.Model):
            slug = models.CharField(max_length=64)
            class Meta: app_label = 'x'
            def save(self, *args, **kwargs):
                if not self.slug:
                    validate_slug_source()
                return super().save(*args, **kwargs)
    """,
    )
    _write_src(
        tmp_path,
        "factory.py",
        """
        def make():
            Widget.objects.create()
    """,
    )

    violations = check_codebase(tmp_path)

    widget_v = [v for v in violations if v.call == "Widget.objects.create"]
    assert widget_v, "save guards only suppress required fields when they assign them"
    assert any("slug" in missing for missing in widget_v[0].missing)


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


def test_encoder_kwonly_arg_not_satisfied_by_extra_positional():
    from .encoder import check_function_call
    from .extractor import ArgConstraint, CallSite, FunctionManifest

    func = FunctionManifest(
        name="create_user",
        file="lib.py",
        line=1,
        module_path="lib",
        args=[
            ArgConstraint(name="name", required=True),
            ArgConstraint(name="role", required=True, kwonly=True),
        ],
    )
    call = CallSite(
        callee_name="create_user",
        file="usage.py",
        line=4,
        positional_count=2,
    )

    violation = check_function_call(call, func)

    assert violation is not None
    assert violation.missing == ["role"]


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


# ---------------------------------------------------------------------------
# optional_dereference mode
# ---------------------------------------------------------------------------


def test_optional_deref_reassignment_clears_prior_guard(tmp_path):
    _write_src(
        tmp_path,
        "views.py",
        """
        def run(users):
            user = users.first()
            if user is not None:
                user.email
            user = users.first()
            return user.name
    """,
    )

    violations = check_codebase(tmp_path)

    optional_v = [
        v
        for v in violations
        if v.context == "optional_dereference" and v.call == "user.name"
    ]
    assert optional_v, "new optional assignment must not inherit an earlier guard"


def test_optional_deref_guard_does_not_cross_function_scope(tmp_path):
    _write_src(
        tmp_path,
        "views.py",
        """
        def guarded(users):
            user = users.first()
            if user is not None:
                return user.email

        def unguarded(users):
            user = users.first()
            return user.email
    """,
    )

    violations = check_codebase(tmp_path)

    optional_v = [
        v
        for v in violations
        if v.context == "optional_dereference" and v.call == "user.email"
    ]
    assert optional_v, "optional guards are lexical to their function scope"


# ---------------------------------------------------------------------------
# graph and scanner failure contracts
# ---------------------------------------------------------------------------


def test_call_sites_to_missing_node_is_empty():
    from .graph import build_call_graph

    graph = build_call_graph([], [])

    assert graph.call_sites_to("missing") == []


def test_cli_diff_failure_exits_nonzero(monkeypatch, tmp_path, capsys):
    from . import cli

    def fail_diff(base, cwd):
        raise cli.DiffResolutionError("base branch unavailable")

    monkeypatch.setattr(cli, "_changed_files_on_branch", fail_diff)

    exit_code = cli.main(["--diff", "main", str(tmp_path)])
    captured = capsys.readouterr()

    assert exit_code == 2
    assert "base branch unavailable" in captured.err


def test_changed_files_wraps_missing_git(monkeypatch, tmp_path):
    from . import cli

    def missing_git(*args, **kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(subprocess, "run", missing_git)

    try:
        cli._changed_files_on_branch("main", cwd=tmp_path)
    except cli.DiffResolutionError as exc:
        assert "git" in str(exc)
    else:
        raise AssertionError("missing git should raise DiffResolutionError")


def test_scan_prs_reports_pact_cli_failure(monkeypatch, tmp_path):
    from . import scan_prs

    def fake_run(args, **kwargs):
        if args[:3] == ["git", "worktree", "add"]:
            return subprocess.CompletedProcess(args, 0, "", "")
        if args[:3] == ["git", "worktree", "remove"]:
            return subprocess.CompletedProcess(args, 0, "", "")
        return subprocess.CompletedProcess(args, 2, "not json", "diff failed")

    monkeypatch.setattr(scan_prs.subprocess, "run", fake_run)

    result = scan_prs.scan_branch(
        {"number": 381, "headRefName": "feature", "title": "Fix pact"},
        tmp_path,
    )

    assert result["violations"] == []
    assert result["error"] == "diff failed"


def test_scan_prs_reports_invalid_json(monkeypatch, tmp_path):
    from . import scan_prs

    def fake_run(args, **kwargs):
        if args[:3] == ["git", "worktree", "add"]:
            return subprocess.CompletedProcess(args, 0, "", "")
        if args[:3] == ["git", "worktree", "remove"]:
            return subprocess.CompletedProcess(args, 0, "", "")
        return subprocess.CompletedProcess(args, 0, "not json", "")

    monkeypatch.setattr(scan_prs.subprocess, "run", fake_run)

    result = scan_prs.scan_branch(
        {"number": 381, "headRefName": "feature", "title": "Fix pact"},
        tmp_path,
    )

    assert result["violations"] == []
    assert result["error"].startswith("invalid pact JSON:")
