"""Compatibility alias for the cloud billing engine.

The implementation is cloud-only and lives in
``ee.cloud.billing.billing_engine``. Keep the former module path importable
while callers migrate to the canonical location.
"""

from __future__ import annotations

import sys

from ee.cloud.billing import billing_engine as _billing_engine

sys.modules[__name__] = _billing_engine
