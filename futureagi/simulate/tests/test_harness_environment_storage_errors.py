"""Reads that fall back when the environment's files cannot be read fall back only on storage errors, logged; any
other error is a bug and propagates."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from minio.error import S3Error

from simulate.services.harness_environment import call_actor_labels, edit_fault_side
from simulate.tests.test_harness_tool_status import CONTRACT

JOB = SimpleNamespace(id="env-1")


def _storage_down(*args, **kwargs):
    raise S3Error("NoSuchKey", "gone", "r", "h", "i", None)


def test_scenario_filter_names_fall_back_to_the_list_s_own_when_storage_cannot_be_read():
    listed = {"call_actors": [{"id": "friend", "name": "Rowan"}]}
    with (
        patch("simulate.services.harness_versioning.drafts.edit_side", _storage_down),
        patch(
            "simulate.services.harness_environment.stage_output", return_value=listed
        ),
    ):
        labels = call_actor_labels(JOB)

    assert labels["friend"] == "Rowan"


@pytest.mark.parametrize("read", [call_actor_labels, edit_fault_side])
def test_a_bug_is_not_read_as_a_storage_outage(read):
    with (
        patch(
            "simulate.services.harness_versioning.drafts.edit_side",
            side_effect=KeyError("typo"),
        ),
        patch("simulate.services.harness_environment.stage_output", return_value=None),
        patch(
            "simulate.services.harness_environment.contract_data", return_value=CONTRACT
        ),
        pytest.raises(KeyError),
    ):
        read(JOB)


def test_fault_targets_fall_back_to_the_environment_s_own_contract():
    with (
        patch("simulate.services.harness_versioning.drafts.edit_side", _storage_down),
        patch(
            "simulate.services.harness_environment.contract_data", return_value=CONTRACT
        ),
    ):
        targets, cast = edit_fault_side(JOB)

    assert {one["tool"] for one in targets} >= {"issue_refund", "lookup_order"}
    assert cast == []
