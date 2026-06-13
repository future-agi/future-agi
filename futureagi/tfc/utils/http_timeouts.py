"""Network timeout defaults for outbound HTTP calls.

Each constant is a ``(connect_timeout, read_timeout)`` tuple — the form
both ``requests`` and ``httpx`` accept. Explicit timeouts prevent worker
processes (Celery, Temporal, Granian request threads) from hanging
indefinitely on a slow or unresponsive remote endpoint. See #499.

Pick by call-site characteristics, not by service name:

- ``DEFAULT_HTTP_TIMEOUT``      — generic API call. Reach for this first.
- ``LLM_HTTP_TIMEOUT``          — synchronous LLM / TTS / STT generation.
                                  Read timeout must accommodate model
                                  warm-up and long completions.
- ``HEALTHCHECK_HTTP_TIMEOUT``  — liveness / credential / id-exists probe.
                                  Fail fast — these block user-facing flows.
- ``LINK_CHECK_HTTP_TIMEOUT``   — reachability probes on user-supplied URLs
                                  (``HEAD`` requests). Untrusted target;
                                  fail fast and never block.
"""

DEFAULT_HTTP_TIMEOUT: tuple[int, int] = (5, 30)
LLM_HTTP_TIMEOUT: tuple[int, int] = (10, 180)
HEALTHCHECK_HTTP_TIMEOUT: tuple[int, int] = (3, 10)
LINK_CHECK_HTTP_TIMEOUT: tuple[int, int] = (3, 10)
