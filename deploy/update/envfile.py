"""Read and edit Compose dotenv files without disturbing anything else in them.

The updater only ever changes image version keys. Every unrelated byte is
kept; unchanged env contents are not copied into recovery records, and lexical
recovery data never enters receipts. New values are plain image tags. Transaction recovery reverses saved lexical
edits, not parsed values: duplicate definitions and dollar escaping retain
their original semantics. A legacy verbatim helper refuses ambiguous dollars.

Files are read and written as UTF-8 with surrogateescape and no newline
translation, so CRLF line endings and bytes that are not UTF-8 (Compose
accepts them) come back out exactly as they went in.

The reading mirrors Compose's own dotenv parser (compose-go's dotenv/parser.go:
getStatementStart, locateKeyName, extractVarValue, expandEscapes), checked
against docker-compose v2.24.0 and v5.6.0 with `docker-compose config`. Where
Compose rejects a file, it is read on as best it can; it never reaches here
through bin/update, whose plan has Compose read the file first.
"""

from __future__ import annotations

import hashlib
import os
import re
import unicodedata
from . import envcommit
from pathlib import Path
from typing import Dict, Iterator, List, Mapping, NamedTuple, Optional, Tuple

_BOM = "\ufeff"
# What Compose skips around a key and after the '=': spaces, but never a line break.
_SPACES = "\t\v\f\r \x85\xa0"
# Go's unicode.IsSpace: what Compose skips between definitions and trims off
# the end of an unquoted value. (Python's \s and str.strip also take \x1c-\x1f.)
_UNICODE_SPACES = _SPACES + "\n\u1680" + "".join(map(chr, range(0x2000, 0x200B))) + "\u2028\u2029\u202f\u205f\u3000"
# A definition may start with export and one of Go's \s ([\t\n\f\r ]).
_EXPORT_RE = re.compile("export[\t\n\f\r ]")
# What a key may hold besides letters, digits and _SPACES, as in Compose.
_KEY_PUNCTUATION = "_.-[]"
_KEY_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
# Only image tags are ever written: nothing Compose could interpolate.
_VALUE_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}")
# A value put back verbatim may hold anything but these: each would end the
# line or break the file.
_NOT_VERBATIM = ("\x00", "\r", "\n")
# The escape sequences Compose expands in double-quoted values. \$ is "$"
# once Compose interpolates; \c matches its pattern but is no escape.
_ESCAPE_RE = re.compile(r"\\(?:[abcfnrtv$\"\\]|0[0-9]{0,3})")
_ESCAPES = {"a": "\a", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t", "v": "\v", "$": "$", '"': '"',
            "\\": "\\"}


class EnvFileError(ValueError):
    """An env file cannot be read or safely rewritten."""


class EnvCommitError(EnvFileError):
    """Keep the journal and retained entries; automatic restoration is unsafe."""


class EnvPreimageError(EnvFileError):
    """The env file is not the exact preimage the caller approved: nothing was prepared or written."""


class _Definition(NamedTuple):
    key: str
    value: str  # as Compose reads it
    start: int  # where its line starts (after a BOM), or just past the closing quote it follows
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

    Compose's extractVarValue for a quoted value: a backslash before the
    quote character makes it literal, any other backslash pair is kept
    (double quotes then expand escapes, as expandEscapes does), and the
    string may span lines (a CRLF inside it stays CRLF). None if the quote
    is never closed.
    """

    chars: List[str] = []
    escaped = False
    for index in range(start + 1, len(text)):
        char = text[index]
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


def _statement(text: str, pos: int) -> Optional[int]:
    """Where the next definition starts at or after `pos`: past spaces, line breaks and comment lines.

    Compose's getStatementStart.
    """

    while True:
        while pos < len(text) and text[pos] in _UNICODE_SPACES:
            pos += 1
        if pos == len(text):
            return None
        if text[pos] != "#":
            return pos
        pos = text.find("\n", pos)
        if pos < 0:
            return None


def _key(text: str, pos: int) -> Optional[Tuple[str, int]]:
    """The key of the definition at `pos` and where its value starts (just past the '=' or ':').

    Compose's locateKeyName: an export and its spaces are skipped, and a key
    ends at the first '=' or ':'. It may hold letters and digits of any
    script, any of _KEY_PUNCTUATION and _SPACES (KEY = value), but no space
    once trailing spaces are trimmed. None for a line that is a bare KEY
    (Compose takes it from the environment) or that Compose rejects.
    """

    if _EXPORT_RE.match(text, pos):
        pos += len("export")
        while pos < len(text) and text[pos] in _SPACES:
            pos += 1
    begin = pos
    while pos < len(text) and text[pos] not in "=:\n":
        char = text[pos]
        category = unicodedata.category(char)
        # Go/Compose may know letters a supported Python's older Unicode table
        # calls unassigned. Never lose their quote boundaries and interpret the
        # next line of their value as a version definition. Compose validates
        # the file; accepting an unknown codepoint here is conservative.
        if char not in _SPACES and char not in _KEY_PUNCTUATION and category[0] not in "LN" and category != "Cn":
            return None
        pos += 1
    if pos == len(text) or text[pos] == "\n":
        return None
    key = text[begin:pos].rstrip(_UNICODE_SPACES)
    return (key, pos + 1) if " " not in key else None


def _definitions(text: str) -> Iterator[_Definition]:
    """Every KEY=value definition in `text`, in file order, read as Compose does.

    Compose's parse loop. A leading UTF-8 BOM is skipped and only "\\n" ends
    a line. After a closing quote Compose reads on: another definition may
    follow on that line (A="x" B=1). A line Compose rejects (or a bare KEY)
    defines nothing and the next line is read; a quote that is never closed
    (Compose rejects the file) is read as part of an unquoted value. Keys
    Compose reads that no variable can name (a.b, export\\vA) are definitions
    too: a quoted value of theirs may span lines. An empty key's value is
    read past, but it defines nothing (v2.24.0 rejects the file; v5.6.0 reads
    it under a name nothing can use).
    """

    pos = 1 if text.startswith(_BOM) else 0
    first = pos
    while True:
        statement = _statement(text, pos)
        if statement is None:
            return
        # One that follows a closing quote on its line starts there and ends
        # with its value: removing it keeps the rest of that line.
        newline = text.rfind("\n", pos, statement)
        after_quote = newline < 0 and pos > first and text[pos - 1] != "\n"
        start = pos if newline < 0 else newline + 1
        newline = text.find("\n", statement)
        line_end = len(text) if newline < 0 else newline
        next_line = line_end if newline < 0 else newline + 1
        parsed = _key(text, statement)
        if parsed is None:
            pos = next_line
            continue
        key, token = parsed
        separator = token
        while token < len(text) and text[token] in _SPACES:
            token += 1
        if token < len(text) and text[token] in "'\"":
            quoted = _quoted(text, token, text[token])
            if quoted is not None:
                value, close = quoted
                end = close + 1
                rest = _statement(text, end)
                newline = text.find("\n", end)
                if not after_quote and (rest is None or (0 <= newline < rest)):
                    # Nothing else on its last line but spaces or a comment: the line goes with it.
                    end = len(text) if newline < 0 else newline + 1
                if key:
                    yield _Definition(key, value, start, token, close + 1, text[token], end)
                pos = close + 1
                continue
        # extractVarValue, unquoted: Compose ends the value at " #" (a space, then #) and trims it.
        raw = text[token:line_end]
        cut = raw.find(" #")
        value = (raw if cut < 0 else raw[:cut]).rstrip(_UNICODE_SPACES)
        if not value:
            # Only spaces after the '=': a new value goes right after it.
            token = separator
        token_end = token + len(value)
        if key:
            yield _Definition(key, value, start, token, token_end, "", token_end if after_quote else next_line)
        pos = next_line


def _read_text(path: Path, shown: Path) -> str:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise EnvFileError(f"cannot read {shown}: {exc}") from exc
    return data.decode("utf-8", "surrogateescape")


def read_env(path: Path) -> Dict[str, str]:
    """Values as Compose reads them: the last definition of a key wins.

    Variables are not expanded, and quote/dollar-escape provenance is lost.
    Never use these logical values as proof of exact restoration. Bytes that
    are not UTF-8 come back as surrogate escapes (subprocess bytes stay intact).
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
        # read_env intentionally does not expand variables and is not a lexical
        # snapshot: '$X' might have come from "\\$X", '$X' or $X. Never guess
        # its interpolation semantics. Exact recovery uses the saved edits.
        return "$" not in value and not any(char in value for char in _NOT_VERBATIM)
    return value == "" or _VALUE_RE.fullmatch(value) is not None


def render_changes(text: str, changes: Mapping[str, Optional[str]], *, verbatim: bool = False) -> str:
    """Set (or, for None, remove) keys; everything else is kept verbatim.

    Only the value itself is replaced: the export prefix, the spaces around
    '=', the quotes, an inline comment and trailing whitespace all stay, so
    writing a key and then its old value again gives back the original text.
    Every definition of a key is changed; a key with none is appended.

    New values must be image tags. The legacy verbatim=True helper accepts
    only unambiguous, single-line values that read back unchanged. Dollar
    values are refused: read_env does not preserve interpolation semantics.
    Transaction recovery must use recovery_record/restore_changes instead.
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


def _render(text: str, changes: Mapping[str, Optional[str]],
            edits: Optional[List[Tuple[int, int, str]]] = None) -> str:
    out: List[str] = []
    cursor = 0
    seen = set()
    for definition in _definitions(text):
        if definition.key not in changes:
            continue
        seen.add(definition.key)
        value = changes[definition.key]
        if value is None:
            start, end, replacement = definition.start, definition.end, ""
        else:
            start, end = definition.token, definition.token_end
            replacement = f"{definition.quote}{value}{definition.quote}"
        out.extend((text[cursor:start], replacement))
        if edits is not None and text[start:end] != replacement:
            edits.append((start, end, replacement))
        cursor = end
    out.append(text[cursor:])
    rendered = "".join(out)
    additions = [(key, value) for key, value in changes.items() if key not in seen and value is not None]
    if not additions:
        return rendered
    suffix = ""
    if rendered.endswith("\r"):
        suffix = "\n"
    elif rendered not in ("", _BOM) and not rendered.endswith("\n"):
        suffix = _line_ending(rendered)
    ending = _line_ending(rendered + suffix)
    suffix += "".join(f"{key}={value}{ending}" for key, value in additions)
    if edits is not None:
        edits.append((len(text), len(text), suffix))
    return rendered + suffix


def _unterminated(definition: _Definition) -> bool:
    """Whether a value starts with a quote that is never closed (Compose rejects the file)."""

    return definition.quote == "" and definition.value[:1] in ("'", '"')


def _check_reads_back(before: str, after: str, changes: Mapping[str, Optional[str]]) -> None:
    """Refuse a verbatim write unless every definition reads back as intended.

    A value goes inside the quotes its line already has (or bare), so one
    holding that quote, a backslash escape, a leading space or ' #' would
    read back as something else, or change how the lines after it read. One
    that opens a quote it never closes reads back as itself here, but Compose
    rejects the file.
    """

    expected: List[Tuple[str, str, bool]] = []
    seen = set()
    for definition in _definitions(before):
        if definition.key not in changes:
            expected.append((definition.key, definition.value, _unterminated(definition)))
            continue
        seen.add(definition.key)
        value = changes[definition.key]
        if value is not None:
            expected.append((definition.key, value, False))
    expected.extend((key, value, False) for key, value in changes.items() if key not in seen and value is not None)
    actual = [(definition.key, definition.value, _unterminated(definition)) for definition in _definitions(after)]
    if actual == expected:
        return
    keys = [key for key, value in changes.items() if value is not None]
    wrong = [key for key in keys
             if [item for item in actual if item[0] == key] != [item for item in expected if item[0] == key]]
    key = (wrong or keys or list(changes))[0]
    raise EnvFileError(
        f"refusing to write back {key}: {changes[key]!r} would not read back as the same value where {key} is "
        f"defined. Set {key} in the env file by hand"
    )


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "surrogateescape")).hexdigest()


def recovery_record(path: Path, changes: Mapping[str, Optional[str]], *,
                    expected_before_sha256: Optional[str] = None) -> dict:
    """A minimal inverse of this write, to fsync in the private journal first.

    Only replaced tokens, removed definitions and appended bytes are saved,
    never unchanged portions of the env file. JSON's surrogate escapes keep
    even non-UTF-8 bytes exact. Offsets address the *written* text. Hashes of
    both complete files make recovery idempotent and reject concurrent edits.
    This record belongs only in the journal, never a public receipt.

    expected_before_sha256 (automatic runs): the sha256 of the exact env bytes
    an automation guard approved. Any other bytes, such as an operator edit
    that landed after the approval, are refused (EnvPreimageError) instead of
    being adopted as this transaction's baseline. Manual runs pass nothing.
    """

    target = Path(os.path.realpath(path))
    text = _read_text(target, path)
    if expected_before_sha256 is not None and _hash(text) != expected_before_sha256:
        raise EnvPreimageError(f"cannot prepare a write to {path}: the env file is not the one that was approved; "
                               "no bytes were written")
    rendered = render_changes(text, changes)
    edits: List[Tuple[int, int, str]] = []
    _render(text, changes, edits)
    inverse = []
    shift = 0
    for start, end, replacement in edits:
        inverse.append({"offset": start + shift, "before": text[start:end], "after": replacement})
        shift += len(replacement) - (end - start)
    try:
        mode = target.stat().st_mode & 0o7777
    except OSError as exc:
        raise EnvFileError(f"cannot read {path}: {exc}") from exc
    return {"format": 1, "target": str(target), "mode": mode, "commit": envcommit.reference(),
            "before_sha256": _hash(text), "after_sha256": _hash(rendered), "edits": inverse}


def _check_recovery(path: Path, recovery: Mapping) -> None:
    if (not isinstance(recovery, Mapping) or type(recovery.get("format")) is not int or recovery["format"] != 1
            or not isinstance(recovery.get("edits"), list)
            or type(recovery.get("mode")) is not int or not 0 <= recovery["mode"] <= 0o7777
            or any(not isinstance(recovery.get(key), str) or not re.fullmatch(r"[0-9a-f]{64}", recovery[key])
                   for key in ("before_sha256", "after_sha256"))):
        raise EnvFileError(f"cannot safely restore {path}: missing or invalid exact env recovery data")
    end = 0
    for edit in recovery["edits"]:
        if (not isinstance(edit, Mapping) or type(edit.get("offset")) is not int or edit["offset"] < end
                or not isinstance(edit.get("before"), str) or not isinstance(edit.get("after"), str)):
            raise EnvFileError(f"cannot safely restore {path}: invalid recovery edit")
        end = edit["offset"] + len(edit["after"])
    if recovery.get("target") != os.path.realpath(path):
        raise EnvFileError(f"cannot safely restore {path}: its symlink target changed since the journal was written")


def recovery_text(path: Path, recovery: Mapping) -> str:
    """The exact pre-write text, or an error without writing anything (also used by dry runs)."""

    _check_recovery(path, recovery)
    text = _read_text(path, path)
    digest = _hash(text)
    if digest == recovery["before_sha256"]:
        # Also validate a record whose write never happened (or whose restore
        # already happened). Malformed edits must not disappear with its journal.
        forward, cursor, shift = [], 0, 0
        for edit in recovery["edits"]:
            offset = edit["offset"] - shift
            before, after = edit["before"], edit["after"]
            if not cursor <= offset <= len(text) or text[offset:offset + len(before)] != before:
                raise EnvFileError(f"cannot safely restore {path}: invalid recovery edit")
            forward.extend((text[cursor:offset], after))
            cursor = offset + len(before)
            shift += len(after) - len(before)
        forward.append(text[cursor:])
        if _hash("".join(forward)) != recovery["after_sha256"]:
            raise EnvFileError(f"cannot safely restore {path}: recovery edits do not match the written file hash")
        return text  # not written yet, or a restore completed before the process died
    if digest != recovery["after_sha256"]:
        raise EnvFileError(f"cannot safely restore {path}: the env file changed since the interrupted run; "
                           "no bytes were overwritten")
    out = []
    cursor = 0
    for edit in recovery["edits"]:
        if not isinstance(edit, Mapping):
            raise EnvFileError(f"cannot safely restore {path}: invalid recovery edit")
        offset, before, after = edit.get("offset"), edit.get("before"), edit.get("after")
        if (type(offset) is not int or not isinstance(before, str) or not isinstance(after, str)
                or not cursor <= offset <= len(text) or text[offset:offset + len(after)] != after):
            raise EnvFileError(f"cannot safely restore {path}: invalid recovery edit")
        out.extend((text[cursor:offset], before))
        cursor = offset + len(after)
    out.append(text[cursor:])
    original = "".join(out)
    if _hash(original) != recovery["before_sha256"]:
        raise EnvFileError(f"cannot safely restore {path}: recovery edits do not match the original file hash")
    return original


def check_commit(path: Path, recovery: Mapping, *, durable: bool = False) -> str:
    """Gate recovery/receipt cleanup on the latest direction, never version tags alone."""
    _check_recovery(path, recovery)
    ref = recovery.get("commit")
    if ref is None:  # Legacy lexical journal, upgraded durably by Updater before a write.
        return "legacy"
    try:
        envcommit.validate_reference(ref)
        target = Path(recovery["target"])
        restored = envcommit.inspect(path, target, ref["restore"], durable=durable)
        if restored != "absent":
            return "restored" if restored == "accepted" else "restoring"
        outcome = envcommit.inspect(path, target, ref["forward"], durable=durable)
        if (outcome == "absent" and not recovery.get("commit_legacy")
                and recovery["before_sha256"] != recovery["after_sha256"]
                and _hash(_read_text(path, path)) != recovery["before_sha256"]):
            raise envcommit.CommitError("missing forward env operation evidence; keep the journal and retained files")
        return outcome
    except envcommit.CommitError as exc:
        raise EnvCommitError(f"cannot write {path}: {exc}") from exc


def restore_changes(path: Path, recovery: Mapping) -> None:
    """Reverse a journaled write exactly, including duplicate definitions and escaping."""

    outcome = check_commit(path, recovery, durable=True)
    target = Path(os.path.realpath(path))
    current = _read_text(target, path)
    original = recovery_text(path, recovery)
    # recovery_text read the file again: refuse a change between those reads too.
    if _hash(current) not in (recovery["before_sha256"], recovery["after_sha256"]):
        raise EnvFileError(f"cannot safely restore {path}: the env file changed during recovery")
    _write_text(path, target, current, original, mode=recovery["mode"], durable=True,
                op_id=(recovery.get("commit") or envcommit.reference())["restore"])
    if outcome == "untouched" and current == original:
        try:
            envcommit.inspect(path, target, recovery["commit"]["forward"], durable=True, cancel=True)
        except envcommit.CommitError as exc:
            raise EnvCommitError(str(exc)) from exc


def write_changes(path: Path, changes: Mapping[str, Optional[str]], *, verbatim: bool = False,
                  recovery: Optional[Mapping] = None) -> None:
    """Exchange changes at a symlink's target, retaining the displaced entry.

    A journaled recovery record identifies BOTH forward and inverse operations
    before either mutates the target. Full env files are retained privately;
    a conflict never permits automatic cleanup or a destructive swap-back.
    """

    target = Path(os.path.realpath(path))
    original = _read_text(target, path)
    rendered = render_changes(original, changes, verbatim=verbatim)
    if recovery is not None:
        _check_recovery(path, recovery)
        if _hash(original) != recovery["before_sha256"] or _hash(rendered) != recovery["after_sha256"]:
            raise EnvFileError(f"cannot write {path}: the env file changed after its recovery record was prepared")
    _write_text(path, target, original, rendered, mode=recovery["mode"] if recovery is not None else None,
                op_id=((recovery or {}).get("commit") or envcommit.reference())["forward"])


def _write_text(path: Path, target: Path, original: str, rendered: str, *,
                mode: Optional[int] = None, durable: bool = False, op_id: Optional[str] = None) -> None:
    try:
        info = target.stat()
        if mode is not None and info.st_mode & 0o7777 != mode:
            raise EnvFileError(f"cannot write {path}: its file mode changed since the journal was written")
        if rendered == original:
            if os.path.realpath(path) != str(target) or _read_text(target, path) != original:
                raise EnvFileError(f"cannot write {path}: the env file changed during the write")
            if durable:
                with target.open("rb") as handle:
                    envcommit.sync_file(handle.fileno())
                _fsync_dir(target.parent)
            return
        envcommit.commit(path, target, original.encode("utf-8", "surrogateescape"),
                         rendered.encode("utf-8", "surrogateescape"), mode,
                         op_id or envcommit.reference()["forward"])
    except envcommit.CommitError as exc:
        raise EnvCommitError(f"cannot write {path}: {exc}") from exc
    except (OSError, UnicodeError) as exc:
        raise EnvFileError(f"cannot write {path}: filesystem operation failed") from exc


def _fsync_dir(directory: Path) -> None:
    fd = os.open(str(directory), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
