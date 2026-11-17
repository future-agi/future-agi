"""A Results rerun identifies its saved variant, never a label or model match."""

import pytest

from model_hub.models.native_experiment_runs import NativeRunBinding
from model_hub.services.results_catalog import project_run
from model_hub.tests import test_native_prompt_create_bridge as fixtures

pytestmark = pytest.mark.django_db(transaction=True)
tenants = fixtures.tenants
native_fixture_evaluator = fixtures.native_fixture_evaluator


def test_prompt_run_projects_its_bound_saved_config(tenants):
    experiment, configs, _, _ = fixtures.subset_world(tenants["a"])
    bindings = NativeRunBinding.objects.filter(snapshot__experiment=experiment)
    assert bindings.count() == len(configs)
    for binding in bindings.select_related("run__dataset_version", "run__experiment"):
        result = project_run(binding.run, counts={}, progress={})
        assert result["prompt_config_id"] == str(binding.native_config_id)
        assert result["prompt_config_id"] in {str(config.id) for config in configs}
        assert "agent_config_id" not in result
