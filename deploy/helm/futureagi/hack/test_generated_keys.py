"""Offline key-custody render controls; never print rendered key material.

Run with python3 -I. Only chart fixtures and synthetic --set flags are used.
"""

import base64
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

CHART = Path(__file__).resolve().parent.parent
FAILURE = (
    "Refusing to generate application keys without cluster access: set "
    "secrets.existingSecret or use helm install/upgrade against the cluster."
)
UPGRADE_FAILURE = (
    "Refusing to replace missing application keys during upgrade: restore the "
    "release key Secret or set secrets.existingSecret to the recovered keys."
)
APP_KEYS = {
    "SECRET_KEY",
    "INTEGRATION_ENCRYPTION_KEY",
    "FI_API_SECRET_PEPPER",
    "AGENTCC_INTERNAL_API_KEY",
    "AGENTCC_ADMIN_TOKEN",
    "PROPERTY_CATALOG_API_PASSWORD",
    "PROPERTY_CATALOG_CONSUMER_PASSWORD",
    "AGENTCC_WEBHOOK_SECRET",
}


class GeneratedKeyRenderTests(unittest.TestCase):
    def render(self, *settings, chart=CHART, flags=()):
        cmd = [
            os.environ.get("HELM", "helm"),
            "template",
            "keyguard-test",
            str(chart),
            "--namespace",
            "keyguard-test",
            "-f",
            str(CHART / "examples/bundled.yaml"),
            "--set",
            "secrets.existingSecret=",
            *flags,
        ]
        for setting in settings:
            cmd.extend(["--set", setting])
        return subprocess.run(
            cmd, capture_output=True, text=True, timeout=30, check=False
        )

    def app_data(self, result):
        self.assertEqual(result.returncode, 0, "synthetic chart render failed")
        documents = result.stdout.split("---\n")
        secret = [
            doc
            for doc in documents
            if "# Source: futureagi/templates/secret.yaml\n" in doc
        ]
        self.assertEqual(len(secret), 1, "expected one chart Secret")
        # The chart emits base64 strings as JSON-compatible quoted scalars.
        return dict(re.findall(r'^  ([A-Z0-9_]+): "([A-Za-z0-9+/=]*)"$', secret[0], re.M))

    def test_offline_generation_is_refused(self):
        result = self.render()
        self.assertNotEqual(result.returncode, 0, "offline render generated keys")
        self.assertIn(FAILURE, result.stderr)
        self.assertFalse(result.stdout.strip(), "refusal emitted manifests")

    def test_existing_secret_emits_no_generated_keys(self):
        result = self.render(
            "secrets.existingSecret=synthetic-app-keys",
            "postgres.existingSecret=synthetic-postgres",
            "clickhouse.existingSecret=synthetic-clickhouse",
            "redis.existingSecret=synthetic-redis",
            "objectStorage.existingSecret=synthetic-storage",
        )
        data = self.app_data(result)
        self.assertFalse(data, "existing Secret path generated chart data")
        self.assertIn("synthetic-app-keys", result.stdout)
        self.assertNotIn(FAILURE, result.stderr)

    def test_ci_opt_out_generates_application_keys(self):
        data = self.app_data(self.render("secrets.allowGeneratedKeysWithoutCluster=true"))
        self.assertTrue(APP_KEYS.issubset(data), "generated application keys missing")
        self.assertTrue(all(data[key] for key in APP_KEYS), "empty generated key")
        fernet = base64.b64decode(data["INTEGRATION_ENCRYPTION_KEY"], validate=True)
        self.assertEqual(len(base64.urlsafe_b64decode(fernet)), 32)

    def test_hosted_still_requires_existing_secret(self):
        result = self.render(
            "hosted.enabled=true", "billing.enforcement=true",
            # Reach the shared application-key guard, not the provider-custody guard.
            "agentccGateway.providerCredentials.gatewayOnly=true",
            "agentccGateway.serviceAccount.create=true",
            "agentccGateway.providerCredentials.existingSecret=synthetic-provider-keys",
            "agentccGateway.providerCredentials.keys[0]=OPENAI_API_KEY",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("secrets.existingSecret", result.stderr)
        self.assertFalse(result.stdout.strip(), "hosted refusal emitted manifests")

    def test_hosted_opt_out_does_not_bypass_existing_secret(self):
        result = self.render(
            "hosted.enabled=true", "billing.enforcement=true",
            "agentccGateway.providerCredentials.gatewayOnly=true",
            "agentccGateway.serviceAccount.create=true",
            "agentccGateway.providerCredentials.existingSecret=synthetic-provider-keys",
            "agentccGateway.providerCredentials.keys[0]=OPENAI_API_KEY",
            "secrets.allowGeneratedKeysWithoutCluster=true",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("hosted requires secrets.existingSecret", result.stderr)

    def test_explicit_false_is_refused_on_install_and_upgrade_render(self):
        for flags in ((), ("--is-upgrade",)):
            with self.subTest(flags=flags):
                result = self.render("secrets.allowGeneratedKeysWithoutCluster=false", flags=flags)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(FAILURE, result.stderr)

    def test_opt_out_must_be_boolean(self):
        result = self.render(flags=("--set-string", "secrets.allowGeneratedKeysWithoutCluster=true"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("allowGeneratedKeysWithoutCluster", result.stderr)
        self.assertIn("boolean", result.stderr)

    def probe_chart(self, probe, named_secret=None):
        # Same technique as check.sh's live StatefulSet stand-in. This checks
        # template decisions, not Kubernetes connectivity or authorization.
        tmp = tempfile.TemporaryDirectory(prefix="keyguard-")
        self.addCleanup(tmp.cleanup)
        chart = Path(tmp.name) / "chart"
        shutil.copytree(CHART, chart)
        template = chart / "templates/secret.yaml"
        text = template.read_text()
        lookup = 'lookup "v1" "Secret" .Release.Namespace ""'
        self.assertEqual(text.count(lookup), 1, "update the probe stand-in")
        text = text.replace(lookup, probe)
        if named_secret is not None:
            lookup = 'lookup "v1" "Secret" .Release.Namespace $name'
            self.assertEqual(text.count(lookup), 1, "update the stored Secret stand-in")
            text = text.replace(lookup, named_secret)
        template.write_text(text)
        return chart

    def test_live_empty_list_allows_first_install(self):
        chart = self.probe_chart('(dict "apiVersion" "v1" "kind" "SecretList" "items" (list))')
        data = self.app_data(self.render(chart=chart))
        self.assertTrue(APP_KEYS.issubset(data))

    def test_offline_empty_map_standin_is_refused(self):
        chart = self.probe_chart('dict')
        result = self.render(chart=chart)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(FAILURE, result.stderr)

    def test_probe_error_fails_closed_but_existing_secret_skips_probe(self):
        chart = self.probe_chart('(fail "synthetic probe forbidden")')
        result = self.render(chart=chart)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("synthetic probe forbidden", result.stderr)
        result = self.render("secrets.existingSecret=synthetic-app-keys", chart=chart)
        self.app_data(result)

    def test_live_upgrade_keeps_saved_application_keys(self):
        chart = self.probe_chart('(dict "kind" "SecretList" "items" (list))')
        template = chart / "templates/secret.yaml"
        text = template.read_text()
        lookup = 'lookup "v1" "Secret" .Release.Namespace $name'
        self.assertEqual(text.count(lookup), 1, "update the stored Secret stand-in")
        # Deliberately synthetic base64 data, not a deployable credential.
        saved = base64.b64encode(b"synthetic-stored-key").decode()
        entries = " ".join(f'"{key}" "{saved}"' for key in sorted(APP_KEYS))
        template.write_text(text.replace(lookup, f'(dict "data" (dict {entries}))'))
        first = self.app_data(self.render(chart=chart, flags=("--is-upgrade",)))
        again = self.app_data(self.render(chart=chart, flags=("--is-upgrade",)))
        self.assertTrue(all(first[key] == saved == again[key] for key in APP_KEYS))

    def test_saved_secret_upgrade_never_lists_secrets(self):
        saved = base64.b64encode(b"synthetic-stored-key").decode()
        entries = " ".join(f'"{key}" "{saved}"' for key in sorted(APP_KEYS))
        for probe in ('(fail "synthetic list forbidden")', 'dict'):
            with self.subTest(probe=probe):
                chart = self.probe_chart(probe, f'(dict "data" (dict {entries}))')
                # --is-upgrade plus lookup stand-ins models the render decision
                # for a connected upgrade/server dry-run, not an API request.
                for _ in range(2):
                    data = self.app_data(self.render(chart=chart, flags=("--is-upgrade",)))
                    self.assertTrue(all(data.get(key) == saved for key in APP_KEYS))

    def test_connected_upgrade_missing_secret_refuses_even_with_ci_opt_out(self):
        chart = self.probe_chart('(dict "kind" "SecretList" "items" (list))', 'dict')
        for opt_out in ("false", "true"):
            with self.subTest(opt_out=opt_out):
                result = self.render(
                    f"secrets.allowGeneratedKeysWithoutCluster={opt_out}",
                    chart=chart, flags=("--is-upgrade",),
                )
                self.assertNotEqual(result.returncode, 0, "upgrade minted replacement keys")
                self.assertIn(UPGRADE_FAILURE, result.stderr)
                self.assertFalse(result.stdout.strip(), "refusal emitted manifests")
                self.assertNotIn(FAILURE, result.stderr)

    def test_failed_hook_retry_refuses_until_saved_secret_is_restored(self):
        # Source-level fault schedule only: Helm rendered the saved hook, then
        # before-hook-creation deleted the Secret and recreation failed. Model
        # the resulting lookup states; no cluster or actual rollback is run.
        saved = base64.b64encode(b"synthetic-history-key").decode()
        entries = " ".join(f'"{key}" "{saved}"' for key in sorted(APP_KEYS))
        live = '(dict "kind" "SecretList" "items" (list))'
        present = self.probe_chart(live, f'(dict "data" (dict {entries}))')
        before = self.app_data(self.render(chart=present, flags=("--is-upgrade",)))
        missing = self.probe_chart(live, 'dict')
        for _ in range(2):
            retry = self.render(chart=missing, flags=("--is-upgrade",))
            self.assertNotEqual(retry.returncode, 0, "retry replaced lost keys")
            self.assertIn(UPGRADE_FAILURE, retry.stderr)
            self.assertFalse(retry.stdout.strip(), "retry refusal emitted manifests")
        # Restoration from backup/retained history makes the named GET return
        # the original data again. All application keys, not just Fernet, match.
        restored = self.app_data(self.render(chart=present, flags=("--is-upgrade",)))
        self.assertTrue(all(before[key] == saved == restored[key] for key in APP_KEYS))

    def test_offline_upgrade_ci_opt_out_is_unchanged(self):
        data = self.app_data(self.render(
            "secrets.allowGeneratedKeysWithoutCluster=true", flags=("--is-upgrade",),
        ))
        self.assertTrue(APP_KEYS.issubset(data))

    def test_existing_secret_upgrade_skips_missing_key_guard_and_list(self):
        chart = self.probe_chart('(fail "synthetic list forbidden")', 'dict')
        result = self.render(
            "secrets.existingSecret=synthetic-recovered-keys",
            chart=chart, flags=("--is-upgrade",),
        )
        data = self.app_data(result)
        self.assertFalse(APP_KEYS.intersection(data), "existing Secret generated application keys")
        self.assertNotIn(UPGRADE_FAILURE, result.stderr)

    def test_named_lookup_error_is_not_treated_as_missing_secret(self):
        chart = self.probe_chart(
            '(dict "kind" "SecretList" "items" (list))',
            '(fail "synthetic named get forbidden")',
        )
        result = self.render(chart=chart, flags=("--is-upgrade",))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("synthetic named get forbidden", result.stderr)
        self.assertFalse(result.stdout.strip(), "lookup error emitted manifests")


if __name__ == "__main__":
    unittest.main()
