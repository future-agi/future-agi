"""The combined schema keeps both accepted migration branches."""

import pytest
from django.db.migrations.loader import MigrationLoader


@pytest.mark.unit
def test_prompt_label_and_experiment_migrations_have_one_leaf():
    loader = MigrationLoader(None)
    leaf = (
        "model_hub",
        "0129_merge_prompt_label_heads_and_experiment_runs",
    )
    assert loader.graph.leaf_nodes("model_hub") == [leaf]
    plan = loader.graph.forwards_plan(leaf)
    assert ("model_hub", "0123_promptlabelhead") in plan
    assert ("model_hub", "0128_merge_keys_at_rest_and_experiment_runs") in plan
    assert ("model_hub", "0125_encrypt_model_hub_keys_at_rest") in plan
    assert ("model_hub", "0127_experimentrun_ordinal_datasetbaseline") in plan
    assert loader.disk_migrations[leaf].operations == []
