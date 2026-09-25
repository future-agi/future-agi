"""Live-PostgreSQL soundness proof for the eval-choice search pre-filter.

Its sibling ``test_dataset_choice_filter_values.py`` is offline by design and
denies sockets. This contract cannot be: whether the shipped SQL predicate is a
superset of the decoded match is a claim about PostgreSQL's own string
semantics, and a Python model of the predicate would assume exactly what needs
proving. So it runs against the test database.

The predicate itself is imported, never restated, so the offline module's
assertions and this proof cannot drift apart.
"""

import pytest
from django.db import connection

from tracer.tests.test_dataset_choice_filter_values import CHOICE_SEARCH_SUPERSET


@pytest.mark.django_db
def test_choice_prefilter_is_a_superset_of_the_decoded_match():
    """Run the shipped predicate in a real PostgreSQL against the real decoder.

    Soundness is a claim about PostgreSQL's own string semantics, so a Python
    model of the predicate would assume exactly what needs proving. Every cell
    the decoder-plus-search path would keep must survive the SQL predicate.
    """

    import json

    from tracer.services.dataset_choice_values import (
        InvalidChoiceCell,
        evaluation_choice_labels,
    )

    predicate = CHOICE_SEARCH_SUPERSET.strip()[len("AND ") :]
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

    violations = []
    for search in searches:
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT value, {predicate} AS keep, %(choice_search)s AS bound "
                "FROM unnest(%(cells)s::text[]) AS cell(value)",
                {"choice_search": search, "cells": cells},
            )
            rows = cursor.fetchall()
        kept = {}
        for value, keep, bound in rows:
            # Prove the needle survived parameter binding intact, or the whole
            # comparison would be against a different search.
            assert bound == search
            kept[value] = keep is True
        assert set(kept) == set(cells)
        violations += [
            (search, cell)
            for cell in cells
            if decoder_keeps(cell, search) and not kept[cell]
        ]
    assert violations == []
