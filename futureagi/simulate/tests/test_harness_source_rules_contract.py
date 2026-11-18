"""How ALK reads a cited quote and a cited file (agent-learning-kit ``fi/alk/harness/leaks.py`` ``normalise`` and
``grounding.py`` ``_file``): the platform's one copy of each, for the cases ALK has not seen, reads them the same."""

import pytest

from simulate.services.harness_scenario_sources import file_path, normalise


@pytest.mark.parametrize(
    "quote, normalised",
    [
        ("Refunds over $500 need a MANAGER.", "refunds over 500 need a manager"),
        ("pay 2.50 now", "pay 2.50 now"),
        ("order_id - status", "order id status"),
        ("  many   spaces ", "many spaces"),
    ],
)
def test_a_quote_is_normalised_as_alk_does(quote, normalised):
    assert normalise(quote) == normalised


@pytest.mark.parametrize(
    "ref, path",
    [
        ("file:policy.md", "policy.md"),
        ("file: /knowledge/policy.md ", "knowledge/policy.md"),
        ("file:../secrets.md", ""),
        ("file:knowledge/a/../b.md", ""),
    ],
)
def test_a_cited_file_is_read_as_alk_reads_it(ref, path):
    assert file_path(ref) == path


@pytest.mark.parametrize(
    "verdict, resolved, problem",
    [
        (
            {"resolved": False, "problem": "the rule was reworded"},
            False,
            "the rule was reworded",
        ),
        # Null: ALK left it to the platform, which finds the rule.
        ({"resolved": None, "problem": ""}, True, ""),
        # A proof from a harness that writes no source results: the platform decides.
        (None, True, ""),
    ],
)
def test_alks_own_verdict_on_a_source_is_used_where_it_gave_one(
    verdict, resolved, problem
):
    from types import SimpleNamespace

    from simulate.services.harness_scenario_sources import resolve

    version = SimpleNamespace(
        documents={
            "contract": {"hard_constraints": ["Refunds over 500 need a manager"]}
        }
    )
    source = {
        "kind": "rule",
        "ref": "rule:0",
        "quote": "refunds over 500 need a manager",
    }
    proved = (
        {"case": [{"kind": "rule", "ref": "rule:0", **verdict}]} if verdict else None
    )

    view = resolve(
        SimpleNamespace(id=None), version, {"case": [source]}, proved=proved
    )["case"][0]

    assert (view["resolved"], view["problem"]) == (resolved, problem)
