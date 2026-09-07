"""Pure qualification of source-local, physical MergeTree capture DDL.

Only the object header, engine, table TTL, two merge settings and table comment
change. All columns, codecs, indices, projections, keys and storage settings are
copied from system.tables.create_table_query, not an environment schema tuple.

This is NOT proof of a capture's contents/ownership. Before exposing attached
parts the backend must verify every required_stored_columns entry in EVERY part
(including MATERIALIZED/DEFAULT columns). Missing columns can otherwise evaluate
mutable defaults/dictionaries on read. The backend separately binds source UUID,
source node, parts and target ownership, and forbids OPTIMIZE/mutations/writes.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Collection
from dataclasses import dataclass
from uuid import UUID

from sqlparse import lexer
from sqlparse import tokens as T

SOURCE_CAPTURE_COMMENT = "futureagi.property-catalog.source-capture.v1"
MAX_SOURCE_CREATE_BYTES = 256 * 1024
_MAX_TOKENS = 32768
_MAX_DEPTH = 128
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,126}\Z", re.ASCII)
# The read projection, not a reconstructed source schema. Unread columns keep
# their exact metadata but do not become new physical-presence obligations.
CATALOG_CAPTURE_INPUTS = (
    "project_id",
    "observation_type",
    "service_name",
    "trace_id",
    "id",
    "start_time",
    "_version",
    "is_deleted",
    "attrs_string",
    "attrs_number",
    "attrs_bool",
    "attributes_extra",
    "model",
)
_MERGE_SETTINGS = (
    "max_bytes_to_merge_at_max_space_in_pool",
    "min_age_to_force_merge_seconds",
)


class SourceCaptureSchemaError(ValueError):
    """Source metadata cannot safely qualify a physical capture."""


@dataclass(frozen=True, slots=True)
class SourceCaptureCreateSpec:
    create_sql: str
    source_sha256: str
    capture_sha256: str
    required_stored_columns: tuple[str, ...]
    target_database: str
    target_table: str
    target_uuid: str
    source_database: str
    source_table: str
    source_uuid: str | None

    def verify_source(self, observed_create: str) -> None:
        """Verify source header/hash; actual source UUID remains backend-bound."""
        tokens = _lex(observed_create)
        database, table, uuid, _ = _header(tokens)
        if ((database or self.source_database), table) != (
            self.source_database,
            self.source_table,
        ):
            raise SourceCaptureSchemaError(
                "source CREATE object differs from bound source"
            )
        if (
            uuid is not None
            and self.source_uuid is not None
            and uuid != self.source_uuid
        ):
            raise SourceCaptureSchemaError(
                "source CREATE UUID differs from bound source"
            )
        if normalized_schema_sha256(observed_create) != self.source_sha256:
            raise SourceCaptureSchemaError(
                "source CREATE schema differs from frozen metadata"
            )

    def verify_capture(self, observed_create: str) -> None:
        """Check target metadata, not its physical UUID/parts/ownership proof.

        system.tables.create_table_query may omit UUID. The backend MUST also
        compare system.tables.uuid to target_uuid in its exact table observation.
        """
        tokens = _lex(observed_create)
        database, table, uuid, _ = _header(tokens)
        if ((database or self.target_database), table) != (
            self.target_database,
            self.target_table,
        ):
            raise SourceCaptureSchemaError(
                "capture CREATE object differs from bound target"
            )
        if uuid is not None and uuid != self.target_uuid:
            raise SourceCaptureSchemaError(
                "capture CREATE UUID differs from bound target"
            )
        if normalized_schema_sha256(observed_create) != self.capture_sha256:
            raise SourceCaptureSchemaError(
                "capture CREATE schema differs from frozen metadata"
            )

    def require_stored_columns(self, columns: Collection[str]) -> None:
        """Check one part's complete physical column inventory, not system.columns.

        The backend must supply a bounded complete inventory for every active
        captured part. No source inventory or empty/prefix response proves this.
        """
        if isinstance(columns, (str, bytes)) or not isinstance(columns, Collection):
            raise SourceCaptureSchemaError("physical columns must be a collection")
        if any(not isinstance(value, str) for value in columns):
            raise SourceCaptureSchemaError("physical column names must be strings")
        missing = sorted(set(self.required_stored_columns) - set(columns))
        if missing:
            raise SourceCaptureSchemaError(
                "capture part lacks physically stored columns: "
                + ", ".join(missing)
                + "; DEFAULT/MATERIALIZED evaluation is not frozen evidence"
            )


@dataclass(frozen=True, slots=True)
class _Token:
    text: str
    start: int
    end: int

    @property
    def word(self) -> str:
        return self.text.upper() if _IDENTIFIER.fullmatch(self.text) else ""


def _block_comment_end(sql: str, start: int) -> int:
    depth, position = 1, start + 2
    while depth:
        opening = sql.find("/*", position)
        closing = sql.find("*/", position)
        if closing < 0:
            raise SourceCaptureSchemaError("unterminated SQL block comment")
        if 0 <= opening < closing:
            depth += 1
            if depth > _MAX_DEPTH:
                raise SourceCaptureSchemaError("SQL comment nesting exceeds bound")
            position = opening + 2
        else:
            depth -= 1
            position = closing + 2
    return position


def _lex(sql: str, *, _depth: int = 0) -> tuple[_Token, ...]:
    if _depth > _MAX_DEPTH:
        raise SourceCaptureSchemaError("SQL nesting exceeds bound")
    if not isinstance(sql, str) or not sql or "\x00" in sql:
        raise SourceCaptureSchemaError("source CREATE must be nonempty SQL text")
    if len(sql.encode("utf-8")) > MAX_SOURCE_CREATE_BYTES:
        raise SourceCaptureSchemaError("source CREATE exceeds byte bound")
    result, offset = [], 0
    stream = iter(lexer.tokenize(sql))
    while True:
        item = next(stream, None)
        if item is None:
            break
        kind, value = item
        start, offset = offset, offset + len(value)
        if sql.startswith("/*", start):
            # sqlparse 0.5.x stops at the first */; ClickHouse permits nesting.
            end = _block_comment_end(sql, start)
            if end != offset:
                offset, stream = end, iter(lexer.tokenize(sql[end:]))
            continue
        if kind in T.Comment or kind in T.Whitespace:
            continue
        if kind in T.Error:
            raise SourceCaptureSchemaError("invalid or unterminated SQL token")
        if kind in T.Keyword:
            # Lexer groups ORDER BY / PRIMARY KEY / IF NOT EXISTS, etc.
            result.extend(
                _Token(m.group(), start + m.start(), start + m.end())
                for m in re.finditer(r"\S+", value)
            )
        elif value.startswith("[") and value.endswith("]") and len(value) >= 2:
            # sqlparse's SQL Server [identifier] rule also matches CH arrays.
            # Re-lex the interior so commas/keywords cannot hide from nesting.
            result.append(_Token("[", start, start + 1))
            if value[1:-1].strip():
                result.extend(
                    _Token(t.text, start + 1 + t.start, start + 1 + t.end)
                    for t in _lex(value[1:-1], _depth=_depth + 1)
                )
            result.append(_Token("]", offset - 1, offset))
        else:
            result.append(_Token(value, start, offset))
        if len(result) > _MAX_TOKENS:
            raise SourceCaptureSchemaError("source CREATE exceeds token bound")
    if not _depth and result and result[-1].text == ";":
        result.pop()
    if not result or any(t.text == ";" for t in result):
        raise SourceCaptureSchemaError("source CREATE must be one statement")
    return tuple(result)


def _nesting(tokens: tuple[_Token, ...]) -> tuple[int, ...]:
    stack, depths = [], []
    for token in tokens:
        depths.append(len(stack))
        if token.text in ("(", "["):
            stack.append(token.text)
            if len(stack) > _MAX_DEPTH:
                raise SourceCaptureSchemaError("SQL nesting exceeds bound")
        elif token.text in (")", "]"):
            if not stack or stack.pop() != {"\u0029": "(", "]": "["}[token.text]:
                raise SourceCaptureSchemaError("unbalanced SQL parentheses/brackets")
        elif token.text in ("{", "}"):
            raise SourceCaptureSchemaError("query parameters are not source metadata")
    if stack:
        raise SourceCaptureSchemaError("unbalanced SQL parentheses/brackets")
    return tuple(depths)


def _identifier(token: _Token) -> str:
    text = token.text
    if text[:1] in ("`", '"') and text[-1:] == text[0]:
        quote = text[0]
        text = text[1:-1].replace(quote * 2, quote)
        text = text.replace("\\" + quote, quote).replace("\\\\", "\\")
    if not text or "\x00" in text or token.text.startswith("'"):
        raise SourceCaptureSchemaError("expected SQL identifier")
    if token.text[0] not in ("`", '"') and not _IDENTIFIER.fullmatch(text):
        raise SourceCaptureSchemaError("expected SQL identifier")
    return text


def _split(
    tokens: tuple[_Token, ...],
    depths: tuple[int, ...],
    start: int,
    end: int,
    level: int,
):
    first = start
    for i in range(start, end):
        if depths[i] == level and tokens[i].text == ",":
            if first == i:
                raise SourceCaptureSchemaError("empty metadata list item")
            yield first, i
            first = i + 1
    if first == end:
        raise SourceCaptureSchemaError("empty metadata list item")
    yield first, end


def _columns(tokens, depths, start, end, required_columns) -> tuple[str, ...]:
    stored, aliases, names = [], {}, set()
    for left, right in _split(tokens, depths, start, end, 1):
        if tokens[left].word in {"INDEX", "PROJECTION", "CONSTRAINT", "PRIMARY"}:
            continue
        name = _identifier(tokens[left])
        if name in names or right - left < 2:
            raise SourceCaptureSchemaError("duplicate or incomplete column: " + name)
        names.add(name)
        modifiers = {
            tokens[i].word: i
            for i in range(left + 1, right)
            if depths[i] == 1
            and tokens[i].word
            in {"TTL", "DEFAULT", "MATERIALIZED", "ALIAS", "EPHEMERAL"}
        }
        if "TTL" in modifiers:
            raise SourceCaptureSchemaError("unsupported column TTL: " + name)
        if "EPHEMERAL" in modifiers:
            raise SourceCaptureSchemaError(
                "unsupported unstored EPHEMERAL column: " + name
            )
        if "ALIAS" in modifiers:
            i = modifiers["ALIAS"] + 1
            last = next(
                (
                    j
                    for j in range(i, right)
                    if depths[j] == 1 and tokens[j].word in {"COMMENT", "CODEC"}
                ),
                right,
            )
            aliases[name] = tokens[i:last]
        else:
            stored.append(name)
    if not stored:
        raise SourceCaptureSchemaError("source CREATE contains no stored columns")
    required = set()
    for name in required_columns:
        visited, target = set(), name
        while target in aliases and target not in visited:
            visited.add(target)
            expression = aliases[target]
            if len(expression) != 1:
                raise SourceCaptureSchemaError(
                    "unsupported unstored ALIAS expression for "
                    + target
                    + "; only direct column references are capture-safe"
                )
            target = _identifier(expression[0])
        if target not in stored:
            raise SourceCaptureSchemaError(
                "required input is not bound to a stored column: " + name
            )
        required.add(target)
    return tuple(sorted(required))


def _identifier_argument(value: str, label: str) -> None:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise SourceCaptureSchemaError(label + " must be an exact identifier")


def normalized_schema_sha256(create_sql: str) -> str:
    """Exact token schema hash excluding the separately bound object header.

    IF NOT EXISTS / header UUID, whitespace, comments, identifier quoting and
    empty MergeTree engine parentheses are normalized. Literal bytes, expression
    structure and settings order are not weakened. Other server rewrites fail
    closed. This hash alone never binds database/table/UUID ownership.
    """
    tokens = _lex(create_sql)
    depths = _nesting(tokens)
    _, _, _, opening = _header(tokens)
    normalized = []
    skip = set()
    for i, token in enumerate(tokens[opening:], opening):
        if i in skip:
            continue
        value = token.text
        if (
            depths[i] == 0
            and value == "MergeTree"
            and i >= 2
            and tokens[i - 2].word == "ENGINE"
            and tokens[i - 1].text == "="
            and [t.text for t in tokens[i + 1 : i + 3]] == ["(", ")"]
        ):
            skip.update((i + 1, i + 2))
        if value.startswith(("`", '"')):
            name = _identifier(token)
            value = name if _IDENTIFIER.fullmatch(name) else value
        normalized.append(value)
    # Length framing prevents collisions between distinct token boundaries.
    body = "".join(f"{len(value.encode('utf-8'))}:{value}" for value in normalized)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _header(tokens):
    if [t.word for t in tokens[:2]] != ["CREATE", "TABLE"]:
        raise SourceCaptureSchemaError("expected exact CREATE TABLE metadata")
    i = 2
    if [t.word for t in tokens[i : i + 3]] == ["IF", "NOT", "EXISTS"]:
        i += 3
    if i >= len(tokens):
        raise SourceCaptureSchemaError("source CREATE lacks object name")
    database, table, uuid = None, _identifier(tokens[i]), None
    i += 1
    if i < len(tokens) and tokens[i].text == ".":
        if i + 1 >= len(tokens):
            raise SourceCaptureSchemaError("source CREATE lacks table name")
        database, table = table, _identifier(tokens[i + 1])
        i += 2
    if i < len(tokens) and tokens[i].word == "UUID":
        if i + 1 >= len(tokens):
            raise SourceCaptureSchemaError("CREATE UUID is missing")
        value = tokens[i + 1].text
        try:
            parsed = UUID(value[1:-1])
        except ValueError as exc:
            raise SourceCaptureSchemaError("CREATE UUID is malformed") from exc
        if value != "'" + str(parsed) + "'" or parsed.int == 0:
            raise SourceCaptureSchemaError("CREATE UUID is not canonical")
        uuid = str(parsed)
        i += 2
    if i >= len(tokens) or tokens[i].text != "(":
        raise SourceCaptureSchemaError(
            "CREATE requires explicit columns; no AS/CLUSTER"
        )
    return database, table, uuid, i


def qualified_capture_schema(
    source_create: str,
    *,
    source_database: str,
    source_table: str,
    target_database: str,
    target_table: str,
    target_uuid: str,
    required_columns: tuple[str, ...] = CATALOG_CAPTURE_INPUTS,
) -> SourceCaptureCreateSpec:
    """Copy exact canonical metadata with only the capture-owned substitutions.

    Returned SQL creates an EMPTY table, not AS SELECT / AS source. This helper
    performs no IO and supplies no ATTACH, retry or authorization operation.
    """
    for label, value in (
        ("source_database", source_database),
        ("source_table", source_table),
        ("target_database", target_database),
        ("target_table", target_table),
    ):
        _identifier_argument(value, label)
    if source_database == target_database:
        raise SourceCaptureSchemaError("capture and source databases must differ")
    if (
        not isinstance(required_columns, tuple)
        or not required_columns
        or len(required_columns) > 256
        or any(not isinstance(name, str) or not name for name in required_columns)
        or len(set(required_columns)) != len(required_columns)
    ):
        raise SourceCaptureSchemaError("required_columns must be 1..256 unique names")
    try:
        parsed_uuid = UUID(target_uuid)
    except (ValueError, TypeError, AttributeError) as exc:
        raise SourceCaptureSchemaError(
            "target_uuid must be a canonical nonzero UUID"
        ) from exc
    if str(parsed_uuid) != target_uuid or parsed_uuid.int == 0:
        raise SourceCaptureSchemaError("target_uuid must be a canonical nonzero UUID")
    tokens = _lex(source_create)
    depths = _nesting(tokens)
    database, table, source_uuid, i = _header(tokens)
    if (database or source_database, table) != (source_database, source_table):
        raise SourceCaptureSchemaError("source CREATE object differs from bound source")
    if source_uuid == target_uuid:
        raise SourceCaptureSchemaError("target UUID must differ from source UUID")
    opening = i
    closing = next(
        j for j in range(i + 1, len(tokens)) if tokens[j].text == ")" and depths[j] == 1
    )
    required = _columns(tokens, depths, opening + 1, closing, required_columns)
    i = closing + 1
    if [t.word or t.text for t in tokens[i : i + 2]] != ["ENGINE", "="]:
        raise SourceCaptureSchemaError("source CREATE requires explicit ENGINE")
    i += 2
    if i >= len(tokens) or tokens[i].text not in {
        "ReplacingMergeTree",
        "ReplicatedReplacingMergeTree",
    }:
        raise SourceCaptureSchemaError(
            "source engine must be ReplacingMergeTree or ReplicatedReplacingMergeTree"
        )
    i += 1
    if i < len(tokens) and tokens[i].text == "(":
        i = next(
            j + 1
            for j in range(i + 1, len(tokens))
            if tokens[j].text == ")" and depths[j] == 1
        )
    # Slice only top-level clauses. TTL may contain nested expressions, commas,
    # DELETE WHERE, GROUP BY SET, and quoted text that looks like other clauses.
    starts = []
    for j in range(i, len(tokens)):
        if depths[j] == 0 and tokens[j].word in {
            "PARTITION",
            "PRIMARY",
            "ORDER",
            "SAMPLE",
            "TTL",
            "SETTINGS",
            "COMMENT",
        }:
            starts.append(j)
    if not starts or starts[0] != i:
        raise SourceCaptureSchemaError("unsupported trailing CREATE metadata")
    clauses = {}
    for position, left in enumerate(starts):
        right = starts[position + 1] if position + 1 < len(starts) else len(tokens)
        name = tokens[left].word
        if name in clauses:
            raise SourceCaptureSchemaError("duplicate CREATE clause: " + name)
        if name in {"PARTITION", "ORDER", "SAMPLE", "PRIMARY"}:
            word = "KEY" if name == "PRIMARY" else "BY"
            if right - left < 3 or tokens[left + 1].word != word:
                raise SourceCaptureSchemaError("invalid CREATE clause: " + name)
        elif right - left < 2:
            raise SourceCaptureSchemaError("empty CREATE clause: " + name)
        if any(
            t.word
            in {
                "AS",
                "SELECT",
                "FORMAT",
                "INTO",
                "OUTFILE",
                "ON",
                "CREATE",
                "ATTACH",
                "INSERT",
            }
            for j, t in enumerate(tokens[left:right], left)
            if depths[j] == 0
        ):
            raise SourceCaptureSchemaError("unsupported trailing CREATE metadata")
        clauses[name] = (left, right)
    if "ORDER" not in clauses:
        raise SourceCaptureSchemaError("source CREATE lacks ORDER BY")
    if "COMMENT" in clauses:
        left, right = clauses["COMMENT"]
        if right - left != 2 or not tokens[left + 1].text.startswith("'"):
            raise SourceCaptureSchemaError("table COMMENT must be one string literal")
    settings, seen = [], set()
    if "SETTINGS" in clauses:
        left, right = clauses["SETTINGS"]
        for first, last in _split(tokens, depths, left + 1, right, 0):
            name = _identifier(tokens[first])
            if last - first < 3 or tokens[first + 1].text != "=" or name in seen:
                raise SourceCaptureSchemaError(
                    "invalid or duplicate table setting: " + name
                )
            seen.add(name)
            if name not in _MERGE_SETTINGS:
                settings.append(
                    source_create[tokens[first].start : tokens[last - 1].end]
                )
    settings.extend(name + " = 0" for name in _MERGE_SETTINGS)
    pieces = [
        f"CREATE TABLE `{target_database}`.`{target_table}` UUID '{target_uuid}'",
        source_create[tokens[opening].start : tokens[closing].end],
        "ENGINE = MergeTree",
    ]
    for name, (left, right) in clauses.items():
        if name not in {"TTL", "SETTINGS", "COMMENT"}:
            pieces.append(source_create[tokens[left].start : tokens[right - 1].end])
    # Newlines also terminate copied line comments before appended syntax.
    pieces.extend(
        ("SETTINGS " + ",\n".join(settings), "COMMENT '" + SOURCE_CAPTURE_COMMENT + "'")
    )
    create_sql = "\n".join(pieces)
    return SourceCaptureCreateSpec(
        create_sql=create_sql,
        source_sha256=normalized_schema_sha256(source_create),
        capture_sha256=normalized_schema_sha256(create_sql),
        required_stored_columns=required,
        target_database=target_database,
        target_table=target_table,
        target_uuid=target_uuid,
        source_database=source_database,
        source_table=source_table,
        source_uuid=source_uuid,
    )
