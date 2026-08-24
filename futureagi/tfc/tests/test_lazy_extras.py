"""Tests for `tfc.utils.lazy_extras` — the single boundary for imports of
packages that live in optional extras groups (slim OSS image support).

Covers both entry points:

- `_MissingExtra` module proxies (used for module-level imports like `av`),
- `load_extra()` (used at function-scoped call sites, incl. submodules),

plus `extra_available()` (capability gating) and the repr/attribute-access
behaviors reviewers flagged: repr must never raise (structlog dumps locals),
and every failure message must name the extras group to install.
"""

from __future__ import annotations

import pytest
from tfc.utils import lazy_extras
from tfc.utils.lazy_extras import (
    _MissingExtra,
    _try_import,
    extra_available,
    load_extra,
)

_BOGUS = "definitely_not_an_installed_package_xyz"


# ── _MissingExtra proxy ───────────────────────────────────────────────────
def test_missing_extra_attribute_access_raises_actionable_importerror():
    proxy = _MissingExtra(_BOGUS, "audio")
    with pytest.raises(ImportError) as excinfo:
        proxy.some_attribute
    message = str(excinfo.value)
    assert _BOGUS in message
    assert "audio" in message
    # Docker users can't `pip install core-backend[...]` (not on PyPI); the
    # hint must point at the image rebuild path instead.
    assert "EXTRAS=audio" in message
    assert "INSTALLATION.md" in message


def test_missing_extra_call_raises_importerror():
    proxy = _MissingExtra(_BOGUS, "voice")
    with pytest.raises(ImportError, match="voice"):
        proxy()


def test_missing_extra_repr_does_not_raise():
    """structlog/debuggers repr() locals; that must never trip ImportError."""
    proxy = _MissingExtra(_BOGUS, "ml")
    text = repr(proxy)
    assert _BOGUS in text
    assert "ml" in text


def test_try_import_returns_real_module_when_installed():
    module = _try_import("json", "audio")
    assert not isinstance(module, _MissingExtra)
    assert module.dumps({"a": 1}) == '{"a": 1}'


def test_try_import_returns_proxy_when_missing():
    module = _try_import(_BOGUS, "pii")
    assert isinstance(module, _MissingExtra)


# ── load_extra() ──────────────────────────────────────────────────────────
def test_load_extra_returns_module_when_installed():
    module = load_extra("email.mime.text", "audio")
    assert module.MIMEText is not None


def test_load_extra_raises_actionable_importerror_when_missing():
    with pytest.raises(ImportError) as excinfo:
        load_extra(_BOGUS, "vectordb")
    message = str(excinfo.value)
    assert _BOGUS in message
    assert "vectordb" in message
    # Original ImportError chained for debugging.
    assert excinfo.value.__cause__ is not None


def test_load_extra_supports_submodule_paths_when_missing():
    with pytest.raises(ImportError, match="voice"):
        load_extra(f"{_BOGUS}.lib.webhook_auth", "voice")


# ── extra_available() ─────────────────────────────────────────────────────
def test_extra_available_true_for_installed_package():
    assert extra_available("json") is True


def test_extra_available_false_for_missing_package():
    assert extra_available(_BOGUS) is False


# ── module-level proxies ──────────────────────────────────────────────────
def test_audio_proxies_are_module_objects():
    """Whether or not the audio extra is installed in this env, the names
    must exist and be module objects (real or proxy) — call sites do
    `from tfc.utils.lazy_extras import av` at module level."""
    import types

    assert isinstance(lazy_extras.av, types.ModuleType)
    assert isinstance(lazy_extras.soundfile, types.ModuleType)
