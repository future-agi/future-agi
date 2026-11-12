"""The framework-free webhooks core (DESIGN.md section 22.1).

Pure Python: the standard library only (``cryptography`` is allowed but not needed yet). Nothing here imports Django,
Temporal, ``tfc``, ``futureagi`` or anything outside ``webhooks.core``, reads settings, or does network I/O of its own;
the resolver, the socket and the store are injected through :mod:`webhooks.core.ports`. The Django app
(``futureagi/webhooks/``) only supplies adapters. ``.importlinter`` and ``tests/test_core_seam.py`` enforce the rule.

The core passes ``conformance/`` (conformance 1.4.0) on its own; ``conformance/tools`` restates the rules to build those
files and is never imported by a runtime module.
"""

CONFORMANCE_VERSION = "1.4.0"
