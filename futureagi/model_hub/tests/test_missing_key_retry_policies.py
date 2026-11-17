"""A missing provider key is a ValueError subclass too. A run with no key for
its provider fails at once wherever a plain ValueError would, instead of being
retried by name."""

from model_hub.tests.test_catalog_miss_retry_policies import value_error_policies


def test_every_policy_that_stops_on_a_value_error_stops_on_a_missing_provider_key():
    for name, policy in value_error_policies().items():
        assert "MissingProviderKeyError" in policy.non_retryable_error_types, name
