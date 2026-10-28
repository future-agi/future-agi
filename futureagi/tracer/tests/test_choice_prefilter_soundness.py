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


def _literal_candidates(cells):
    """Run the shipped literal-candidate predicate over (storage text, metadata).

    ``metadata`` is ``value_infos::text``: a JSON object, or a JSON string for
    historical cells. ``ascii_form`` is supplied as the reader supplies it.
    """
    import json

    from tracer.services.dataset_choice_values import (
        CHOICE_DOCUMENT_SQL,
        LITERAL_CANDIDATE_SQL,
    )

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT val, value_infos, "
            f"({LITERAL_CANDIDATE_SQL}) AS keep FROM ("
            "SELECT val, value_infos::text AS value_infos, ascii_form, "
            f"{CHOICE_DOCUMENT_SQL} AS document "
            "FROM (SELECT cell.val, cell.infos::jsonb AS value_infos, "
            "cell.ascii_form FROM unnest(%(values)s::text[], %(infos)s::text[], "
            "%(ascii_forms)s::text[]) AS cell(val, infos, ascii_form)) AS cells"
            ") AS cells",
            {
                "values": [value for value, _text in cells],
                "infos": [text for _value, text in cells],
                "ascii_forms": [json.dumps(value) for value, _text in cells],
            },
        )
        rows = cursor.fetchall()
    assert [value for value, _text, _keep in rows] == [value for value, _ in cells]
    return [keep is True for _value, _text, keep in rows]


def _escaped(value, escape, *, upper=False):
    """A JSON string body with a ``\\u`` escape for each character ``escape`` picks."""
    import json

    body = []
    for char in value:
        if not escape(char):
            body.append(json.dumps(char, ensure_ascii=False)[1:-1])
            continue
        units = char.encode("utf-16-be")
        for index in range(0, len(units), 2):
            unit = f"{int.from_bytes(units[index : index + 2], 'big'):04x}"
            body.append("\\u" + (unit.upper() if upper else unit))
    return "".join(body)


@pytest.mark.django_db
def test_every_literal_choice_cell_ships_its_metadata():
    """Run the shipped literal-candidate predicate against the real decoder.

    Only cells it keeps have their metadata read, so every (storage text,
    metadata) pair ``literal_choice`` accepts must survive it, however the
    producer escaped or encoded the JSON.
    """

    import json

    from tracer.services.dataset_choice_values import literal_choice

    def encoded(value, **options):
        return json.dumps(
            {"reason": "chose it", "output": "choices", "data": {"result": value}},
            **options,
        )

    values = [
        "[west]",
        "['west']",
        '["west"]',
        "{west}",
        " [west] ",
        "[a/b]",
        "[a\\b]",
        "[a\tb]",
        "[a\x01b]",
        "[a\x7fb]",
        "[\u96ea]",
        "[caf\u00e9]",
        "[caf\u00e9 \u96ea]",
        "[a\x7f\u00e9]",
        "[emoji \U0001f600]",
    ]
    stored = []
    for value in values:
        for options in ({}, {"ensure_ascii": False}):
            text = encoded(value, **options)
            stored += [(value, text), (value, json.dumps(text))]
        # Every legal spelling a producer may choose: \u escapes of any
        # character in either case, one accent escaped beside a literal one,
        # and \/.
        bodies = [
            _escaped(value, lambda char: True),
            _escaped(value, lambda char: True, upper=True),
            _escaped(value, lambda char: " " <= char <= "~"),
            _escaped(value, lambda char, top=max(value): "\x7f" <= char == top),
            value.replace("\\", "\\\\")
            .replace('"', '\\"')
            .replace("/", "\\/")
            .replace("\t", "\\t")
            .replace("\x01", "\\u0001"),
        ]
        stored += [
            (value, json.dumps('{"output":"choices","data":"' + body + '"}'))
            for body in bodies
        ]
    literal = [(value, text) for value, text in stored if literal_choice(value, text)]
    assert len(literal) >= len(values) * 8
    dropped = [
        cell
        for cell, keep in zip(literal, _literal_candidates(literal), strict=True)
        if not keep
    ]
    assert dropped == []


@pytest.mark.django_db
def test_container_cells_are_not_literal_candidates():
    """Metadata that never names the stored text as a JSON string ships nothing.

    The old predicate kept any storage with a slash, quote or accent, and any
    metadata holding a ``\\u00`` escape, so JSON-list storage and every cell whose
    reason had an accent (json.dumps escapes it) shipped ~1-3 KB each.
    """
    import json

    reason = "L\u2019\u00e9motion \u201cclaire\u201d \u2014 path/to a\\b"
    shapes = {
        "['anger', 'annoyance']": ["anger", "annoyance"],
        "['N/A']": ["N/A"],
        "['n\u00e9gatif']": ["n\u00e9gatif"],
        repr(["Doesn't answer"]): ["Doesn't answer"],
        '["Yes"]': ["Yes"],
        "{'choice': 'positive', 'score': 0.9}": {"result": "positive"},
    }
    cells = []
    for value, data in shapes.items():
        metadata = {"output": "choices", "data": data, "reason": reason}
        for ensure_ascii in (True, False):
            text = json.dumps(metadata, ensure_ascii=ensure_ascii)
            cells += [(value, text), (value, json.dumps(text))]
    assert not any(_literal_candidates(cells))
