"""Settings installed only into the E2E backend image."""

from dataclasses import replace

from tfc.settings.settings import *  # noqa: F403
from tfc.capabilities.registry import FEATURE_REGISTRY

ROOT_URLCONF = "tfc.urls_e2e"

# The managed mock stack has no EE license. This test-image-only registry
# replacement keeps the product capability decision and image untouched.
FEATURE_REGISTRY["error_feed"] = replace(
    FEATURE_REGISTRY["error_feed"], oss_baseline=True
)
