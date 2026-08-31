"""Proving a store, without knowing which engine it is.

The build stage writes the engine-specific half: which image, how to read what it holds, how
to put it back. That half is written per agent, by a model, against an engine nobody vetted in
advance -- so the only thing standing between a subtly wrong reset and a suite of results that
mean nothing is this file.

Everything here is pure code and engine-independent. It never issues a query of its own,
because it cannot know the dialect; the one piece of engine-specific material it needs is a
``mutation`` -- any statement that changes something -- and even that is checked before it is
trusted, since a mutation that does nothing would make a broken restore look perfect.

The sharp one is ``ids do not drift``. Rows going back is easy and most wrong restores manage
it; what they miss is the counter behind the rows, so the next scenario's first insert gets an
id continuing from the last one. Rather than ask what a counter is called on this engine --
which is exactly the kind of thing we cannot know -- the same mutation is run twice from the
same starting point and the two results are compared. Any drift, in anything, shows up as a
difference.
"""

from __future__ import annotations

from typing import Any, Callable

from ..probe import ProbeReport, ProbeResult
from . import Store

STORE = "store"

# A check over a proven store: a sentence when something is wrong, None when it held.
Check = Callable[[Store], "str | None"]

# Whether the checks themselves can fail is asked elsewhere, by ``world/mutate.py``: it damages
# the whole world rather than only emptying the store, silences every tool as well, and runs each
# kind of damage against its own restored copy. Two gates asking the same question in different
# words is how one of them quietly stops being run, so there is deliberately only the one.


def _result(name: str, passed: bool, detail: str = "", kind: str = STORE) -> ProbeResult:
    return ProbeResult(name=name, kind=kind, passed=passed, detail=detail)


def prove_store(store: Store, mutation: str) -> ProbeReport:
    """Run a store through what it has to survive before any scenario is written against it.

    ``mutation`` is anything the engine accepts that changes what it holds -- one insert is
    plenty. It comes from the build stage because it is the one part of this that has to be
    written in the engine's own language.

    A failure here is ours, never the agent's. Nothing in this function involves the agent, so
    a report with anything red means the environment is not yet a thing worth measuring against.
    """
    report = ProbeReport()

    try:
        baseline = store.freeze()
    except Exception as exc:  # noqa: BLE001 - a store that cannot be frozen fails here
        report.results.append(_result("can be frozen", False, f"freeze raised: {exc}"))
        return report
    report.results.append(_result("can be frozen", True))

    # Migrations that did not run leave a store with nothing in it, and every check written
    # afterwards would pass or fail for reasons that have nothing to do with the agent.
    if not baseline.rows:
        report.results.append(
            _result(
                "holds a schema",
                False,
                "the store has no tables at all, so its migrations did not run",
            )
        )
        return report
    report.results.append(
        _result("holds a schema", True, f"{len(baseline.rows)} tables")
    )

    seeded = sum(len(rows) for rows in baseline.rows.values())
    report.results.append(
        _result(
            "holds a seed",
            seeded > 0,
            f"{seeded} rows" if seeded else "every table is empty, so nothing can be presumed",
        )
    )

    # -- the mutation has to be worth something before it can prove anything ------------
    try:
        store.apply(mutation)
    except Exception as exc:  # noqa: BLE001 - the caller's statement, reported as given
        report.results.append(
            _result("the mutation runs", False, f"{exc}")
        )
        return report
    report.results.append(_result("the mutation runs", True))

    mutated = store.state()
    if mutated == baseline.rows:
        report.results.append(
            _result(
                "the mutation moves it",
                False,
                "the store is unchanged after it, so it cannot prove a restore works",
            )
        )
        return report
    report.results.append(_result("the mutation moves it", True))

    # -- putting it back has to be exact -------------------------------------------------
    try:
        store.restore(baseline)
    except Exception as exc:  # noqa: BLE001
        report.results.append(_result("restore runs", False, f"restore raised: {exc}"))
        return report
    report.results.append(_result("restore runs", True))

    back = store.state()
    report.results.append(
        _result(
            "restore is exact",
            back == baseline.rows,
            "" if back == baseline.rows else _difference(baseline.rows, back),
        )
    )

    # -- and it has to put back what is behind the rows, not only the rows ---------------
    try:
        store.apply(mutation)
        again = store.state()
    except Exception as exc:  # noqa: BLE001
        report.results.append(_result("ids do not drift", False, f"{exc}"))
        return report

    report.results.append(
        _result(
            "ids do not drift",
            again == mutated,
            ""
            if again == mutated
            else (
                "the same change from the same starting point produced something different "
                "the second time, so the restore left a counter where it was: "
                + _difference(mutated, again)
            ),
        )
    )

    store.restore(baseline)
    report.results.append(
        _result("restore repeats", store.state() == baseline.rows)
    )
    return report


def _difference(expected: dict[str, Any], found: dict[str, Any]) -> str:
    """The first place two states disagree, said plainly.

    Whole-state diffs are unreadable at any real size, and the first disagreement is almost
    always the whole story.
    """
    for table in sorted(set(expected) | set(found)):
        before, after = expected.get(table), found.get(table)
        if before == after:
            continue
        if before is None:
            return f"{table} appeared"
        if after is None:
            return f"{table} disappeared"
        if len(before) != len(after):
            return f"{table}: {len(before)} rows expected, {len(after)} found"
        for index, (one, two) in enumerate(zip(before, after)):
            if one != two:
                return f"{table} row {index}: expected {one}, found {two}"
    return "no difference found, which should not happen"
