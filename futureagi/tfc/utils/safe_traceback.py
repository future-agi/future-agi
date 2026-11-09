"""Failure-origin diagnostics without exception messages, source or local values."""

from pathlib import Path
from traceback import walk_tb


def sanitized_exception_traceback(exc):
    """Retain frames and chained causes without passing live exceptions to logging.

    HTTP/database exception strings and attached request objects can contain
    credentials or payloads. Even exc_info on a message-redacted exception exposes
    frame locals to some logging processors. Emit only code locations and types.
    Bound chains and frame counts so a failure cannot create unbounded log data.
    """
    chain = []
    seen = set()
    while exc is not None and id(exc) not in seen and len(chain) < 8:
        seen.add(id(exc))
        frames = [
            {
                "file": Path(frame.f_code.co_filename).name,
                "function": frame.f_code.co_name,
                "line": line,
            }
            for frame, line in walk_tb(exc.__traceback__)
        ]
        cause = exc.__cause__
        context = None if exc.__suppress_context__ else exc.__context__
        chain.append(
            {
                "error_type": type(exc).__name__,
                "frames": frames[-32:],
                "chained_by": "cause" if cause is not None else "context" if context is not None else None,
            }
        )
        exc = cause if cause is not None else context
    return chain
