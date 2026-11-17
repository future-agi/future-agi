"""Key-operation arguments and environment-only inputs for the existing command."""

import os
import re

from django.core.management.base import CommandError

from agentcc.services.gateway_key_crypto import KeyTransition

KEY_PHASES = ("key-transition", "key-verify")


def add_key_arguments(parser):
    parser.add_argument("--key-batch-size", type=int, default=100)
    parser.add_argument("--key-max-batches", type=int, default=1000)
    parser.add_argument("--key-deadline-seconds", type=int, default=300)
    parser.add_argument("--key-lock-timeout-ms", type=int, default=2000)


def key_operation(options):
    """Validate before connecting. Even a plan/verify needs an approved quiet view."""
    if options["apply"] and options["phase"] == "key-verify":
        raise CommandError("Gateway key verification is read-only")
    if not options["quiesced"]:
        raise CommandError(
            "Assert approved gateway write/worker/schedule quiescence with --quiesced"
        )
    revision = os.environ.get("AGENTCC_HOSTED_RELEASE_REVISION", "")
    if (
        not re.fullmatch(r"[0-9a-f]{40}", revision)
        or options["compatible_runtime_revision"] != revision
    ):
        raise CommandError(
            "Gateway key transition requires the reviewed compatible runtime revision"
        )
    for name, low, high in (
        ("key_batch_size", 1, 500),
        ("key_max_batches", 1, 10000),
        ("key_deadline_seconds", 1, 3600),
        ("key_lock_timeout_ms", 1, 5000),
    ):
        if not low <= options[name] <= high:
            raise CommandError("Gateway transition bounds are invalid")
    source = os.environ.get("AGENTCC_GATEWAY_SOURCE_ENCRYPTION_KEY")
    target = os.environ.get("AGENTCC_GATEWAY_TARGET_ENCRYPTION_KEY")
    verify = options["phase"] == "key-verify"
    if not verify and not source:
        raise CommandError("Gateway transition requires a source key")
    crypto = KeyTransition(None if verify else source, target)
    # Runtime startup must use one of the declared keys. Verification is an
    # independent target-only process; it may not silently use the source key.
    runtime = os.environ.get("INTEGRATION_ENCRYPTION_KEY")
    if runtime not in ((target,) if verify else (source, target)):
        raise CommandError("Gateway runtime key must match the declared operation")
    return crypto, revision
