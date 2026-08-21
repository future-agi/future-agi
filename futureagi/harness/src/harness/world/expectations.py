"""What a world is expected to look like afterwards, and whether it does.

Written once and used twice. The build stage declares a sequence and asserts the state it leaves
behind; a scenario declares the state a conversation should leave behind. Those are the same
question asked at two different scales, and if each had its own implementation they would drift
until a check that passes the gate fails the run for reasons that have nothing to do with the
agent.

The shape is ``{"table.count": 3, "table.column": "value"}``: how many records there are, and
whether a particular value is among them.
"""

from __future__ import annotations

from typing import Any, Mapping

COUNT = "count"


def check_state(
    state: Mapping[str, list[dict[str, Any]]], expected: Mapping[str, Any]
) -> list[str]:
    """Every expectation that does not hold, said in terms of what was found instead."""
    failures: list[str] = []
    for path, want in (expected or {}).items():
        table, _, column = str(path).partition(".")
        if table not in state:
            failures.append(
                f"{path}: no {table} in this world; it has "
                f"{', '.join(sorted(state)) or 'nothing'}"
            )
            continue
        rows = state[table]
        if column in ("", COUNT):
            if len(rows) != want:
                failures.append(f"{path}: {len(rows)} rows, expected {want}")
            continue
        if rows and column not in rows[0]:
            failures.append(
                f"{path}: {table} has no {column}; its columns are "
                f"{', '.join(sorted(rows[0]))}"
            )
            continue
        present = {str(row.get(column)) for row in rows}
        # A list means every one of these has to be somewhere, which is how an expectation about
        # a basket of several items is naturally written. Compared as a single value it could
        # never hold, and an expectation that cannot hold grades nothing while appearing to.
        wanted = list(want) if isinstance(want, (list, tuple)) else [want]
        absent = [value for value in wanted if str(value) not in present]
        if absent:
            found = ", ".join(sorted(present)[:6]) or "nothing"
            failures.append(
                f"{path}: no row has {column}="
                + " or ".join(repr(value) for value in absent)
                + f"; found {found}"
            )
    return failures


def unresolvable(
    state: Mapping[str, list[dict[str, Any]]], expected: Mapping[str, Any]
) -> list[str]:
    """Expectations that name a table or column the world does not have.

    Separate from whether they hold, because they are a different kind of wrong. An expectation
    that fails is a finding about the agent; one that names a table nobody built is a finding
    about the expectation, and letting it through means grading a run against a typo.
    """
    problems: list[str] = []
    for path in expected or {}:
        table, _, column = str(path).partition(".")
        if table not in state:
            # Indexing a particular row is the most common way to write an expectation this
            # cannot carry, and saying only "no such table" sends the reader looking for a
            # spelling mistake instead of at the shape.
            indexed = "[" in table
            problems.append(
                f"{path}: no table called {table!r}"
                + (
                    ". Expectations are about the whole table, not one row: use "
                    "'table.count' for how many, or 'table.column' for a value that has to "
                    "appear in some row."
                    if indexed
                    else ""
                )
            )
        elif (
            column not in ("", COUNT) and state[table] and column not in state[table][0]
        ):
            problems.append(f"{path}: {table} has no column {column!r}")
    return problems
