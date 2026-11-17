"""A catalog miss is a ValueError subclass. Temporal matches a policy's
non-retryable types by the exact class name, so every policy that stops on a
ValueError names the catalog miss too; otherwise it would be retried."""

import importlib

from temporalio.common import RetryPolicy

STOPS_ON_A_VALUE_ERROR = {
    "simulate.temporal.retry_policies.DB_RETRY_POLICY",
    "simulate.temporal.retry_policies.PROVIDER_RETRY_POLICY",
    "tfc.temporal.agent_playground.workflows.NODE_EXECUTION_RETRY_POLICY",
    "tfc.temporal.experiments.workflows.ROW_PROCESSING_RETRY_POLICY",
    "tfc.temporal.simulate.workflows.LLM_RETRY_POLICY",
}


def value_error_policies():
    """Every retry policy in those modules that stops on a ValueError, by name."""
    found = {}
    for module_name in {name.rsplit(".", 1)[0] for name in STOPS_ON_A_VALUE_ERROR}:
        module = importlib.import_module(module_name)
        for attr, value in vars(module).items():
            if isinstance(value, RetryPolicy) and "ValueError" in (
                value.non_retryable_error_types or []
            ):
                found[f"{module_name}.{attr}"] = value
    assert set(found) == STOPS_ON_A_VALUE_ERROR
    return found


def test_every_policy_that_stops_on_a_value_error_stops_on_a_catalog_miss():
    for name, policy in value_error_policies().items():
        assert "ModelNotInCatalog" in policy.non_retryable_error_types, name
