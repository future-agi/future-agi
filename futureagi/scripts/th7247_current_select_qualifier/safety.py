"""Pure, offline-testable safety primitives for the TH-7247 qualifier.

This module deliberately imports only the Python standard library.  The live
qualifier installs these checks before Django opens a database connection.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path, PurePosixPath
from typing import Any

BASE_COMMIT = "041084a8bfea8e5e7f66b87d7f6883c57659729b"
SCHEMA = "th7247-current-select-only/v2"
PG_TIMEOUT_MS = 9_500
CH_TIMEOUT_SECONDS = 9.5
CH_MAX_THREADS = 4
CH_MAX_MEMORY_BYTES = 4 * 1024**3
CH_MAX_RESULT_ROWS = 250_000
CH_MAX_RESULT_BYTES = 64 * 1024**2
CH_MAX_BYTES_TO_READ = 16 * 1024**3


class SafetyViolation(RuntimeError):
    """A fail-closed invariant was violated."""


_PG_FORBIDDEN_VERBS = frozenset(
    {
        "ALTER",
        "ANALYZE",
        "CALL",
        "CLUSTER",
        "COMMENT",
        "COPY",
        "CREATE",
        "DELETE",
        "DISCARD",
        "DO",
        "DROP",
        "EXECUTE",
        "GRANT",
        "INSERT",
        "LISTEN",
        "LOAD",
        "LOCK",
        "MERGE",
        "NOTIFY",
        "PREPARE",
        "REASSIGN",
        "REFRESH",
        "REINDEX",
        "RESET",
        "REVOKE",
        "SECURITY",
        "SETVAL",
        "TRUNCATE",
        "UNLISTEN",
        "UPDATE",
        "VACUUM",
    }
)
_PG_FORBIDDEN_FUNCTIONS = frozenset(
    {
        "LO_EXPORT",
        "LO_IMPORT",
        "NEXTVAL",
        "PG_ADVISORY_LOCK",
        "PG_ADVISORY_LOCK_SHARED",
        "PG_NOTIFY",
        "PG_RELOAD_CONF",
        "PG_ROTATE_LOGFILE",
        "PG_SWITCH_WAL",
        "PG_TERMINATE_BACKEND",
        "SETVAL",
    }
)
_CH_FORBIDDEN_TOP_LEVEL = frozenset(
    {
        "ALTER",
        "ATTACH",
        "BACKUP",
        "CHECK",
        "CREATE",
        "DELETE",
        "DETACH",
        "DROP",
        "EXCHANGE",
        "GRANT",
        "INSERT",
        "KILL",
        "MOVE",
        "OPTIMIZE",
        "RENAME",
        "REPLACE",
        "RESTORE",
        "REVOKE",
        "SET",
        "SYSTEM",
        "TRUNCATE",
        "UPDATE",
        "USE",
        "WATCH",
    }
)
_CH_WITH_MUTATION = frozenset(
    {
        "ALTER",
        "ATTACH",
        "CREATE",
        "DELETE",
        "DETACH",
        "DROP",
        "INSERT",
        "OPTIMIZE",
        "RENAME",
        "REPLACE",
        "TRUNCATE",
        "UPDATE",
    }
)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        + b"\n"
    )


def safe_relative_path(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if (
        not value
        or path.is_absolute()
        or ".." in path.parts
        or "." in path.parts
        or path.as_posix() != value
    ):
        raise SafetyViolation("manifest contains an unsafe relative path")
    return path


def verify_regular_file(path: Path, expected_sha256: str) -> None:
    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
        raise SafetyViolation("artifact hash is not a lowercase SHA-256")
    if not path.is_file() or path.is_symlink():
        raise SafetyViolation(f"required regular file is absent: {path.name}")
    if sha256_bytes(path.read_bytes()) != expected_sha256:
        raise SafetyViolation(f"artifact hash mismatch: {path.name}")


def _sql_tokens(sql: str) -> list[str]:
    """Tokenize SQL after removing comments and quoted literals.

    The tokenizer is intentionally small and fail-closed.  It is sufficient
    for deciding whether a statement may cross a SELECT-only qualification
    boundary; it is not intended to parse general SQL.
    """

    text = str(sql)
    cleaned: list[str] = []
    index = 0
    state = "code"
    dollar_tag = ""
    while index < len(text):
        char = text[index]
        pair = text[index : index + 2]
        if state == "code":
            if pair == "--":
                state = "line_comment"
                cleaned.append(" ")
                index += 2
                continue
            if pair == "/*":
                state = "block_comment"
                cleaned.append(" ")
                index += 2
                continue
            if char == "'":
                state = "single_quote"
                cleaned.append(" ")
                index += 1
                continue
            if char == '"':
                state = "double_quote"
                cleaned.append(" ")
                index += 1
                continue
            if char == "`":
                state = "backtick_quote"
                cleaned.append(" ")
                index += 1
                continue
            if char == "$":
                match = re.match(r"\$[A-Za-z_][A-Za-z0-9_]*\$|\$\$", text[index:])
                if match:
                    dollar_tag = match.group(0)
                    state = "dollar_quote"
                    cleaned.append(" ")
                    index += len(dollar_tag)
                    continue
            cleaned.append(char)
            index += 1
            continue
        if state == "line_comment":
            if char in "\r\n":
                state = "code"
                cleaned.append(char)
            index += 1
            continue
        if state == "block_comment":
            if pair == "*/":
                state = "code"
                index += 2
            else:
                index += 1
            continue
        if state == "single_quote":
            if pair == "''":
                index += 2
            elif char == "'":
                state = "code"
                index += 1
            else:
                index += 1
            continue
        if state == "double_quote":
            if pair == '""':
                index += 2
            elif char == '"':
                state = "code"
                index += 1
            else:
                index += 1
            continue
        if state == "backtick_quote":
            if char == "`":
                state = "code"
            index += 1
            continue
        if state == "dollar_quote":
            if text.startswith(dollar_tag, index):
                state = "code"
                index += len(dollar_tag)
            else:
                index += 1
    if state not in {"code", "line_comment"}:
        raise SafetyViolation("SQL contains an unterminated quoted construct")
    return re.findall(r"[A-Za-z_][A-Za-z0-9_]*|;|\(|\)", "".join(cleaned).upper())


def _timeout_ms(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = re.fullmatch(
        r"\s*([0-9]+(?:\.[0-9]+)?)\s*(ms|s)?\s*",
        str(value),
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    amount = float(match.group(1))
    return amount * 1_000 if (match.group(2) or "ms").lower() == "s" else amount


def _safe_statement_timeout_control(normalized: str, params: Any) -> bool:
    parameterized = bool(
        re.fullmatch(
            r"(?:SELECT\s+SET_CONFIG\(\s*'STATEMENT_TIMEOUT'\s*,\s*%S\s*,\s*TRUE\s*\)"
            r"|SET\s+LOCAL\s+STATEMENT_TIMEOUT\s*=\s*%S)",
            normalized,
        )
    )
    if parameterized:
        if not isinstance(params, (list, tuple)) or len(params) != 1:
            return False
        timeout_ms = _timeout_ms(params[0])
        return timeout_ms is not None and 0 < timeout_ms <= PG_TIMEOUT_MS
    literal = re.fullmatch(
        r"SET\s+LOCAL\s+STATEMENT_TIMEOUT\s*=\s*'?"
        r"([0-9]+(?:\.[0-9]+)?(?:MS|S)?)'?",
        normalized,
    )
    if not literal:
        return False
    timeout_ms = _timeout_ms(literal.group(1))
    return timeout_ms is not None and 0 < timeout_ms <= PG_TIMEOUT_MS


def assert_pg_read(sql: str, params: Any = None) -> None:
    tokens = _sql_tokens(sql)
    normalized = " ".join(str(sql).split()).upper()
    safe_savepoint_control = any(
        re.fullmatch(pattern, normalized)
        for pattern in (
            r'SAVEPOINT "[A-Z0-9_]+"',
            r'RELEASE SAVEPOINT "[A-Z0-9_]+"',
            r'ROLLBACK TO SAVEPOINT "[A-Z0-9_]+"',
        )
    )
    safe_statement_timeout = _safe_statement_timeout_control(normalized, params)
    allowed_control = (
        safe_savepoint_control
        or safe_statement_timeout
        or (
            normalized == "SET TRANSACTION READ ONLY"
            or normalized == "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"
            or normalized
            == "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
        )
    )
    words = {token for token in tokens if token and token[0].isalpha()}
    forbidden = words & (_PG_FORBIDDEN_VERBS | _PG_FORBIDDEN_FUNCTIONS)
    set_config_calls = sum(token == "SET_CONFIG" for token in tokens)
    locking_select = any(
        tokens[index : index + len(sequence)] == list(sequence)
        for sequence in (
            ("FOR", "UPDATE"),
            ("FOR", "SHARE"),
            ("FOR", "NO", "KEY", "UPDATE"),
            ("FOR", "KEY", "SHARE"),
        )
        for index in range(len(tokens))
    )
    if (
        ";" in tokens
        or forbidden
        or (set_config_calls and (set_config_calls != 1 or not safe_statement_timeout))
        or locking_select
        or (not allowed_control and (not tokens or tokens[0] not in {"SELECT", "WITH"}))
    ):
        raise SafetyViolation("PostgreSQL non-read statement blocked")


def assert_ch_read(sql: str) -> None:
    tokens = _sql_tokens(sql)
    if ";" in tokens or not tokens or tokens[0] not in {"SELECT", "WITH"}:
        raise SafetyViolation("ClickHouse non-read statement blocked")
    # A SELECT may legitimately read ``system.settings`` or project a column
    # named ``update``.  The server-side ``readonly=2`` guard handles table
    # functions, while this lexical boundary rejects the ClickHouse grammar's
    # write form where WITH is followed by a mutation statement.
    words = {token for token in tokens if token and token[0].isalpha()}
    if tokens[0] == "WITH" and words & _CH_WITH_MUTATION:
        raise SafetyViolation("ClickHouse mutation statement blocked")


def bounded_ch_settings(value: Any) -> dict[str, Any]:
    settings = dict(value or {})
    settings.pop("max_rows_to_read", None)
    settings.update(
        {
            "readonly": 2,
            "max_execution_time": min(
                float(settings.get("max_execution_time") or CH_TIMEOUT_SECONDS),
                CH_TIMEOUT_SECONDS,
            ),
            "max_threads": min(
                max(int(settings.get("max_threads") or CH_MAX_THREADS), 1),
                CH_MAX_THREADS,
            ),
            "max_memory_usage": min(
                int(settings.get("max_memory_usage") or CH_MAX_MEMORY_BYTES),
                CH_MAX_MEMORY_BYTES,
            ),
            "max_result_rows": min(
                max(int(settings.get("max_result_rows") or CH_MAX_RESULT_ROWS), 1),
                CH_MAX_RESULT_ROWS,
            ),
            "max_result_bytes": min(
                max(
                    int(settings.get("max_result_bytes") or CH_MAX_RESULT_BYTES),
                    1,
                ),
                CH_MAX_RESULT_BYTES,
            ),
            "max_bytes_to_read": min(
                max(
                    int(settings.get("max_bytes_to_read") or CH_MAX_BYTES_TO_READ),
                    1,
                ),
                CH_MAX_BYTES_TO_READ,
            ),
            "timeout_overflow_mode": "throw",
            "result_overflow_mode": "throw",
        }
    )
    return settings


def static_guard_self_test() -> None:
    assert_pg_read("SELECT 1")
    assert_pg_read("WITH source AS (SELECT 1) SELECT * FROM source")
    assert_pg_read(
        "SELECT set_config('statement_timeout', %s, true)",
        ["9000ms"],
    )
    assert_pg_read('SAVEPOINT "s123_x1"')
    assert_pg_read('RELEASE SAVEPOINT "s123_x1"')
    assert_pg_read('ROLLBACK TO SAVEPOINT "s123_x1"')
    assert_ch_read("SELECT 1")
    assert_ch_read("SELECT name, value FROM system.settings")
    assert_ch_read("WITH source AS (SELECT 1) SELECT * FROM source")
    rejected = (
        (assert_pg_read, "UPDATE target SET value=1"),
        (
            assert_pg_read,
            "WITH changed AS (UPDATE target SET value=1 RETURNING value) "
            "SELECT value FROM changed",
        ),
        (assert_pg_read, "SELECT nextval('unsafe_sequence')"),
        (
            assert_pg_read,
            "SELECT set_config('default_transaction_read_only', 'off', false)",
        ),
        (assert_pg_read, "SELECT * FROM target FOR UPDATE"),
        (assert_pg_read, "SELECT 1; DELETE FROM target"),
        (assert_ch_read, "INSERT INTO target VALUES (1)"),
        (assert_ch_read, "WITH source AS (SELECT 1) INSERT INTO target SELECT 1"),
        (assert_ch_read, "SYSTEM FLUSH LOGS"),
        (assert_ch_read, "SELECT 1; SELECT 2"),
    )
    for guard, statement in rejected:
        try:
            guard(statement)
        except SafetyViolation:
            continue
        raise AssertionError(f"guard accepted unsafe SQL: {statement}")


__all__ = [
    "BASE_COMMIT",
    "CH_TIMEOUT_SECONDS",
    "PG_TIMEOUT_MS",
    "SCHEMA",
    "SafetyViolation",
    "assert_ch_read",
    "assert_pg_read",
    "bounded_ch_settings",
    "canonical_json_bytes",
    "safe_relative_path",
    "sha256_bytes",
    "static_guard_self_test",
    "verify_regular_file",
]
