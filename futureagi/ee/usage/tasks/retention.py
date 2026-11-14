"""Compatibility alias for the cloud data-retention tasks.

Aliasing the module instead of copying exports ensures patches through either
the legacy or canonical path affect the same module globals.
"""

from __future__ import annotations

import sys

from ee.cloud.tasks import retention as _retention

sys.modules[__name__] = _retention
