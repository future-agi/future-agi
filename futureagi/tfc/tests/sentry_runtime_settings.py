"""Test backing stores with the application's middleware and JSON log handlers."""

import os
from unittest.mock import patch

from tfc.logging.config import get_logging_config
from tfc.settings.test import *  # noqa: F403 - extend the test backing stores
from tfc.settings.settings import MIDDLEWARE as MIDDLEWARE  # isort: skip (override test middleware)

# Select the production renderer, not a production environment or datastore.
with patch.dict(os.environ, ENV_TYPE="production"):
    LOGGING = get_logging_config(os.environ["TMPDIR"])
