"""Offline regression tests for the SDK child environment trust boundary.

Run directly with ``python -I path/to/test_hosted_child_environment.py``.
Compile the production environment helpers without importing Django/Temporal or
loading settings/.env. The helpers see ONLY a synthetic environment, never the
operator's environment. No SDK, database, provider or network is used.
"""

from __future__ import annotations

import ast
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

_SOURCE = Path(__file__).resolve().parents[1] / "temporal/activities/hosted_runner.py"
_FUNCTIONS = {
    "_is_backend_only_secret",
    "_secret_env_ref_keys",
    "_child_environment",
    "_resolve_secret",
    "_secret_ref_origin",
    "_secret_source_allowed",
}


def _environment_helpers(worker_env: dict[str, str]) -> dict[str, Any]:
    tree = ast.parse(_SOURCE.read_text())
    nodes = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in _FUNCTIONS:
            nodes.append(node)
        elif isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name)
            and (
                target.id.startswith("_CHILD_ENV_")
                or target.id in {"_SINK_ENV_BY_PURPOSE", "_SINK_SOURCE_DESTINATIONS"}
            )
            for target in node.targets
        ):
            nodes.append(node)
    classifier = _SOURCE.parents[3] / "tfc/ee_loader.py"
    nodes.extend(
        node
        for node in ast.parse(classifier.read_text()).body
        if isinstance(node, ast.FunctionDef) and node.name == "is_cloud_env"
    )
    namespace = {
        "Any": Any,
        "os": SimpleNamespace(environ=worker_env, path=os.path),
        "settings": SimpleNamespace(CLOUD_DEPLOYMENT="US"),
        "CLOUD_DEPLOYMENTS": ("US", "EU", "DEV"),
        "_customer_provider_env_keys": lambda: {
            "VAPI_API_KEY",
            "RETELL_API_KEY",
            "VAPI_PUBLIC_API_KEY",
        },
    }
    exec(
        compile(ast.Module(body=nodes, type_ignores=[]), str(_SOURCE), "exec"),
        namespace,
    )
    return namespace


class HostedChildEnvironmentTests(unittest.TestCase):
    def setUp(self):
        for name in ("socket.socket", "socket.create_connection"):
            guard = patch(name, side_effect=AssertionError("sockets forbidden"))
            guard.start()
            self.addCleanup(guard.stop)

    def child_env(self, worker_env, job=None):
        return _environment_helpers(worker_env)["_child_environment"](job or {})

    def test_database_and_backend_credentials_are_not_inherited(self):
        names = {
            "PG_HOST",
            "PG_PORT",
            "PG_DB",
            "PG_USER",
            "PG_PASSWORD",
            "PG_READ_DB",
            "PG_DIRECT_HOST",
            "PG_DIRECT_PORT",
            "PG_READ_PASSWORD",
            "PGHOST",
            "PGPORT",
            "PGDATABASE",
            "PGUSER",
            "PGPASSWORD",
            "PGPASSFILE",
            "PGSERVICE",
            "PGSERVICEFILE",
            "PGSSLKEY",
            "PGSSLCERT",
            "INTERNAL_API_SECRET",
            "SECRET_KEY",
            "DJANGO_SECRET_KEY",
            "DATABASE_URL",
            "POSTGRES_PWD",
            "PGBOUNCER_HOST",
            "TEMPORAL_CLIENT_KEY",
            "CELERY_BROKER_URL",
            "RABBITMQ_DEFAULT_PASS",
            "REDIS_URL",
            "REDISCLI_AUTH",
            "CHANNEL_REDIS_URL",
            "CLICKHOUSE_PASSWORD",
            "CH_HOST",
            "CH_USER",
            "CH_PASSWORD",
            "CH25_PASSWORD",
            "FI_PG_READ",
            "FI_PG_WRITE",
            "FI_PG_READ_USER",
            "FI_CH_URL",
            "FI_AUTH_REDIS_ADDR",
            "FI_OBSERVED_CATALOG_CH_URL",
            "OMEGA_GATEWAY_RATE_LIMIT_REDIS_URL",
            "SIGNING_KEY",
            "HARNESS_PLATFORM_API_KEY",
            "PROPERTY_CATALOG_CH_USER",
            "SRC_PG_USER",
            "DST_CH_PASSWORD",
            "PEERDB_PASSWORD",
            "MINIO_ACCESS_KEY",
            "S3_ACCESS_KEY",
            "GCS_HMAC_ACCESS_KEY",
            "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY",
            "AWS_SESSION_TOKEN",
            "GCP_MARKETPLACE_SA_JSON",
            "INTEGRATION_ENCRYPTION_KEY",
            "AGENTCC_INTERNAL_API_KEY",
            "AGENTCC_HARNESS_API_KEY",
            "FUTUREAGI_CLOUD_API_KEY",
            "DAYTONA_API_KEY",
            "E2B_API_KEY",
            "MAILGUN_API_KEY",
            "EE_LICENSE_KEY",
            "DOCS_AGENT_API_KEY",
            "DEPLOYMENT_TELEMETRY_SLACK_WEBHOOK",
            "ERROR_LOGS_WEBHOOK",
            "SLACK_WEBHOOK_CHANNEL",
            "FI_API_SECRET_PEPPER",
            "FI_API_SECRET_PEPPERS_RETIRED",
            "UNRELATED_PASSWORD",
            "UNRELATED_SECRET",
            "UNRELATED_TOKEN",
            "UNRELATED_PRIVATE_KEY",
            "UNRELATED_CREDENTIALS",
            "UNRELATED_DSN",
            "PREFIX_PASSWORD_SUFFIX",
            "PREFIX_SECRET_SUFFIX",
            "PREFIX_TOKEN_SUFFIX",
            "PREFIX_PRIVATE_KEY_SUFFIX",
            "PREFIX_CREDENTIAL_SUFFIX",
            "PREFIX_DATABASE_URL_SUFFIX",
            "FI_BACKEND_TOKEN",
            "LIVEKIT_ADMIN_SECRET",
            "GOOGLE_OTHER_CREDENTIALS",
        }
        for name in sorted(names):
            for spelling in (name, name.lower(), name.title()):
                with self.subTest(name=spelling):
                    env = self.child_env({spelling: "synthetic-backend-only"})
                    self.assertNotIn(spelling, env)

    def test_required_runtime_environment_survives(self):
        names = {
            "PATH",
            "HOME",
            "LANG",
            "TMPDIR",
            "SSL_CERT_FILE",
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
            "GOOGLE_API_KEY",
            "GEMINI_API_KEY",
            "DEEPGRAM_API_KEY",
            "CARTESIA_API_KEY",
            "ELEVENLABS_API_KEY",
            "SIMULATOR_LLM_API_KEY",
            "SIMULATOR_LLM_MODEL",
            "SIMULATOR_STT_MODEL",
            "LIVEKIT_URL",
            "LIVEKIT_API_KEY",
            "LIVEKIT_API_SECRET",
            "VAPI_PHONE_NUMBER_ID",
            "VAPI_ASSISTANT_ID",
            "VAPI_API_BASE_URL",
            "RETELL_AGENT_ID",
            "RETELL_API_URL",
            "RETELL_LIVEKIT_URL",
            "GOOGLE_CLOUD_PROJECT",
            "GOOGLE_CLOUD_LOCATION",
            "FI_API_KEY",
            "FI_SECRET_KEY",
            "FI_INTERNAL_SUBMIT_SECRET",
            "FUTURE_AGI_SECRET_KEY",
            "AGENT_LEARNING_SECRET_KEY",
            "ALK_RUNNER_INTERNAL_SECRET",
            "FI_BASE_URL",
            "FI_RUN_ROOT",
            "FI_RUN_TEST_ID",
            "FI_TEST_EXECUTION_ID",
        }
        worker = dict.fromkeys(names, "synthetic-runtime")
        # Stricter than r1: unproven provider dependencies and sink aliases
        # are not inherited. The runner supplies job-owned FI_* fields instead.
        denied = {
            "ANTHROPIC_API_KEY",
            "LIVEKIT_URL",
            "FI_API_KEY",
            "FI_SECRET_KEY",
            "FI_INTERNAL_SUBMIT_SECRET",
            "FUTURE_AGI_SECRET_KEY",
            "AGENT_LEARNING_SECRET_KEY",
            "ALK_RUNNER_INTERNAL_SECRET",
            "FI_BASE_URL",
            "FI_RUN_ROOT",
            "FI_RUN_TEST_ID",
            "FI_TEST_EXECUTION_ID",
        }
        self.assertEqual(
            {name: value for name, value in worker.items() if name not in denied},
            self.child_env(worker),
        )

    def test_closed_allowlist_survives_and_unknown_names_do_not(self):
        names = frozenset(
            """
            PATH HOME LANG LC_ALL LC_CTYPE LC_COLLATE LC_MESSAGES LC_MONETARY
            LC_NUMERIC LC_TIME TZ TMPDIR PYTHONUNBUFFERED PYTHONIOENCODING
            PYTHONUTF8 PYTHONDONTWRITEBYTECODE SSL_CERT_FILE SSL_CERT_DIR
            REQUESTS_CA_BUNDLE HTTP_PROXY HTTPS_PROXY NO_PROXY
            http_proxy https_proxy no_proxy
            SIMULATOR_LLM_API_KEY SIMULATOR_LLM_BASE_URL OPENAI_API_KEY
            OPENAI_BASE_URL SIMULATOR_LLM_API_KEY_HEADER GOOGLE_CLOUD_PROJECT
            VERTEX_PROJECT GOOGLE_CLOUD_LOCATION VERTEX_LOCATION
            GOOGLE_APPLICATION_CREDENTIALS GEMINI_API_KEY GOOGLE_API_KEY
            SIMULATOR_LLM_THINKING SIMULATOR_LLM_FALLBACK_MODELS ELEVEN_API_KEY
            ELEVENLABS_API_KEY DEEPGRAM_API_KEY CARTESIA_API_KEY
            FI_HOSTED_DISPATCH_ACK ALK_VOICE_MAX_CASE_CONCURRENCY
            HARNESS_BACKGROUND_NOISE HARNESS_BACKGROUND_NOISE_VOLUME
            HARNESS_VOICEMAIL_CLIP HARNESS_VOICEMAIL_CLIP_TRANSCRIPT
            HARNESS_VOICEMAIL_CLIP_HAS_TONE HARNESS_CALLER_BARGE_IN_RATE
            HARNESS_CALL_DIRECTION SIMULATOR_VOICE_PROVIDER SIMULATOR_STT_MODEL
            SIMULATOR_TTS_MODEL SIMULATOR_TTS_VOICE_ID HARNESS_VOICEMAIL_STYLE
            HARNESS_ANSWERED_BY SIMULATOR_LLM_PROVIDER SIMULATOR_LLM_MODEL
            LIVEKIT_INBOUND_TRUNK_ID LIVEKIT_API_KEY LIVEKIT_API_SECRET
            VAPI_API_BASE_URL VAPI_ASSISTANT_ID VAPI_PHONE_NUMBER_ID
            RETELL_API_BASE_URL RETELL_AGENT_ID RETELL_FROM_NUMBER
            RETELL_API_URL RETELL_LIVEKIT_URL AGENTCC_API_KEY AGENTCC_BASE_URL
        """.split()
        )
        worker = dict.fromkeys(names, "/synthetic/runtime")
        helpers = _environment_helpers(worker)
        self.assertEqual(worker, helpers["_child_environment"]({}))
        for name in (
            "WEBHOOKS_ENCRYPTION_KEYS",
            "ALK_CAB_GUEST_POC_PIN",
            "PG_PASSWORD",
            "RANDOM_UNKNOWN_42",
            "ALK_UNSAFE_INPROCESS_CODE_ACTORS",
            "SIMULATOR_NEW_KEY",
            "HARNESS_NEW_KNOB",
            "PYTHONPATH",
            "PYTHONHOME",
            "LC_UNKNOWN",
            "VAPI_PUBLIC_API_KEY",
            "AGENTCC_INTERNAL_API_KEY",
            "AGENTCC_ADMIN_TOKEN",
            "AGENTCC_HARNESS_API_KEY",
            "AGENTCC_API_KEY_BACKUP",
            "AGENTCC_BASE_URL_BACKUP",
        ):
            for spelling in (name, name.lower(), name.title()):
                with self.subTest(name=spelling):
                    self.assertEqual({}, self.child_env({spelling: "synthetic"}))

        self.assertEqual(names, helpers.get("_CHILD_ENV_ALLOWLIST"))
        self.assertIsInstance(helpers.get("_CHILD_ENV_ALLOWLIST"), frozenset)

    def test_unsafe_override_is_absent_even_from_resolved_target_ref(self):
        key = "ALK_UNSAFE_INPROCESS_CODE_ACTORS"
        job = {
            "spec": {
                "target": {"secret_refs": {"auth": {"manager": "env", "key": key}}}
            }
        }
        self.assertNotIn(key, self.child_env({key: "true"}, job))

    def test_google_runtime_credential_path_stays_absolute(self):
        env = self.child_env({"GOOGLE_APPLICATION_CREDENTIALS": "synthetic-key.json"})
        self.assertEqual(
            env["GOOGLE_APPLICATION_CREDENTIALS"], os.path.abspath("synthetic-key.json")
        )

    def test_runtime_exceptions_are_exact_not_case_insensitive_or_prefix_based(self):
        for name in (
            "livekit_api_secret",
            "fi_secret_key",
            "google_application_credentials",
            "LIVEKIT_API_SECRET_BACKUP",
            "FI_SECRET_KEY_BACKUP",
            "GOOGLE_APPLICATION_CREDENTIALS_BACKUP",
        ):
            with self.subTest(name=name):
                self.assertNotIn(name, self.child_env({name: "synthetic-backend-only"}))

    def test_sink_and_target_refs_are_resolved_after_inheritance_filter(self):
        worker = {
            "INTERNAL_API_SECRET": "synthetic-internal",
            "SINK_API_TOKEN": "synthetic-api",
            "SINK_SECRET": "synthetic-sink-secret",
            "TARGET_TOKEN": "synthetic-target",
            "PG_PASSWORD": "synthetic-database",
        }
        refs = {
            purpose: {"manager": "env", "key": key, "purpose": purpose}
            for purpose, key in (
                ("internal_api_secret", "INTERNAL_API_SECRET"),
                ("api_key", "SINK_API_TOKEN"),
                ("secret_key", "SINK_SECRET"),
            )
        }
        job = {
            "sink": {
                "api_url": "https://sink.invalid",
                "run_test_id": "synthetic-run",
                "test_execution_id": "synthetic-execution",
                "secret_refs": refs,
            },
            "spec": {
                "target": {
                    "secret_refs": {"auth": {"manager": "env", "key": "TARGET_TOKEN"}}
                }
            },
        }
        env = self.child_env(worker, job)
        self.assertEqual(
            env,
            {
                "FI_INTERNAL_SUBMIT_SECRET": "synthetic-internal",
                "FI_API_KEY": "synthetic-api",
                "FI_SECRET_KEY": "synthetic-sink-secret",
                "FI_BASE_URL": "https://sink.invalid",
                "FI_RUN_TEST_ID": "synthetic-run",
                "FI_TEST_EXECUTION_ID": "synthetic-execution",
            },
        )
        self.assertEqual(worker["PG_PASSWORD"], "synthetic-database")

    def test_unresolved_job_refs_and_customer_provider_keys_cannot_fall_back(self):
        worker = dict.fromkeys(
            [
                "FI_SECRET_KEY",
                "LIVEKIT_API_SECRET",
                "OPENAI_API_KEY",
                "TARGET_TOKEN",
                "VAPI_API_KEY",
                "RETELL_API_KEY",
            ],
            "synthetic-worker",
        )

        def unresolved(name):
            return {"manager": "unresolved", "key": name}

        job = {
            "metadata": {"secret_env": [unresolved("LIVEKIT_API_SECRET")]},
            "sink": {"secret_refs": {"secret": unresolved("FI_SECRET_KEY")}},
            "spec": {
                "target": {
                    "secret_refs": {
                        "api": unresolved("OPENAI_API_KEY"),
                        "auth": unresolved("TARGET_TOKEN"),
                    }
                }
            },
        }
        self.assertEqual({}, self.child_env(worker, job))

    def test_lease_is_the_only_source_of_inbound_did(self):
        worker = {"LIVEKIT_INBOUND_DID": "synthetic-stale"}
        self.assertNotIn("LIVEKIT_INBOUND_DID", self.child_env(worker))
        env = self.child_env(worker, {"metadata": {"leased_did": "synthetic-leased"}})
        self.assertEqual(env["LIVEKIT_INBOUND_DID"], "synthetic-leased")

    def test_empty_and_null_job_sections_are_safe(self):
        self.assertEqual({}, self.child_env({}))
        self.assertEqual(
            {}, self.child_env({}, {"sink": None, "spec": None, "metadata": None})
        )


if __name__ == "__main__":
    unittest.main()
