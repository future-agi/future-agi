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

# Google fixture endpoints exist only in this E2E image. Production settings
# and accepted Google issuer policy are unchanged. The callback module imports
# these legacy constants directly from tfc.settings.settings.
from tfc.settings import settings as product_settings

product_settings.AUTH0_DOMAIN = AUTH0_DOMAIN = "mock-google:28443"
product_settings.AUTH0_CLIENT_ID = AUTH0_CLIENT_ID = "e2e-google-client"
product_settings.AUTH0_CLIENT_SECRET = AUTH0_CLIENT_SECRET = "e2e-google-secret"
GOOGLE_OAUTH_JWKS_URL = "https://mock-google:28443/certs"
