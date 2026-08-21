import importlib
import types


class _MissingExtra(types.ModuleType):
    """Stand-in for a package that lives in an optional extras group.

    Every attribute access raises ``ImportError`` with a message naming
    the missing extras group so the operator knows which ``pip install``
    command to run.
    """

    def __init__(self, name: str, extras_group: str):
        super().__init__(name)
        self._extras_group = extras_group

    def __getattr__(self, attr: str):
        raise ImportError(
            f"`{self.__name__}` requires the `{self._extras_group}` extra. "
            f"Install with: pip install 'core-backend[{self._extras_group}]'"
        )

    def __call__(self, *args, **kwargs):
        self.__getattr__("__call__")


def _try_import(module_name: str, extras_group: str) -> types.ModuleType:
    try:
        return importlib.import_module(module_name)
    except ImportError:
        return _MissingExtra(module_name, extras_group)


# audio extras
av = _try_import("av", "audio")
soundfile = _try_import("soundfile", "audio")

# voice extras
retell = _try_import("retell", "voice")
