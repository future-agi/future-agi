"""Guard non-execute Django cursor APIs, including deferred COPY/stream entry.

A closed list covers local cursor operations. Unknown driver callables require
an erase/migration boundary instead of silently bypassing execute_wrappers.
This is an application cursor boundary, not a sandbox against raw connections.
"""

import re
from functools import wraps

LOCAL_CALLS = frozenset(
    {
        "fetchone",
        "fetchmany",
        "fetchall",
        "close",
        "mogrify",
        "setinputsizes",
        "setoutputsize",
        "scroll",
        "nextset",
    }
)


def copy_sql(method, args, kwargs, lex):
    """Qualify table-form COPY only; opaque/query/multi-statement forms fail closed."""
    if method in {"copy_from", "copy_to"}:
        table = args[1] if len(args) > 1 else kwargs.get("table")
        if not isinstance(table, str):
            raise ValueError("unparseable COPY target")
        # Qualify schema spellings conservatively across psycopg2 versions;
        # older versions accept a qualified name, newer ones quote one name.
        text = "COPY " + table + " "
        text += "FROM STDIN" if method == "copy_from" else "TO STDOUT"
    else:
        text = args[0] if args else kwargs.get("statement", kwargs.get("sql"))
    if not isinstance(text, str):
        raise ValueError("opaque COPY statement")
    statements = list(lex(text))
    if len(statements) != 1:
        raise ValueError("COPY requires one statement")
    words = statements[0]
    if not words or words[0] != ("copy", False):
        raise ValueError("COPY requires a table target")
    index = 1

    def identifier(index):
        if index >= len(words):
            return False
        value, quoted = words[index]
        return bool(value) and (quoted or bool(re.fullmatch(r"[^\W\d][\w$]*", value)))

    if not identifier(index):
        raise ValueError("unparseable COPY target")
    index += 1
    if index < len(words) and words[index] == (".", False):
        index += 1
        if not identifier(index):
            raise ValueError("unparseable qualified COPY target")
        index += 1
    if index < len(words) and words[index] == ("(", False):
        index += 1
        while True:
            if not identifier(index):
                raise ValueError("unparseable COPY column list")
            index += 1
            if index < len(words) and words[index] == (",", False):
                index += 1
                continue
            if index >= len(words) or words[index] != (")", False):
                raise ValueError("unparseable COPY column list")
            index += 1
            break
    if index >= len(words) or words[index] not in {("from", False), ("to", False)}:
        raise ValueError("unparseable COPY direction")
    if index + 1 >= len(words):
        raise ValueError("missing COPY source/destination")
    return text


class CheckedCopy:
    """Recheck authority at deferred context entry and every COPY operation."""

    def __init__(self, context, check):
        self.context, self.check, self.copy = context, check, None

    def __enter__(self):
        self.check()
        self.copy = self.context.__enter__()
        return self

    def __exit__(self, *exc):
        # An expired successful context must abort COPY, not flush buffered rows.
        # Cleanup on an existing exception must always run.
        if exc[0] is None:
            try:
                self.check()
            except BaseException as error:
                self.context.__exit__(type(error), error, error.__traceback__)
                raise
        return self.context.__exit__(*exc)

    def __getattr__(self, name):
        value = getattr(self.copy, name)
        if not callable(value):
            return value

        @wraps(value)
        def checked(*args, **kwargs):
            self.check()
            return value(*args, **kwargs)

        return checked

    def __iter__(self):
        iterator = iter(self.copy)
        while True:
            self.check()
            try:
                yield next(iterator)
            except StopIteration:
                return


def checked_stream(iterator, check):
    try:
        while True:
            check()
            try:
                yield next(iterator)
            except StopIteration:
                return
    finally:
        iterator.close()


def install_cursor_guard(cursor_class, qualify):
    original_getattr = cursor_class.__getattr__
    original_callproc = cursor_class.callproc

    @wraps(original_getattr)
    def guarded_getattr(self, name):
        value = original_getattr(self, name)
        if not callable(value) or name in LOCAL_CALLS:
            return value

        @wraps(value)
        def guarded(*args, **kwargs):
            def check():
                qualify(self.db, name, args, kwargs)

            check()
            result = value(*args, **kwargs)
            if name == "copy":
                return CheckedCopy(result, check)
            if name == "stream":
                return checked_stream(result, check)
            return result

        return guarded

    @wraps(original_callproc)
    def guarded_callproc(self, *args, **kwargs):
        qualify(self.db, "callproc", args, kwargs)
        return original_callproc(self, *args, **kwargs)

    cursor_class.__getattr__ = guarded_getattr
    cursor_class.callproc = guarded_callproc
