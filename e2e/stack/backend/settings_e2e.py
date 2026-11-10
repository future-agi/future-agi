"""Settings installed only into the E2E backend image."""

import os
from dataclasses import replace

from tfc.capabilities.registry import FEATURE_REGISTRY
from tfc.settings import settings as production_settings
from tfc.settings.settings import *  # noqa: F403

ROOT_URLCONF = "tfc.urls_e2e"

# This switch is intentionally unknown to production settings. The fake does not
# qualify a real provider or add a production allowlist entry.
if os.environ.get("E2E_SKILLS_FIXTURES") == "1":
    INSTALLED_APPS = [
        *production_settings.INSTALLED_APPS,
        "tfc.e2e_skills_transport.SkillsFixtureConfig",
    ]
    SKILLS_TESTING_ENABLED = True
    SKILLS_GITHUB_ENABLED = True
    SKILLS_VERIFIED_MODELS = ["gpt-4o-mini"]

    # J6: exercise purpose-bound reporting on the isolated managed stack.
    # This is not qualification of a production SDK, runtime or application.
    SKILLS_CONTEXT_CONSUMPTION_ENABLED = True

# The managed mock stack has no EE license. This test-image-only registry
# replacement keeps the product capability decision and image untouched.
FEATURE_REGISTRY["error_feed"] = replace(
    FEATURE_REGISTRY["error_feed"], oss_baseline=True
)
