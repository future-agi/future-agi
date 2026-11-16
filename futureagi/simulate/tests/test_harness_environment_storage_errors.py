"""Reads that fall back when the environment's files cannot be read fall back only on storage errors, logged; any
other error is a bug and propagates."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from minio.error import S3Error

from simulate.services.harness_environment import call_actor_labels, edit_fault_targets
from simulate.tests.test_harness_tool_status import CONTRACT

JOB = SimpleNamespace(id="env-1")


def _storage_down(*args, **kwargs):
    raise S3Error("NoSuchKey", "gone", "r", "h", "i", None)


@pytest.mark.parametrize("read", [call_actor_labels, edit_fault_targets])
def test_storage_that_cannot_be_read_falls_back(read):
    with (
        patch("simulate.services.harness_versioning.drafts.edit_side", _storage_down),
        patch("simulate.services.harness_environment.stage_output", return_value=None),
        patch(
            "simulate.services.harness_environment.contract_data", return_value=CONTRACT
        ),
        patch("simulate.services.harness_environment.logger") as logger,
    ):
        read(JOB)

    logger.exception.assert_called_once()


@pytest.mark.parametrize("read", [call_actor_labels, edit_fault_targets])
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
        targets = edit_fault_targets(JOB)

    assert {one["tool"] for one in targets} >= {"issue_refund", "lookup_order"}
