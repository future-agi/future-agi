"""Fresh backend/worker probes on synthetic schema fixtures, not bootstrap proof."""

import json
import os
import subprocess
import sys
import time

import django

from agentcc.tests.hosted_rehearsal_support import Rehearsal


def main():
    django.setup()
    with Rehearsal() as rehearsal:
        for service in ("backend", "worker"):
            env = rehearsal.environment(
                SERVICE_TYPE=service,
                STARTUP_DB_MUTATION_MODE="disabled",
                AGENTCC_ADMIN_TOKEN="unsafe-synthetic-value",
            )
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "agentcc.tests.hosted_process_entry",
                    "readiness",
                ],
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
            )
            assert result.returncode != 0
            assert "unsafe-synthetic-value" not in result.stdout + result.stderr
            assert rehearsal.source not in result.stdout + result.stderr
        pending = rehearsal.child_probe(
            "readiness", SERVICE_TYPE="backend", STARTUP_DB_MUTATION_MODE="disabled"
        )
        assert '"reason": "database_or_schema"' in pending and "503" in pending
        rehearsal.fixture_state("0046_org_config_db_defaults")
        pending = rehearsal.child_probe("readiness")
        assert '"reason": "database_or_schema"' in pending and "503" in pending
        rehearsal.fixture_state(latest=True)
        assert '"status": "ready"' in rehearsal.child_probe("readiness")
        failed_redis = os.environ["REDIS_URL"].replace(
            "redis://", "redis://:synthetic-wrong-auth@"
        )
        started = time.monotonic()
        refused = rehearsal.child_probe("readiness", REDIS_URL=failed_redis)
        elapsed = time.monotonic() - started
        assert '"reason": "shared_state"' in refused and "503" in refused
        assert "synthetic-wrong-auth" not in refused
        assert (
            elapsed < 20
        )  # process startup plus bounded dependency probe, not SQL lock time
        assert '"status": "ready"' in rehearsal.child_probe("readiness")
        print(
            json.dumps(
                {
                    "source": rehearsal.revision,
                    "scope": "synthetic-backend-worker-processes",
                    "redis_failure_process_seconds": round(elapsed, 3),
                    "timings": rehearsal.timings,
                    "release_gates": "OWED",
                },
                sort_keys=True,
            )
        )


if __name__ == "__main__":
    main()
