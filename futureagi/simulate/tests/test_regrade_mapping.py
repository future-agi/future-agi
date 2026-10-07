"""Pure unit tests for `regrade_mapping` (no DB): the re-gradability rule.

`is_harness_run_test` needs the DB, so it is covered by the run-new-evals
endpoint tests and the run-test detail and list tests.
"""

from types import SimpleNamespace

import pytest

from simulate.services.harness_evals import regrade_mapping


def _config(mapping, *, owner="system", required_keys=("conversation", "agent_prompt")):
    return SimpleNamespace(
        mapping=mapping,
        eval_template=SimpleNamespace(
            owner=owner,
            config=None
            if required_keys is None
            else {"required_keys": list(required_keys)},
        ),
    )


@pytest.mark.unit
def test_a_config_with_its_own_mapping_is_graded_with_a_copy_of_it():
    mapping = {"text": "transcript"}
    config = _config(mapping, owner="user", required_keys=["text"])

    result = regrade_mapping(config)

    assert result == {"text": "transcript"}
    assert result is not mapping


@pytest.mark.unit
@pytest.mark.parametrize("stored", [{}, None])
@pytest.mark.parametrize(
    "required_keys, expected",
    [
        (["conversation"], {"conversation": "transcript"}),
        (["agent_prompt"], {"agent_prompt": "agent_prompt"}),
        (
            ["conversation", "agent_prompt"],
            {"conversation": "transcript", "agent_prompt": "agent_prompt"},
        ),
    ],
)
def test_a_built_in_suite_eval_gets_the_inputs_the_harness_gave_it(
    stored, required_keys, expected
):
    config = _config(stored, required_keys=required_keys)

    assert regrade_mapping(config) == expected


@pytest.mark.unit
@pytest.mark.parametrize(
    "owner, required_keys",
    [
        pytest.param("user", ["conversation"], id="per-scenario-claim"),
        pytest.param(
            "system", ["conversation", "expected_response"], id="asks-for-more"
        ),
        pytest.param("system", ["input"], id="asks-for-something-else"),
        pytest.param("system", [], id="asks-for-nothing"),
        pytest.param("system", None, id="no-template-config"),
    ],
)
def test_an_empty_mapping_config_only_the_harness_can_fill_is_not_regradable(
    owner, required_keys
):
    config = _config({}, owner=owner, required_keys=required_keys)

    assert regrade_mapping(config) is None
