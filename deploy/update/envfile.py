"""Read and edit Compose dotenv files without disturbing anything else in them.

The updater only ever changes image version keys. Every other line, comment
and value is kept byte for byte, and secrets are never copied anywhere. New
values are plain image tags; the one exception is putting back a value
exactly as it was found (verbatim), which may be anything Compose read from
a single line, such as ${RELEASE}.

Files are read and written as UTF-8 with surrogateescape and no newline
translation, so CRLF line endings and bytes that are not UTF-8 (Compose
accepts them) come back out exactly as they went in.
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Dict, Iterator, List, Mapping, NamedTuple, Optional, Tuple

_BOM = "\ufeff"
# The start of a definition: optional export, a key, '='. A line that does
# not start like this (a comment, a blank line, anything else) defines nothing.
_DEFINITION_RE = re.compile(r"\s*(?:export\s+)?(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*=")
_KEY_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
# Only image tags are ever written: nothing Compose could interpolate.
_VALUE_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}")
# A value put back verbatim may hold anything but these: each would end the
# line or break the file.
_NOT_VERBATIM = ("\x00", "\r", "\n")
# What Compose skips after the '=': spaces, but never a line break.
_SPACES = "\t\v\f\r \x85\xa0"
# The escape sequences Compose expands in double-quoted values. \$ is "$"
# once Compose interpolates; \c matches its pattern but is no escape.
_ESCAPE_RE = re.compile(r"\\(?:[abcfnrtv$\"\\]|0[0-9]{0,3})")
_ESCAPES = {"a": "\a", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t", "v": "\v", "$": "$", '"': '"',
            "\\": "\\"}


class EnvFileError(ValueError):
    """An env file cannot be read or safely rewritten."""


class _Definition(NamedTuple):
    key: str
    value: str  # as Compose reads it
    start: int  # where its line starts (after a BOM)
    token: int  # where the value is written: its opening quote or first character
    token_end: int  # just past the value and its closing quote
    quote: str  # the quote around the value, or ""
    end: int  # just past the line ending of its last line


def _unescape(match: re.Match) -> str:
    sequence = match.group(0)
    if sequence[1] != "0":
        return _ESCAPES.get(sequence[1], sequence)
    # Octal is \0 and three octal digits. Compose drops the 0 of one it
    # cannot decode and keeps the rest as written.
    digits = sequence[2:]
    if len(digits) == 3 and set(digits) <= set("01234567") and int(digits, 8) <= 0o377:
        return chr(int(digits, 8))
    return "\\" + digits


def _quoted(text: str, start: int, quote: str) -> Optional[Tuple[str, int]]:
    """The value of the string quoted at `start`, and where its closing quote is.

    As in Compose: a backslash before the quote character makes it literal,
    any other backslash pair is kept (double quotes then expand escapes), the
    string may span lines, and CRLF inside it reads as LF. None if the quote
    is never closed.
    """

    chars: List[str] = []
    escaped = False
    for index in range(start + 1, len(text)):
        char = text[index]
        if char == "\r" and text.startswith("\n", index + 1):
            continue
        if char == quote:
            if not escaped:
                value = "".join(chars)
                return (_ESCAPE_RE.sub(_unescape, value) if quote == '"' else value), index
            chars.append(char)
            escaped = False
        elif escaped:
            chars.extend(("\\", char))
            escaped = False
        elif char == "\\":
            escaped = True
        else:
            chars.append(char)
    return None


def _definitions(text: str) -> Iterator[_Definition]:
    """Every KEY=value definition in `text`, in file order, read as Compose does.

    A leading UTF-8 BOM is skipped and only "\\n" ends a line. Text after a
    closing quote is ignored. A quote that is never closed (Compose rejects
    the file) is read as part of an unquoted value.
    """

    pos = 1 if text.startswith(_BOM) else 0
    while pos < len(text):
        newline = text.find("\n", pos)
        line_end = len(text) if newline < 0 else newline
        next_line = line_end if newline < 0 else newline + 1
        match = _DEFINITION_RE.match(text, pos, line_end)
        if match is None:
            pos = next_line
            continue
        key = match.group("key")
        token = match.end()
        while token < line_end and text[token] in _SPACES:
            token += 1
        if token < line_end and text[token] in "'\"":
            quoted = _quoted(text, token, text[token])
            if quoted is not None:
                value, close = quoted
                newline = text.find("\n", close)
                end = len(text) if newline < 0 else newline + 1
                yield _Definition(key, value, pos, token, close + 1, text[token], end)
                pos = end
                continue
        # Compose ends an unquoted value at " #" (a space, then #) and trims it.
        raw = text[token:line_end]
        cut = raw.find(" #")
        value = (raw if cut < 0 else raw[:cut]).rstrip()
        if not value:
            # Only spaces after the '=': a new value goes right after it.
            token = match.end()
        yield _Definition(key, value, pos, token, token + len(value), "", next_line)
        pos = next_line


def _read_text(path: Path, shown: Path) -> str:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise EnvFileError(f"cannot read {shown}: {exc}") from exc
    return data.decode("utf-8", "surrogateescape")


def read_env(path: Path) -> Dict[str, str]:
    """Values as Compose reads them: the last definition of a key wins.

    Variables are not expanded. Bytes that are not UTF-8 come back as
    surrogate escapes (they reach a subprocess environment unchanged).
    """

    return {definition.key: definition.value for definition in _definitions(_read_text(path, path))}


def example_keys(path: Path) -> list:
    """Keys an example file defines (comments do not count), in file order."""

    if not path.exists():
        return []
    keys = []
    for definition in _definitions(_read_text(path, path)):
        if definition.key not in keys:
            keys.append(definition.key)
    return keys


def _line_ending(text: str) -> str:
    """The line ending of the last complete line: CRLF files stay CRLF."""

    last = text.rfind("\n")
    return "\r\n" if last > 0 and text[last - 1] == "\r" else "\n"


def _allowed(value: object, verbatim: bool) -> bool:
    if value is None:
        return True
    if not isinstance(value, str):
        return False
    if verbatim:
        return not any(char in value for char in _NOT_VERBATIM)
    return value == "" or _VALUE_RE.fullmatch(value) is not None


def render_changes(text: str, changes: Mapping[str, Optional[str]], *, verbatim: bool = False) -> str:
    """Set (or, for None, remove) keys; everything else is kept verbatim.

    Only the value itself is replaced: the export prefix, the spaces around
    '=', the quotes, an inline comment and trailing whitespace all stay, so
    writing a key and then its old value again gives back the original text.
    Every definition of a key is changed; a key with none is appended.

    New values must be image tags. verbatim=True is only for putting back
    values exactly as read_env found them (${RELEASE} included): any value on
    one line is accepted, and the result must read back as exactly those
    values, or nothing is written.
    """

    for key, value in changes.items():
        # Keys and values reach this from journals and --receipt files.
        if not isinstance(key, str) or not _KEY_RE.fullmatch(key):
            raise EnvFileError(f"refusing to write unexpected key {key!r}")
        if not _allowed(value, verbatim):
            raise EnvFileError(f"refusing to write unexpected value for {key}: {value!r}")
    rendered = _render(text, changes)
    if verbatim:
        _check_reads_back(text, rendered, changes)
    return rendered


def _render(text: str, changes: Mapping[str, Optional[str]]) -> str:
    out: List[str] = []
    cursor = 0
    seen = set()
    for definition in _definitions(text):
        if definition.key not in changes:
            continue
        seen.add(definition.key)
        value = changes[definition.key]
        if value is None:
            out.append(text[cursor:definition.start])
            cursor = definition.end
        else:
            out.append(text[cursor:definition.token])
            out.append(f"{definition.quote}{value}{definition.quote}")
            cursor = definition.token_end
    out.append(text[cursor:])
    rendered = "".join(out)
    additions = [(key, value) for key, value in changes.items() if key not in seen and value is not None]
    if not additions:
        return rendered
    if rendered.endswith("\r"):
        rendered += "\n"
    elif rendered not in ("", _BOM) and not rendered.endswith("\n"):
        rendered += _line_ending(rendered)
    ending = _line_ending(rendered)
    return rendered + "".join(f"{key}={value}{ending}" for key, value in additions)


def _check_reads_back(before: str, after: str, changes: Mapping[str, Optional[str]]) -> None:
    """Refuse a verbatim write unless every definition reads back as intended.

    A value goes inside the quotes its line already has (or bare), so one
    holding that quote, a backslash escape, a leading space or ' #' would
    read back as something else, or change how the lines after it read.
    """

    expected: List[Tuple[str, str]] = []
    seen = set()
    for definition in _definitions(before):
        if definition.key not in changes:
            expected.append((definition.key, definition.value))
            continue
        seen.add(definition.key)
        value = changes[definition.key]
        if value is not None:
            expected.append((definition.key, value))
    expected.extend((key, value) for key, value in changes.items() if key not in seen and value is not None)
    actual = [(definition.key, definition.value) for definition in _definitions(after)]
    if actual == expected:
        return
    keys = [key for key, value in changes.items() if value is not None]
    wrong = [key for key in keys
             if [value for name, value in actual if name == key] != [value for name, value in expected if name == key]]
    key = (wrong or keys or list(changes))[0]
    raise EnvFileError(
        f"refusing to write back {key}: {changes[key]!r} would not read back as the same value where {key} is "
        f"defined. Set {key} in the env file by hand"
    )


def write_changes(path: Path, changes: Mapping[str, Optional[str]], *, verbatim: bool = False) -> None:
    """Atomically apply `changes` to the file `path` points at.

    A symlinked .env is edited at its target. The temporary file is created
    0600 next to the target, named like .env.* so .gitignore covers it, takes
    the original's mode and (when permitted) owner, and is removed on error.
    verbatim=True puts back values exactly as found (see render_changes).
    """

    target = Path(os.path.realpath(path))
    original = _read_text(target, path)
    try:
        info = target.stat()
    except OSError as exc:
        raise EnvFileError(f"cannot read {path}: {exc}") from exc
    rendered = render_changes(original, changes, verbatim=verbatim)
    if rendered == original:
        return
    try:
        data = rendered.encode("utf-8", "surrogateescape")
        fd, temp_name = tempfile.mkstemp(prefix=".env.futureagi-update-", suffix=".tmp", dir=str(target.parent))
    except (OSError, UnicodeError) as exc:
        raise EnvFileError(f"cannot write {path}: {exc}") from exc
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_name, info.st_mode & 0o7777)
        if hasattr(os, "chown"):
            try:
                os.chown(temp_name, info.st_uid, info.st_gid)
            except PermissionError:
                pass
        os.replace(temp_name, target)
        _fsync_dir(target.parent)
    except BaseException as exc:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        if isinstance(exc, OSError):
            raise EnvFileError(f"cannot write {path}: {exc}") from exc
        raise


def _fsync_dir(directory: Path) -> None:
    try:
        fd = os.open(str(directory), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)
