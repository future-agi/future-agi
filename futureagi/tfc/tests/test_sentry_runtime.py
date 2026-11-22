"""Exercise the full runtime wiring without reconfiguring the suite's loggers."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

CASES = Path(__file__).with_name("sentry_runtime_cases.py")
BACKEND = CASES.parents[2]


@pytest.mark.parametrize(
    "case",
    [
        "registered_activity",
        "middleware_503",
        "supplied_frame",
        "authenticated_request",
        "request_span_overflow",
        pytest.param("structured_provider_log[django]", id="structured_provider_log"),
        pytest.param("structured_provider_log[local]", id="worker_vapi_console"),
        pytest.param("structured_provider_log[production]", id="worker_vapi_json"),
        pytest.param("embedding_rejection[django]", id="embedding_rejection"),
        pytest.param("embedding_rejection[local]", id="worker_embedding_console"),
        pytest.param("embedding_rejection[production]", id="worker_embedding_json"),
        "activity_scope_tags",
        "missing_stage_retryable",
        "worker_local_error",
    ],
)
def test_runtime_contract(case, tmp_path):
    # The production handler configuration and SDK integration setup both have
    # process-wide effects. Start each contract with test settings and no DSN;
    # the case itself binds the actual SDK to an in-memory transport.
    env = {k: os.environ[k] for k in ("PATH", "LANG") if k in os.environ}
    env.update(
        HOME=str(tmp_path),
        TMPDIR=str(tmp_path),
        PYTHONPATH=str(BACKEND),
        DJANGO_SETTINGS_MODULE="tfc.tests.sentry_runtime_settings",
        CI="true",
        ENV_TYPE="local",
        EE_LICENSE_KEY="",
        REGION="qa",
        APP_VERSION="qa",
        SENTRY_ENABLED="false",
        SENTRY_DSN="",
        SENTRY_LOG_LEVEL="ERROR",
        OTEL_SDK_DISABLED="true",
        FUTURE_AGI_TELEMETRY_DISABLED="true",
        HF_HUB_OFFLINE="1",
        TRANSFORMERS_OFFLINE="1",
        LITELLM_LOCAL_MODEL_COST_MAP="True",
        AWS_EC2_METADATA_DISABLED="true",
        PG_HOST="127.0.0.1",
        CH_HOST="127.0.0.1",
        CH25_HOST="127.0.0.1",
        REDIS_URL="redis://127.0.0.1:6379/0",
        REDIS_LOCK_URL="redis://127.0.0.1:6379/2",
    )
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "--assert=plain",
            "--show-capture=no",
            "-c",
            str(BACKEND / "pytest.ini"),
            "--confcutdir",
            str(CASES.parent),
            "-o",
            f"cache_dir={tmp_path / 'cache'}",
            f"{CASES}::test_{case}",
        ],
        cwd=tmp_path,
        env=env,
        check=True,
        timeout=330,
    )
