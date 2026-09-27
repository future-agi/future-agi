"""Live-ClickHouse soundness proof for the eval-choice search pre-filter.

Its sibling ``test_dataset_choice_filter_values.py`` is offline by design and
denies sockets. This contract cannot be: whether the shipped SQL predicate is a
superset of the decoded match is a claim about ClickHouse's own string
semantics, and a Python model of the predicate would assume exactly what needs
proving. So it lives here, gated on the same explicitly isolated test server the
other live catalog contracts use.

The predicate itself is imported, never restated, so the offline module's
assertions and this proof cannot drift apart.
"""

import json
import os

import pytest

from tracer.tests.test_dataset_choice_filter_values import CHOICE_SEARCH_SUPERSET


def test_choice_prefilter_is_a_superset_of_the_decoded_match():
    """Run the shipped predicate in a real ClickHouse against the real decoder.

    Soundness is a claim about ClickHouse's own string semantics, so a Python
    model of the predicate would assume exactly what needs proving. Every cell
    the decoder-plus-search path would keep must survive the SQL predicate.

    Gated on the same explicitly isolated test ClickHouse the other live
    contracts use, so a missing server is a hard error in a configured
    environment rather than a silent pass.
    """

    import clickhouse_connect

    from tracer.services.dataset_choice_values import (
        InvalidChoiceCell,
        evaluation_choice_labels,
    )

    host = os.environ.get("OBSERVED_CATALOG_TEST_CH_HOST")
    if not host:
        pytest.skip("requires an explicitly isolated observed catalog test ClickHouse")
    assert host in {"clickhouse", "127.0.0.1", "localhost"}
    client = clickhouse_connect.get_client(
        host=host,
        port=int(os.environ.get("OBSERVED_CATALOG_TEST_CH_PORT", "8123")),
        username=os.environ.get("OBSERVED_CATALOG_TEST_CH_USER", "test"),
        password=os.environ.get("OBSERVED_CATALOG_TEST_CH_PASSWORD", "test"),
    )

    predicate = CHOICE_SEARCH_SUPERSET.strip()[len("AND ") :].replace(
        "%(choice_search)s", "{s:String}"
    )
    labels = [
        "west",
        "East",
        "O'Reilly",
        "path\\name",
        'He said "\u96ea"',
        "\u96ea",
        "stra\u00dfe",
        "STRASSE",
        "\ufb01le",
        "\u0130stanbul",
        "caf\u00e9",
        "caf\u0065\u0301",
        "emoji \U0001f600",
        "a\nb",
        "[west]",
    ]
    cells = set()
    for label in labels:
        for ascii_only in (True, False):
            cells.add(
                json.dumps({"choice": label, "score": 0.2}, ensure_ascii=ascii_only)
            )
            cells.add(json.dumps([label, "other"], ensure_ascii=ascii_only))
        cells.add(repr({"score": 0.4, "choice": label}))
        cells.add(label)
    cells = sorted(cells)
    searches = [
        "we",
        "WEST",
        "o'r",
        "path\\",
        "name",
        "ss",
        "strasse",
        "fi",
        "file",
        "i",
        "istanbul",
        "cafe",
        "emoji",
        "0.2",
        "score",
        "other",
        "[",
    ]

    def decoder_keeps(cell, search):
        needle = search.casefold()
        found = []
        for literal in (False, True):
            try:
                found += evaluation_choice_labels(cell, literal=literal)
            except InvalidChoiceCell:
                pass
        return any(needle in str(label).casefold() for label in found)

    rows = "\n".join(json.dumps({"value": cell}) for cell in cells)
    source = "format(JSONEachRow, 'value String', $$" + rows + "$$)"
    assert "$$" not in rows
    violations = []
    for search in searches:
        result = client.query(
            f"SELECT value, toUInt8({predicate}) AS keep, "
            f"{{s:String}} AS bound FROM {source}",
            parameters={"s": search},
        )
        kept = {}
        for value, keep, bound in result.result_rows:
            # Prove the needle survived parameter binding intact, or the whole
            # comparison would be against a different search.
            assert bound == search
            kept[value] = keep == 1
        assert set(kept) == set(cells)
        violations += [
            (search, cell)
            for cell in cells
            if decoder_keeps(cell, search) and not kept[cell]
        ]
    assert violations == []
