"""Abort startup rather than silently continuing when proof containment fails."""
import os

try:
    import python_guard  # noqa: F401
except BaseException:
    os.write(2, b"EDITOR_GUARD_STARTUP_FAILED\n")
    os._exit(98)
