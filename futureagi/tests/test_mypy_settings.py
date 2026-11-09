"""The configured Django typing plugin must see models, not an empty package."""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROBE = """
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from mypy_django_plugin.config import DjangoPluginConfig
from mypy_django_plugin.django.context import DjangoContext
config = DjangoPluginConfig(str(Path(sys.argv[1]) / "pyproject.toml"))
context = DjangoContext(config.django_settings_module)
version = context.get_model_class_by_fullname(
    "model_hub.models.skills_hub.HubSkillVersion"
)
assert version is not None, "The configured typing registry has no Skills models"
assert version._meta.get_field("skill").attname == "skill_id"
assert "objects" in version._meta.managers_map
assert context.get_model_class_by_fullname(
    "model_hub.models.skills_hub.NonexistentTypingProbe"
) is None
from typing import get_type_hints
from model_hub.services.skills_hub.v12 import campaigns
for function, name in [
    (campaigns._campaign, "HubSkillContextCampaign"),
    (campaigns._target, "HubSkillContextCampaignTarget"),
]:
    expected = context.get_model_class_by_fullname(
        "model_hub.models.skills_hub_v12." + name
    )
    assert expected is not None
    assert get_type_hints(function)["return"] is expected, (
        "Campaign selectors must expose their concrete model"
    )
print("MODEL_TYPING_REGISTRY_PASS")
"""


def test_mypy_config_loads_concrete_models():
    # A child process is essential: pytest already initialized its own Django
    # registry, which could conceal an empty settings package in the plugin.
    result = subprocess.run(
        [sys.executable, "-I", "-c", PROBE, str(ROOT)],
        cwd=ROOT,
        env={
            **os.environ,
            "NO_STARTUP_DB_MUTATIONS": "true",
            "FI_SKIP_CH25_MIGRATION": "1",
            "FUTURE_AGI_TELEMETRY_DISABLED": "true",
            "EE_LICENSE_KEY": "",
        },
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "MODEL_TYPING_REGISTRY_PASS" in result.stdout
