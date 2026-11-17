"""PostgreSQL lexical boundary before sqlparse sees any statement.

Comments nest; quoted data is opaque, never recursively parsed as SQL. This is
not a PostgreSQL grammar validator. Unsupported lexical forms fail closed, rather
than letting a second lexer guess where an executable statement starts.
"""

import re

import sqlparse
from sqlparse import tokens

WORD = re.compile(r"[^\W\d][\w$]*", re.UNICODE)
DOLLAR = re.compile(r"\$(?:[^\W\d][\w]*)?\$", re.UNICODE)

# Required deployment assumption, not a runtime parsing fallback: PostgreSQL's
# default since 9.1 is on, and FA does not override it. Ordinary strings escape
# quotes only by doubling them; backslashes are literal. E strings remain escaped.
ASSUMED_STANDARD_CONFORMING_STRINGS = True


def quoted_end(sql, start, quote, *, escapes=False):
    index = start + 1
    while index < len(sql):
        if escapes and sql[index] == "\\":
            index += 2
        elif sql[index] == quote:
            if sql[index : index + 2] == quote * 2:
                index += 2
            else:
                return index + 1
        else:
            index += 1
    raise ValueError("unterminated SQL quote")


def statements(sql):
    """One quote-aware pass; return comment-free statements and opaque literals.

    Validate the *whole* input before returning any statement, including a bad
    suffix after a valid read. Identifier spelling survives for name protection.
    """
    if not isinstance(sql, str) or "\x00" in sql:
        raise ValueError("opaque or NUL-containing SQL cannot be qualified")
    result, out, literals = [], [], {}
    index, depth = 0, 0

    def literal(value):
        marker = f"'__hard_erase_literal_{len(literals)}__'"
        literals[marker] = value
        out.append(marker)

    while index < len(sql):
        char = sql[index]
        if sql.startswith("--", index):
            index += 2
            while index < len(sql) and sql[index] not in "\r\n":
                index += 1
            out.append(" ")
        elif sql.startswith("/*", index):
            nesting = 1
            index += 2
            while index < len(sql) and nesting:
                if sql.startswith("/*", index):
                    nesting += 1
                    index += 2
                elif sql.startswith("*/", index):
                    nesting -= 1
                    index += 2
                else:
                    index += 1
            if nesting:
                raise ValueError("unterminated SQL comment")
            out.append(" ")
        elif sql.startswith("*/", index):
            raise ValueError("unmatched SQL comment close")
        elif char == '"':
            end = quoted_end(sql, index, char)
            out.append(sql[index:end])
            index = end
        elif char == "'":
            end = quoted_end(sql, index, char)
            literal(sql[index + 1 : end - 1].replace("''", "'"))
            index = end
        elif char == "$":
            match = DOLLAR.match(sql, index)
            if match:
                delimiter = match[0]
                end = sql.find(delimiter, match.end())
                if end < 0:
                    raise ValueError("unterminated SQL dollar quote")
                literal(None)
                index = end + len(delimiter)
            else:
                parameter = re.match(r"\$[0-9]+", sql[index:])
                if not parameter:
                    raise ValueError("unsupported SQL dollar construct")
                out.append(parameter[0])
                index += len(parameter[0])
        elif match := WORD.match(sql, index):
            word = match[0]
            index = match.end()
            if word.lower() == "e" and sql[index : index + 1] == "'":
                end = quoted_end(sql, index, "'", escapes=True)
                # E strings are opaque, not valid UESCAPE clauses here.
                literal(None)
                index = end
            elif word.lower() == "u" and sql[index : index + 2] == "&'":
                raise ValueError("unsupported Unicode SQL string")
            else:
                out.append(word)
        elif char == ";":
            if depth:
                raise ValueError("unsupported nested SQL statement separator")
            result.append("".join(out))
            out = []
            index += 1
        else:
            if char in "`\\" or (ord(char) < 32 and char not in "\t\n\r\f"):
                raise ValueError("unsupported SQL lexical construct")
            depth += (char == "(") - (char == ")")
            if depth < 0:
                raise ValueError("unmatched SQL parenthesis")
            out.append(char)
            index += 1
    if depth:
        raise ValueError("unterminated SQL parenthesis")
    result.append("".join(out))
    # Drop empty/comment-only segments only after validating the entire input;
    # a malformed suffix must never be hidden by an earlier complete statement.
    return [text for text in result if text.strip()], literals


def unicode_identifiers(out, literals):
    """Normalize U& identifiers, including PostgreSQL's optional UESCAPE."""
    normalized = []
    index = 0
    while index < len(out):
        if (
            out[index : index + 2] == [("u", False), ("&", False)]
            and index + 2 < len(out)
            and out[index + 2][1]
        ):
            value = out[index + 2][0]
            escape, consumed = "\\", 3
            if index + 3 < len(out) and out[index + 3] == ("uescape", False):
                escape = literals.get(index + 4)
                if (
                    not isinstance(escape, str)
                    or len(escape) != 1
                    or escape in "0123456789abcdefABCDEF+'\""
                    or escape.isspace()
                ):
                    raise ValueError("invalid Unicode identifier escape")
                consumed = 5
            value = re.sub(
                re.escape(escape)
                + r"(\+[0-9a-fA-F]{6}|[0-9a-fA-F]{4}|"
                + re.escape(escape)
                + ")",
                lambda match, escape=escape: (
                    escape if match[1] == escape else chr(int(match[1].lstrip("+"), 16))
                ),
                value,
            )
            normalized.append((value, True))
            index += consumed
        else:
            normalized.append(out[index])
            index += 1
    return normalized


def _lex(sql):
    clean, opaque = statements(sql)
    for text in clean:
        out, literals = [], {}
        # Lex only: sqlparse's grouping/statement heuristics are not a PostgreSQL
        # parser and must not undo our statement boundaries or inspect bodies.
        for kind, value in sqlparse.lexer.tokenize(text):
            if kind in tokens.Whitespace:
                continue
            if kind in tokens.Comment or kind in tokens.Error:
                raise ValueError("unsupported SQL token after lexical qualification")
            if kind in tokens.Literal.String.Single:
                if value not in opaque:
                    raise ValueError("unqualified SQL literal")
                literals[len(out)] = opaque[value]
                out.append(("<literal>", False))
            else:
                quoted = value.startswith('"') and value.endswith('"')
                out.append(
                    (
                        value[1:-1].replace('""', '"') if quoted else value.lower(),
                        quoted,
                    )
                )
        yield unicode_identifiers(out, literals)
