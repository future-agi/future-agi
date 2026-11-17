"""Search on GET /entries?q= (SPEC §7.3, §9.2): only matching entries, whatever the query's shape."""

import pytest

from omega_hub.tests.flows import publish

pytestmark = pytest.mark.django_db


def refs_of(client, query):
    return [
        card["ref"]
        for card in client.get(f"/omega-hub/v1/entries{query}").json()["results"]
    ]


@pytest.fixture
def two(world):
    publish(world, "first-agent")
    publish(world, "notes-plugin")
    return world


def test_a_hyphenated_query_that_matches_nothing_finds_nothing(two):
    # websearch_to_tsquery turns a hyphenated word into a phrase; ts_rank is then above 0 for every row.
    assert refs_of(two.c, "?q=nonexistent-thing") == []
    assert refs_of(two.c, "?q=sync-helper") == []


def test_a_hyphenated_entry_name_finds_that_entry_only(two):
    assert refs_of(two.c, "?q=first-agent") == ["future-agi/first-agent"]
    assert refs_of(two.c, "?q=notes-plugin") == ["future-agi/notes-plugin"]


def test_plain_words_still_match_text_and_rank_the_matches(two):
    assert "future-agi/first-agent" in refs_of(two.c, "?q=weather")
    assert "future-agi/notes-plugin" not in refs_of(two.c, "?q=weather")
    assert refs_of(two.c, "?q=zzzunmatched") == []


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("weather", {"agent": 1}),  # full-text search
        ("first-agent", {"agent": 1}),  # name fallback / hyphenated query
        ("notes-plugin", {"plugin": 1}),
        ("future-agi", {"agent": 1, "plugin": 1}),  # publisher handle
        ("nonexistent-thing", {}),
        ("zzzunmatched", {}),
    ],
)
def test_search_facets_count_only_matches(two, query, expected):
    response = two.c.get("/omega-hub/v1/entries", {"q": query})
    assert response.status_code == 200, response.content
    body = response.json()
    kinds = body["facets"]["kinds"]
    assert {kind: count for kind, count in kinds.items() if count} == expected
    assert sum(kinds.values()) == len(body["results"])


@pytest.mark.parametrize("query", ["", "   ", "future-agi", "notes-plugin", "weather"])
def test_unranked_entries_count_only_when_they_match_a_nonblank_query(two, query):
    from omega_hub.models import Entry

    Entry.objects.filter(name="notes-plugin").update(ranked=False)
    body = two.c.get("/omega-hub/v1/entries", {"q": query}).json()
    expected_plugin = 1 if query in ("future-agi", "notes-plugin") else 0
    assert body["facets"]["kinds"]["plugin"] == expected_plugin
    assert sum(body["facets"]["kinds"].values()) == len(body["results"])


def test_kind_facet_ignores_selected_kind_but_keeps_the_search_predicate(two):
    body = two.c.get(
        "/omega-hub/v1/entries", {"q": "future-agi", "kind": "agent"}
    ).json()
    assert [card["ref"] for card in body["results"]] == ["future-agi/first-agent"]
    assert body["facets"]["kinds"]["agent"] == 1
    assert body["facets"]["kinds"]["plugin"] == 1
    none = two.c.get("/omega-hub/v1/entries", {"q": "weather", "kind": "plugin"}).json()
    assert none["results"] == []
    assert none["facets"]["kinds"]["agent"] == 1
    assert none["facets"]["kinds"]["plugin"] == 0
