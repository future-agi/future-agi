"""Render logging's diagnostic fallback before writing it to stderr."""

import io
import logging
import os
import sys
import traceback

# The StreamHandler emit boundary (checkout_output.py) is skipped, like
# logging's own frames, when locating the caller for the "Call stack" section.
_BOUNDARY = os.path.join(os.path.dirname(__file__), "checkout_output.py")


def handle_error(handler, record):
    """Mirror stdlib handleError, with a final text buffer instead of raw writes.

    The message, arguments and current exception are rendered exactly once at
    the same native uses. Buffering does not evaluate a record or its carriers.
    No process-global stream is replaced: concurrent/nested logging keeps its
    own output boundary. raiseExceptions and OSError/RecursionError behavior
    follow the stdlib implementation.
    """
    if not logging.raiseExceptions or not sys.stderr:
        return
    from .checkout_output import scrub_rendered

    output = io.StringIO()
    error = sys.exception()
    try:
        output.write("--- Logging error ---\n")
        traceback.print_exception(error, limit=None, file=output)
        output.write("Call stack:\n")
        frame = error.__traceback__.tb_frame
        while frame and (
            os.path.dirname(frame.f_code.co_filename) == logging.__path__[0]
            or frame.f_code.co_filename == _BOUNDARY
        ):
            frame = frame.f_back
        if frame:
            traceback.print_stack(frame, file=output)
        else:
            output.write(
                "Logged from file %s, line %s\n" % (record.filename, record.lineno)  # noqa: UP031
            )
        try:
            # Keep native %-formatting: format() can evaluate different hooks.
            output.write("Message: %r\nArguments: %s\n" % (record.msg, record.args))  # noqa: UP031
        except RecursionError:
            raise
        except Exception:
            output.write(
                "Unable to print the message and arguments"
                " - possible formatting error.\nUse the"
                " traceback above to help find the error.\n"
            )
    except OSError:
        pass
    finally:
        del error
        try:
            sys.stderr.write(scrub_rendered(output.getvalue()))
        except OSError:
            pass
        output.close()
