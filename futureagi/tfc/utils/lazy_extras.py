"""Single boundary for imports of packages that live in optional extras.

Two entry points:

- Module proxies (``av``, ``soundfile``) for call sites that need a
  module-level import to keep working on slim images. Attribute access on
  a missing package raises ``ImportError`` naming the extra.
- ``load_extra(module_path, extras_group)`` for function-scoped imports
  (including submodules like ``elevenlabs.client``). Raises the same
  actionable ``ImportError`` when the package is absent.

Route every function-scoped import of an extras package through this
module so "extra not installed" always fails the same way, with a message
that names the extra to install — never a bare ``ModuleNotFoundError``.
"""

import importlib
import importlib.util
import types


def _install_hint(module_name: str, extras_group: str) -> str:
    return (
        f"`{module_name}` is part of the optional `{extras_group}` "
        f"dependency group, which is not installed in this build. "
        f"For source installs run `uv sync --extra {extras_group}` "
        f"(or `pip install '.[{extras_group}]'`). For Docker deployments, "
        f"rebuild the image with `--build-arg EXTRAS={extras_group}` "
        f"(see INSTALLATION.md § 'Optional feature extras'), or use the "
        f"EE image, which bundles all extras."
    )


class _MissingExtra(types.ModuleType):
    """Stand-in for a package that lives in an optional extras group.

    Every attribute access raises ``ImportError`` with a message naming
    the missing extras group so the operator knows how to get it."""

    def __init__(self, name: str, extras_group: str):
        super().__init__(name)
        self._extras_group = extras_group

    def __getattr__(self, attr: str):
        raise ImportError(_install_hint(self.__name__, self._extras_group))

    def __call__(self, *args, **kwargs):
        self.__getattr__("__call__")

    # Explicit __repr__ so log processors and debuggers dumping locals
    # (e.g. structlog rendering a frame) print a marker instead of
    # tripping __getattr__'s ImportError.
    def __repr__(self) -> str:
        return (
            f"<missing optional module {self.__name__!r} "
            f"(extra: {self._extras_group!r})>"
        )


def _try_import(module_name: str, extras_group: str) -> types.ModuleType:
    try:
        return importlib.import_module(module_name)
    except ImportError:
        return _MissingExtra(module_name, extras_group)


def load_extra(module_path: str, extras_group: str) -> types.ModuleType:
    """Import (and return) a module that ships in an optional extras group.

    For function-scoped call sites, including submodule paths::

        ElevenLabs = load_extra("elevenlabs.client", "audio").ElevenLabs

    Raises ``ImportError`` with a message naming the extra when the
    package is not installed."""
    try:
        return importlib.import_module(module_path)
    except ImportError as exc:
        raise ImportError(_install_hint(module_path, extras_group)) from exc


def extra_available(module_path: str) -> bool:
    """True when a package from an optional extras group is installed.

    Use for capability gating (e.g. deny voice-sim when `livekit` is
    absent) without importing the package."""
    return importlib.util.find_spec(module_path) is not None


# audio extras
av = _try_import("av", "audio")
soundfile = _try_import("soundfile", "audio")
