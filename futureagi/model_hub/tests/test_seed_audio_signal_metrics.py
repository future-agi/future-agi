import pytest
from django.core.cache import cache

from model_hub.management.commands.seed_system_evals import (
    SYSTEM_EVALS_VERSION,
    seed_evals,
)
from model_hub.models.evals_metric import EvalTemplate


@pytest.mark.django_db
@pytest.mark.parametrize("previous_version", [26, 27])
def test_audio_signal_metrics_seeds_after_existing_versions(previous_version):
    previous = cache.get("system_evals_version")
    cache.set("system_evals_version", previous_version, timeout=None)
    try:
        seed_evals()
        template = EvalTemplate.no_workspace_objects.get(
            name="audio_signal_metrics", owner="system", eval_id=219
        )
        assert template.visible_ui is True
        assert template.eval_type == "code"
        assert template.config["required_keys"] == ["input_audio"]
        assert cache.get("system_evals_version") == SYSTEM_EVALS_VERSION
        assert SYSTEM_EVALS_VERSION > previous_version
    finally:
        if previous is None:
            cache.delete("system_evals_version")
        else:
            cache.set("system_evals_version", previous, timeout=None)
